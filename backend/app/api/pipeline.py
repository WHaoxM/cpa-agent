"""
Pipeline API - L0-L4 管线调用接口
整合 mvp/entity_extractor 的 NS-LE Pipeline

与 pipeline_ext.py 的区别:
- pipeline_ext.py: 同步直接调用，提供 /extract、/match、/portrait 等细粒度端点
- 本模块: L3 专用入口有持久派发；通用 trigger 和其他旧启动入口仍不可用
"""

import os
import uuid
import json
from datetime import datetime
from typing import Optional

from flask import Blueprint, request, jsonify

from ._helpers import ScopeError, parse_json_field, resolve_request_scope, safe_error, scope_error_response
from ..services.db import get_mysql_client
from ..utils.logger import get_logger
from ..validators import validate_student_id, validate_pipeline_name
from ..config import Config

logger = get_logger('cpa-agent.api.pipeline')

pipeline_bp = Blueprint('pipeline', __name__)


def _dispatch_unavailable():
    """Do not persist an unconsumable pending task as if it were accepted."""
    return jsonify({
        'success': False,
        'code': 'pipeline_dispatch_unavailable',
        'error': '该管线入口尚未接入执行器，未创建任务',
        'retryable': False,
    }), 503


@pipeline_bp.route('/entity-review', methods=['GET'])
def get_entity_review():
    """Current confirmed resume's model-link proposals and user review journal."""
    try:
        scope = resolve_request_scope()
        from ..services.resume_entity_review import read_entity_review
        data = read_entity_review(get_mysql_client(), student_id=scope.student_id)
        return jsonify({'success': True, 'data': data})
    except ScopeError as e:
        return scope_error_response(e)
    except LookupError:
        return jsonify({'success': False, 'error': '当前用户没有已确认简历'}), 404
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        return jsonify(safe_error('读取实体审阅', e, message='实体审阅暂不可用，请检查迁移和数据库')), 503


