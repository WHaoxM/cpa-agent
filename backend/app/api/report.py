"""
报告相关 API 路由
/api/report — 职业发展报告的生成、查询、导出
"""

import json
import uuid
from flask import request, jsonify

from . import report_bp
from ._helpers import clamp_pagination, parse_json_field, safe_error
from ..config import Config
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.report')


def _build_demo_report_payload(report_id: str, student_id: str, job_portrait_id: str, data: dict) -> dict:
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {
            "targetRoles": ["AI application engineer", "Python backend engineer"],
            "topMatchScore": 82,
            "studentDimSnapshot": {
                "professional_skill": 76,
                "certification": 64,
                "innovation": 72,
                "learning": 84,
                "pressure": 70,
                "communication": 78,
                "internship": 68,
            },
        }

    title = data.get("title") or "Career development report"
    chapters = {
        "ch1": {
            "title": "Student profile",
            "summary": "Demo mode report generated from the saved career snapshot.",
        },
        "ch2": {
            "title": "Target role analysis",
            "job_portrait_id": job_portrait_id,
            "target_roles": snapshot.get("targetRoles", []),
        },
        "ch3": {
            "title": "Person-job match",
            "match_score": snapshot.get("topMatchScore", 82),
        },
        "ch4": {
            "title": "Career path",
            "milestones": ["Foundation consolidation", "Project practice", "Interview preparation"],
        },
        "ch5": {
            "title": "Skill gap plan",
            "focus": ["Engineering practice", "RAG application", "Deployment readiness"],
        },
        "ch6": {
            "title": "Review cycle",
            "cadence": "Review progress every 2 weeks in demo mode.",
        },
    }
    report_json = {
        "report_id": report_id,
        "student_id": student_id,
        "job_portrait_id": job_portrait_id,
        "title": title,
        "type": data.get("type") or "career",
        "source": "demo_fallback",
        "snapshot": snapshot,
        "chapters": chapters,
    }
    return {
        "report_id": report_id,
        "student_id": student_id,
        "job_portrait_id": job_portrait_id,
        "title": title,
        "status": "completed",
        "chapters": chapters,
        "report_json": report_json,
        "elapsed_seconds": 0.1,
    }


def _save_demo_report(mysql, report: dict) -> None:
    chapters = report["chapters"]
    sql = """
    INSERT INTO career_report (
        report_id,
        student_id,
        job_portrait_id,
        title,
        status,
        ch1_json,
        ch2_json,
        ch3_json,
        ch4_json,
        ch5_json,
        ch6_json,
        report_json,
        elapsed_seconds
    )
    VALUES (
        :report_id,
        :student_id,
        :job_portrait_id,
        :title,
        :status,
        :ch1_json,
        :ch2_json,
        :ch3_json,
        :ch4_json,
        :ch5_json,
        :ch6_json,
        :report_json,
        :elapsed_seconds
    )
    ON DUPLICATE KEY UPDATE
        title = VALUES(title),
        status = VALUES(status),
        ch1_json = VALUES(ch1_json),
        ch2_json = VALUES(ch2_json),
        ch3_json = VALUES(ch3_json),
        ch4_json = VALUES(ch4_json),
        ch5_json = VALUES(ch5_json),
        ch6_json = VALUES(ch6_json),
        report_json = VALUES(report_json),
        elapsed_seconds = VALUES(elapsed_seconds)
    """
    mysql.execute_write(sql, {
        "report_id": report["report_id"],
        "student_id": report["student_id"],
        "job_portrait_id": report["job_portrait_id"],
        "title": report["title"],
        "status": report["status"],
        "ch1_json": json.dumps(chapters["ch1"], ensure_ascii=False),
        "ch2_json": json.dumps(chapters["ch2"], ensure_ascii=False),
        "ch3_json": json.dumps(chapters["ch3"], ensure_ascii=False),
        "ch4_json": json.dumps(chapters["ch4"], ensure_ascii=False),
        "ch5_json": json.dumps(chapters["ch5"], ensure_ascii=False),
        "ch6_json": json.dumps(chapters["ch6"], ensure_ascii=False),
        "report_json": json.dumps(report["report_json"], ensure_ascii=False),
        "elapsed_seconds": report["elapsed_seconds"],
    })


