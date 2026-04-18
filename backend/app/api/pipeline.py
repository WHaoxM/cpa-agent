"""
Pipeline API - L0-L4 管线调用接口
整合 mvp/entity_extractor 的 NS-LE Pipeline
"""

import os
import uuid
from datetime import datetime
from typing import Optional

from flask import Blueprint, request, jsonify

from ..services.db import get_mysql_client
from ..utils.logger import get_logger
from ..validators import validate_student_id, validate_pipeline_name

logger = get_logger('cpa-agent.api.pipeline')

pipeline_bp = Blueprint('pipeline', __name__)


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
        logger.warning(f"MVP Pipeline 导入失败: {e}, 返回 Mock")
        return None


def _create_task(pipeline: str, params: dict, mysql) -> str:
    """创建管线任务记录"""
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    sql = """
    INSERT INTO pipeline_task
    (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
    VALUES (:task_id, :pipeline, :status, :params_json, :student_id, :job_id, :triggered_by, :created_at)
    """

    import json
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

    return jsonify({
        'success': True,
        'data': pipelines
    })


@pipeline_bp.route('/trigger', methods=['POST'])
def trigger_pipeline():
    """触发管线任务"""
    data = request.get_json() or {}

    pipeline = data.get('pipeline', '')
    student_id = data.get('student_id', '')

    ok, msg = validate_pipeline_name(pipeline)
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    if student_id:
        ok, msg = validate_student_id(student_id)
        if not ok:
            return jsonify({'success': False, 'error': msg}), 400

    mysql = get_mysql_client()
    task_id = _create_task(pipeline, data, mysql)

    logger.info(f"管线触发: task_id={task_id}, pipeline={pipeline}")

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'pipeline': pipeline,
            'status': 'pending'
        }
    })


@pipeline_bp.route('/status/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    """获取任务状态"""
    mysql = get_mysql_client()

    sql = """
    SELECT task_id, pipeline, status, progress, result_json, error_message, created_at
    FROM pipeline_task WHERE task_id = :task_id
    """
    result = mysql.execute(sql, {"task_id": task_id})

    if not result:
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    task = result[0]

    import json
    result_json = task.get('result_json')
    if isinstance(result_json, str):
        try:
            result_json = json.loads(result_json)
        except:
            pass

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


@pipeline_bp.route('/l0/parse-resume', methods=['POST'])
def parse_resume():
    """L0: 简历解析"""
    data = request.get_json() or {}

    file_path = data.get('file_path')
    student_id = data.get('student_id')

    if not student_id:
        return jsonify({'success': False, 'error': 'student_id 必填'}), 400

    mysql = get_mysql_client()
    task_id = _create_task('L0_parse_resume', {'student_id': student_id, 'file_path': file_path}, mysql)

    try:
        NSLEPipeline = _get_mvp_pipeline()

        if NSLEPipeline:
            pipeline = NSLEPipeline()
            result = pipeline.process_resume(file_path, student_id)

            mysql.execute_write(
                "UPDATE pipeline_task SET status='completed', progress=100, result_json=:result WHERE task_id=:task_id",
                {"result": str(result), "task_id": task_id}
            )
        else:
            mysql.execute_write(
                "UPDATE pipeline_task SET status='running', progress=50 WHERE task_id=:task_id",
                {"task_id": task_id}
            )

        return jsonify({
            'success': True,
            'data': {
                'task_id': task_id,
                'status': 'running',
                'message': '简历解析任务已创建'
            }
        })

    except Exception as e:
        logger.error(f"L0 简历解析失败: {e}")
        mysql.execute_write(
            "UPDATE pipeline_task SET status='failed', error_message=:error WHERE task_id=:task_id",
            {"error": str(e), "task_id": task_id}
        )
        return jsonify({'success': False, 'error': str(e)}), 500


@pipeline_bp.route('/l1/build-graph', methods=['POST'])
def build_graph():
    """L1: 知识图谱构建"""
    data = request.get_json() or {}
    student_id = data.get('student_id')

    if not student_id:
        return jsonify({'success': False, 'error': 'student_id 必填'}), 400

    mysql = get_mysql_client()
    task_id = _create_task('L1_build_graph', {'student_id': student_id}, mysql)

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'status': 'pending',
            'message': '图谱构建任务已创建'
        }
    })


@pipeline_bp.route('/l2/generate-portrait', methods=['POST'])
def generate_portrait():
    """L2: 画像生成"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    job_id = data.get('job_id')

    if not student_id:
        return jsonify({'success': False, 'error': 'student_id 必填'}), 400

    mysql = get_mysql_client()
    task_id = _create_task('L2_generate_portrait', {'student_id': student_id, 'job_id': job_id}, mysql)

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'status': 'pending',
            'message': '画像生成任务已创建'
        }
    })


@pipeline_bp.route('/l3/run-match', methods=['POST'])
def run_match():
    """L3: 人岗匹配"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    job_portrait_id = data.get('job_portrait_id')

    if not student_id or not job_portrait_id:
        return jsonify({'success': False, 'error': 'student_id 和 job_portrait_id 必填'}), 400

    mysql = get_mysql_client()
    task_id = _create_task('L3_run_match', {'student_id': student_id, 'job_portrait_id': job_portrait_id}, mysql)

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'status': 'pending',
            'message': '匹配任务已创建'
        }
    })


@pipeline_bp.route('/l4/generate-report', methods=['POST'])
def generate_report():
    """L4: 报告生成"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    job_portrait_id = data.get('job_portrait_id')

    if not student_id or not job_portrait_id:
        return jsonify({'success': False, 'error': 'student_id 和 job_portrait_id 必填'}), 400

    mysql = get_mysql_client()
    task_id = _create_task('L4_generate_report', {'student_id': student_id, 'job_portrait_id': job_portrait_id}, mysql)

    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'status': 'pending',
            'message': '报告生成任务已创建'
        }
    })