@pipeline_bp.route('/entity-review', methods=['POST'])
def post_entity_review():
    """Append a versioned review. No profile or graph mutation occurs here."""
    try:
        scope = resolve_request_scope()
        from ..services.resume_entity_review import ReviewConflict, write_entity_review
        data = write_entity_review(get_mysql_client(), student_id=scope.student_id,
                                   payload=request.get_json(silent=True))
        return jsonify({'success': True, 'data': data})
    except ScopeError as e:
        return scope_error_response(e)
    except LookupError:
        return jsonify({'success': False, 'error': '当前用户没有已确认简历'}), 404
    except ReviewConflict as e:
        return jsonify({'success': False, 'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify(safe_error('保存实体审阅', e, message='实体审阅未确认保存，请刷新重试')), 503


def _get_mvp_pipeline():
    """获取 mvp Pipeline"""
    try:
        import sys
        mvp_path = os.path.join(os.path.dirname(__file__), '..', 'pipeline', 'entity_extractor')
        if mvp_path not in sys.path:
            sys.path.insert(0, mvp_path)

        from entity_extractor.nsle_pipeline import NSLEPipeline
        return NSLEPipeline
    except ImportError as e:
        logger.error(f"MVP Pipeline 导入失败: {e}")
        return None


def _create_task(pipeline: str, params: dict, mysql) -> str:
    """创建管线任务记录"""
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    sql = """
    INSERT INTO pipeline_task
    (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
    VALUES (:task_id, :pipeline, :status, :params_json, :student_id, :job_id, :triggered_by, :created_at)
    """

    mysql.execute_write(
        sql,
        {
            "task_id": task_id,
            "pipeline": pipeline,
            "status": "pending",
            "params_json": json.dumps(params),
            "student_id": params.get("student_id"),
            "job_id": params.get("job_id"),
            "triggered_by": "api",
            "created_at": datetime.now()
        }
    )

    return task_id


def _extract_demo_skills(text: str) -> list[str]:
    """Extract a small deterministic skill list for runnable demo tasks."""
    candidates = [
        "Python", "Java", "Spring Boot", "MySQL", "Redis", "Docker",
        "RAG", "LangChain", "Prompt Engineering", "Vue", "React",
        "TypeScript", "机器学习", "数据分析",
    ]
    lowered = text.lower()
    return [skill for skill in candidates if skill.lower() in lowered or skill in text]


def _build_demo_result(pipeline: str, params: dict) -> dict:
    resume_text = params.get("resume_text") or params.get("text") or ""
    target_role = params.get("target_role") or params.get("job_id") or params.get("job_portrait_id")

    base = {
        "source": "demo_fallback",
        "pipeline": pipeline,
        "message": "Demo preview only; no domain artifact was persisted.",
    }

    if pipeline == "L0_parse_resume":
        skills = _extract_demo_skills(str(resume_text))
        return {
            **base,
            "summary": {
                "block_count": 1 if resume_text else 0,
                "detected_skills": skills,
                "target_role": target_role,
            },
            "next_steps": ["L1_build_graph", "L2_generate_portrait"],
        }

    if pipeline == "L1_build_graph":
        return {**base, "summary": {"nodes_created": 0, "edges_created": 0}, "next_steps": ["L2_generate_portrait"]}

    if pipeline == "L2_generate_portrait":
        return {**base, "summary": {"portrait_dimensions": ["D1", "D2", "D3", "D4"], "target_role": target_role}, "next_steps": ["L3_run_match"]}

    if pipeline == "L3_run_match":
        return {**base, "summary": {"match_score": 0.78, "passed_hard_constraints": True}, "next_steps": ["L4_generate_report"]}

    if pipeline == "L4_generate_report":
        return {**base, "summary": {"sections": 6, "status": "report_outline_ready"}, "next_steps": []}

    return base


def _complete_demo_task(task_id: str, pipeline: str, params: dict, mysql) -> dict:
    result = _build_demo_result(pipeline, params)
    mysql.execute_write(
        """
        UPDATE pipeline_task
        SET status='partial', progress=100, result_json=:result, error_message=NULL
        WHERE task_id=:task_id
        """,
        {
            "task_id": task_id,
            "result": json.dumps(result, ensure_ascii=False),
        },
    )
    return result


@pipeline_bp.route('/list', methods=['GET'])
def list_pipelines():
    """列出所有管线"""
    pipelines = [
        {'name': 'L0_parse_resume', 'description': '简历解析 (MinerU + YOLOv10 + Qwen3-SFT)'},
        {'name': 'L1_build_graph', 'description': '知识图谱构建 (技能骨架 + 岗位聚合 + 路径图谱)'},
        {'name': 'L2_generate_portrait', 'description': '画像生成 (学生四维画像 + 岗位四维画像)'},
        {'name': 'L3_run_match', 'description': '人岗匹配 (D1硬约束 + D2技能覆盖 + D3/D4加权)'},
        {'name': 'L4_generate_report', 'description': '报告生成 (6章结构, 模板70% + LLM30%)'},
    ]
    for item in pipelines:
        item['trigger_mode'] = ('demo_preview' if Config.DEMO_MODE else
                                'dedicated_async' if item['name'] == 'L3_run_match' else 'unavailable')
        if item['trigger_mode'] == 'dedicated_async':
            item['start_path'] = '/api/pipeline/l3/run-match'

    return jsonify({
        'success': True,
        'data': pipelines
    })


@pipeline_bp.route('/trigger', methods=['POST'])
def trigger_pipeline():
    """触发管线任务"""
    data = request.get_json(silent=True)
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
    data["student_id"] = scope.student_id

    pipeline = data.get('pipeline', '')
    student_id = scope.student_id

    ok, msg = validate_pipeline_name(pipeline)
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    if student_id:
        ok, msg = validate_student_id(student_id)
        if not ok:
            return jsonify({'success': False, 'error': msg}), 400

    if not Config.DEMO_MODE:
        return _dispatch_unavailable()

    mysql = get_mysql_client()
    task_id = _create_task(pipeline, data, mysql)
    status = "pending"
    result = None

    if Config.DEMO_MODE:
        result = _complete_demo_task(task_id, pipeline, data, mysql)
        status = "partial"

    logger.info(f"管线触发: task_id={task_id}, pipeline={pipeline}")

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'pipeline': pipeline,
            'status': status,
            'progress': 100 if status == "partial" else 0,
            'result': result,
        }
    })