@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """生成职业发展报告 (L4 管线)"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    job_portrait_id = data.get('job_portrait_id') or data.get('job_id')

    if not student_id or not job_portrait_id:
        return jsonify({
            "success": False,
            "error": "student_id 和 job_portrait_id 必填"
        }), 400

    report_id = f"report_{uuid.uuid4().hex[:12]}"

    logger.info(f"触发报告生成: report_id={report_id}, student_id={student_id}, job_portrait_id={job_portrait_id}")

    # ── D-16: 实际触发 L4 管线 ──
    if Config.DEMO_MODE:
        try:
            mysql = get_mysql_client()
            demo_report = _build_demo_report_payload(report_id, student_id, job_portrait_id, data)
            _save_demo_report(mysql, demo_report)
            return jsonify({
                "success": True,
                "data": {
                    "report_id": report_id,
                    "status": "completed",
                    "message": "Demo report generated",
                    "source": "demo_fallback",
                    "report": demo_report["report_json"],
                },
            })
        except Exception as e:
            return jsonify(safe_error("Demo报告生成", e, message="Demo report generation failed")), 500

    report_result = None
    try:
        from ..services.pipeline import PipelineRunner, PipelineLevel
        runner = PipelineRunner()
        report_result = runner.run(
            pipeline=PipelineLevel.L4_GENERATE_REPORT,
            params={
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "report_id": report_id,
            },
        )
    except Exception as e:
        logger.error(f"L4 报告生成失败: {e}")
        report_result = {"status": "failed", "error": "报告生成失败"}

    response_data = {
        "report_id": report_id,
    }

    if report_result and isinstance(report_result, dict):
        response_data["status"] = report_result.get("status", "completed")
        if "error" not in report_result:
            response_data["message"] = "报告生成完成"
        else:
            response_data["message"] = f"报告生成失败: {report_result.get('error', '')}"
    else:
        response_data["status"] = "pending"
        response_data["message"] = "报告生成任务已创建，请通过 report_id 查询进度"

    return jsonify({
        "success": True,
        "data": response_data
    })


@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """获取报告详情"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            cr.id,
            cr.report_id,
            cr.student_id,
            cr.job_portrait_id,
            cr.title,
            cr.status,
            cr.ch1_json,
            cr.ch2_json,
            cr.ch3_json,
            cr.ch4_json,
            cr.ch5_json,
            cr.ch6_json,
            cr.report_json,
            cr.html_path,
            cr.pdf_path,
            cr.elapsed_seconds,
            cr.created_at,
            cr.updated_at,
            sp.name as student_name,
            sp.school,
            jp.position_name
        FROM career_report cr
        LEFT JOIN student_profile sp ON cr.student_id = sp.student_id
        LEFT JOIN job_portrait_4d jp ON cr.job_portrait_id = jp.portrait_id
        WHERE cr.report_id = :report_id
        """

        results = mysql.execute(sql, {"report_id": report_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到报告: {report_id}"
            }), 404

        report = results[0]

        chapters = {}
        for i in range(1, 7):
            ch_field = f"ch{i}_json"
            if report.get(ch_field):
                chapters[f"ch{i}"] = parse_json_field(report[ch_field])

        report['report_json'] = parse_json_field(report.get('report_json'))

        return jsonify({
            "success": True,
            "data": {
                "report_id": report['report_id'],
                "student_id": report['student_id'],
                "student_name": report.get('student_name'),
                "school": report.get('school'),
                "job_portrait_id": report['job_portrait_id'],
                "position_name": report.get('position_name'),
                "title": report.get('title'),
                "status": report.get('status'),
                "chapters": chapters,
                "report_json": report.get('report_json'),
                "download_urls": {
                    "html": report.get('html_path'),
                    "pdf": report.get('pdf_path')
                },
                "elapsed_seconds": report.get('elapsed_seconds'),
                "created_at": report.get('created_at'),
                "updated_at": report.get('updated_at')
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取报告", e, message="查询失败，请稍后重试")), 500


@report_bp.route('/<report_id>/export', methods=['GET'])
def export_report(report_id: str):
    """导出报告 (HTML/PDF)"""
    fmt = request.args.get('format', 'html')

    try:
        mysql = get_mysql_client()

        sql = "SELECT report_id, status, html_path, pdf_path FROM career_report WHERE report_id = :report_id"
        results = mysql.execute(sql, {"report_id": report_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到报告: {report_id}"
            }), 404

        report = results[0]
        status = report.get('status')

        if status != 'completed':
            return jsonify({
                "success": False,
                "error": f"报告状态为 {status}，暂不支持导出"
            }), 400

        download_url = None
        if fmt == 'html':
            download_url = report.get('html_path')
        elif fmt == 'pdf':
            download_url = report.get('pdf_path')

        if not download_url:
            return jsonify({
                "success": False,
                "error": f"{fmt} 格式暂未生成"
            }), 404

        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "format": fmt,
                "download_url": download_url
            }
        })

    except Exception as e:
        return jsonify(safe_error("导出报告", e, message="导出失败，请稍后重试")), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """获取报告列表"""
    try:
        mysql = get_mysql_client()

        student_id = request.args.get('student_id')
        status = request.args.get('status')
        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

        if student_id:
            where_clause = "WHERE cr.student_id = :student_id"
            params = {"student_id": student_id, "limit": limit, "offset": offset}
            count_where = "WHERE student_id = :student_id"
            count_params = {"student_id": student_id}
        else:
            where_clause = "WHERE 1=1"
            params = {"limit": limit, "offset": offset}
            count_where = "WHERE 1=1"
            count_params = {}

        if status:
            where_clause += " AND cr.status = :status"
            params["status"] = status
            count_where += " AND status = :status"
            count_params["status"] = status

        sql = f"""
        SELECT
            cr.report_id,
            cr.student_id,
            cr.job_portrait_id,
            cr.title,
            cr.status,
            cr.created_at,
            cr.updated_at,
            cr.elapsed_seconds,
            cr.html_path,
            cr.pdf_path
        FROM career_report cr
        {where_clause}
        ORDER BY cr.created_at DESC
        LIMIT :limit OFFSET :offset
        """

        results = mysql.execute(sql, params)

        count_sql = f"SELECT COUNT(*) as total FROM career_report {count_where}"
        count_result = mysql.execute(count_sql, count_params)
        total = count_result[0]['total'] if count_result else 0

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        return jsonify(safe_error("获取报告列表", e, message="查询失败，请稍后重试")), 500


@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """获取报告生成进度"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            cr.report_id,
            cr.status,
            cr.elapsed_seconds,
            pt.progress,
            pt.started_at,
            pt.completed_at
        FROM career_report cr
        LEFT JOIN pipeline_task pt ON pt.result_json LIKE CONCAT('%', cr.report_id, '%')
        WHERE cr.report_id = :report_id
        """

        results = mysql.execute(sql, {"report_id": report_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到报告: {report_id}"
            }), 404

        report = results[0]
        status = report.get('status', 'pending')

        progress = report.get('progress')
        if progress is None:
            if status == 'pending':
                progress = 0
            elif status == 'generating':
                progress = 50
            elif status == 'completed':
                progress = 100
            elif status == 'failed':
                progress = -1

        current_chapter = None
        if status == 'generating':
            current_chapter = "正在生成报告内容"
        elif status == 'completed':
            current_chapter = "报告生成完成"
        elif status == 'failed':
            current_chapter = "报告生成失败"

        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "status": status,
                "progress": progress,
                "current_chapter": current_chapter,
                "elapsed_seconds": report.get('elapsed_seconds'),
                "started_at": report.get('started_at'),
                "completed_at": report.get('completed_at'),
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取报告进度", e, message="查询失败，请稍后重试")), 500


@report_bp.route('/task/<task_id>', methods=['GET'])
def get_report_task(task_id: str):
    """获取报告生成任务状态"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            pt.task_id,
            pt.pipeline,
            pt.status,
            pt.progress,
            pt.params_json,
            pt.result_json,
            pt.error_message,
            pt.started_at,
            pt.completed_at,
            pt.created_at
        FROM pipeline_task pt
        WHERE pt.task_id = :task_id
        """

        results = mysql.execute(sql, {"task_id": task_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到任务: {task_id}"
            }), 404

        task = results[0]

        task['params_json'] = parse_json_field(task.get('params_json'))
        task['result_json'] = parse_json_field(task.get('result_json'))

        return jsonify({
            "success": True,
            "data": task
        })

    except Exception as e:
        return jsonify(safe_error("获取任务状态", e)), 500