@pipeline_bp.route('/status/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    """获取任务状态"""
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    mysql = get_mysql_client()

    sql = """
    SELECT task_id, pipeline, status, progress, result_json, error_message, created_at
    FROM pipeline_task WHERE task_id = :task_id AND student_id = :student_id
    """
    result = mysql.execute(sql, {"task_id": task_id, "student_id": scope.student_id})

    if not result:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    task = result[0]

    result_json = parse_json_field(task.get('result_json'))

    return jsonify({
        'success': True,
        'data': {
            'task_id': task['task_id'],
            'pipeline': task['pipeline'],
            'status': task['status'],
            'progress': task.get('progress', 0),
            'result': result_json,
            'error': task.get('error_message'),
            'created_at': task['created_at'].isoformat() if task.get('created_at') else None
        }
    })


@pipeline_bp.route('/events/<task_id>', methods=['GET'])
def get_task_events(task_id: str):
    """Replay committed business transitions, not estimated progress."""
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    try:
        cursor = int(request.args.get('cursor', '0'))
        limit = int(request.args.get('limit', '100'))
        if cursor < 0 or not 1 <= limit <= 200:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'cursor/limit 无效'}), 400
    from ..services.pipeline_events import read_pipeline_events
    try:
        data = read_pipeline_events(get_mysql_client(), student_id=scope.student_id,
                                    task_id=task_id, cursor=cursor, limit=limit)
        return jsonify({'success': True, 'data': data})
    except LookupError:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    except Exception as e:
        return jsonify(safe_error('Pipeline events', e, message='任务事件暂不可用，请检查迁移和数据库')), 503


@pipeline_bp.route('/trace/<task_id>', methods=['GET'])
def get_resume_trace(task_id: str):
    """Owner-scoped replay of persisted L0 checkpoints and data handoffs."""
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    try:
        cursor = int(request.args.get('cursor', '0'))
        limit = int(request.args.get('limit', '100'))
        if cursor < 0 or not 1 <= limit <= 200:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'cursor/limit 无效'}), 400
    from ..services.pipeline_resume_jobs import read_resume_trace
    try:
        data = read_resume_trace(get_mysql_client(), student_id=scope.student_id,
                                 task_id=task_id, cursor=cursor, limit=limit)
        return jsonify({'success': True, 'data': data})
    except LookupError:
        return jsonify({'success': False, 'error': '简历任务不存在'}), 404
    except Exception as e:
        return jsonify(safe_error('Pipeline trace', e, message='解析轨迹暂不可用，请检查迁移和数据库')), 503


@pipeline_bp.route('/l0/parse-resume', methods=['POST'])
def parse_resume():
    """L0: 简历解析"""
    data = request.get_json() or {}
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)

    file_path = data.get('file_path')
    student_id = scope.student_id

    # This legacy route accepts paths; only the Principal's upload directory is readable.
    if file_path:
        owner_root = os.path.realpath(os.path.join(Config.UPLOAD_FOLDER, student_id))
        resolved = os.path.realpath(str(file_path))
        try:
            owned = os.path.commonpath((owner_root, resolved)) == owner_root
        except ValueError:
            owned = False
        if not owned:
            return jsonify({'success': False, 'error': '简历文件不属于当前 Principal'}), 403

    mysql = get_mysql_client()
    task_id = _create_task('L0_parse_resume', {'student_id': student_id, 'file_path': file_path}, mysql)

    try:
        NSLEPipeline = _get_mvp_pipeline()

        if not NSLEPipeline:
            mysql.execute_write(
                "UPDATE pipeline_task SET status='failed', error_message=:error WHERE task_id=:task_id",
                {"error": "NSLEPipeline 模块不可用", "task_id": task_id}
            )
            return jsonify({'success': False, 'error': 'NSLEPipeline 模块不可用，请检查依赖安装'}), 500

        pipeline = NSLEPipeline()
        result = pipeline.process_resume(file_path, student_id)

        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(result.get("error") or "解析失败")

        mysql.execute_write(
            "UPDATE pipeline_task SET status='partial', progress=100, result_json=:result WHERE task_id=:task_id",
            {"result": str(result), "task_id": task_id}
        )

        return jsonify({
            'success': True,
            'data': {
                'task_id': task_id,
                'status': 'partial',
                'message': '解析结果已返回，结构化画像仍待确认和持久化'
            }
        })

    except Exception as e:
        mysql.execute_write(
            "UPDATE pipeline_task SET status='failed', error_message=:error WHERE task_id=:task_id",
            {"error": str(e), "task_id": task_id}
        )
        return jsonify(safe_error("L0 简历解析", e, message="解析失败，请稍后重试")), 500


@pipeline_bp.route('/l1/build-graph', methods=['POST'])
def build_graph():
    """L1: 知识图谱构建"""
    try:
        resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    return _dispatch_unavailable()


@pipeline_bp.route('/l2/generate-portrait', methods=['POST'])
def generate_portrait():
    """L2: 画像生成"""
    try:
        resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    return _dispatch_unavailable()


@pipeline_bp.route('/l3/run-match', methods=['POST'])
def run_match():
    """L3: 人岗匹配"""
    data = request.get_json(silent=True)
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    if Config.DEMO_MODE:
        return _dispatch_unavailable()
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
    job_portrait_id = data.get('job_portrait_id')

    if not isinstance(job_portrait_id, str) or not job_portrait_id.strip() or len(job_portrait_id) > 64:
        return jsonify({'success': False, 'error': 'job_portrait_id 必填'}), 400
    # Ignore browser-supplied identity, scores and source claims. The domain
    # prerequisites are authoritative rows read before an intent is accepted.
    try:
        from ..services.pipeline import (PipelineSourceUnavailable,
                                         require_confirmed_profile,
                                         require_authoritative_match_sources)
        from ..services.pipeline_match_jobs import persist_match_intent, enqueue_match_intent
        mysql = get_mysql_client()
        profile = require_confirmed_profile(mysql, scope.student_id)
        require_authoritative_match_sources(mysql, scope.student_id, job_portrait_id, profile)
        task_id = f"task_{uuid.uuid4().hex[:20]}"
        persist_match_intent(mysql, student_id=scope.student_id,
                             job_portrait_id=job_portrait_id, task_id=task_id)
        try:
            delivery = enqueue_match_intent(mysql, task_id)
        except Exception:
            # The intent is already committed. Preserve its stable ID so the
            # owner can read it and the operator can replay it after recovery.
            logger.exception("L3 delivery failed after durable commit task_id=%s", task_id)
            delivery = "unverified"
        return jsonify({'success': True, 'data': {
            'task_id': task_id, 'pipeline': 'L3_run_match', 'status': 'pending',
            'delivery': delivery, 'retryable': delivery != 'queued',
        }}), 202
    except (ValueError, PipelineSourceUnavailable) as e:
        return jsonify({'success': False, 'error': str(e), 'retryable': False}), 409
    except Exception as e:
        return jsonify(safe_error('L3 持久派发', e, message='匹配任务未确认受理；请检查迁移和数据库')), 503


@pipeline_bp.route('/l4/generate-report', methods=['POST'])
def generate_report():
    """L4: 报告生成"""
    data = request.get_json(silent=True)
    try:
        resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
    job_portrait_id = data.get('job_portrait_id')

    if not job_portrait_id:
        return jsonify({'success': False, 'error': 'job_portrait_id 必填'}), 400

    return _dispatch_unavailable()
