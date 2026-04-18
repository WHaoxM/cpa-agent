"""
报告相关 API 路由
/api/report — 职业发展报告的生成、查询、导出
"""

import uuid
from flask import request, jsonify

from . import report_bp
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.report')


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

    return jsonify({
        "success": True,
        "data": {
            "report_id": report_id,
            "status": "pending",
            "message": "报告生成任务已创建，请通过 report_id 查询进度"
        }
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
        WHERE cr.report_id = %s
        """

        results = mysql.execute(sql, {"report_id": report_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到报告: {report_id}"
            }), 404

        report = results[0]

        def parse_json_field(field_value):
            if isinstance(field_value, str):
                import json
                try:
                    return json.loads(field_value)
                except:
                    return None
            return field_value

        chapters = {}
        for i in range(1, 7):
            ch_field = f"ch{i}_json"
            if report.get(ch_field):
                chapters[f"ch{i}"] = parse_json_field(report[ch_field])

        report_json = report.get('report_json')
        if isinstance(report_json, str):
            import json
            try:
                report['report_json'] = json.loads(report_json)
            except:
                pass

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
        logger.error(f"获取报告失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


@report_bp.route('/<report_id>/export', methods=['GET'])
def export_report(report_id: str):
    """导出报告 (HTML/PDF)"""
    fmt = request.args.get('format', 'html')

    try:
        mysql = get_mysql_client()

        sql = "SELECT report_id, status, html_path, pdf_path FROM career_report WHERE report_id = %s"
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
        logger.error(f"导出报告失败: {e}")
        return jsonify({
            "success": False,
            "error": f"导出失败: {str(e)}"
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """获取报告列表"""
    try:
        mysql = get_mysql_client()

        student_id = request.args.get('student_id')
        status = request.args.get('status')
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        if student_id:
            where_clause = "WHERE cr.student_id = %s"
            params = {"student_id": student_id, "limit": limit, "offset": offset}
            count_where = "WHERE student_id = %s"
            count_params = {"student_id": student_id}
        else:
            where_clause = "WHERE 1=1"
            params = {"limit": limit, "offset": offset}
            count_where = "WHERE 1=1"
            count_params = {}

        if status:
            where_clause += " AND cr.status = %(status)s"
            params["status"] = status
            count_where += " AND status = %(status)s"
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
            sp.name as student_name,
            jp.position_name
        FROM career_report cr
        LEFT JOIN student_profile sp ON cr.student_id = sp.student_id
        LEFT JOIN job_portrait_4d jp ON cr.job_portrait_id = jp.portrait_id
        {where_clause}
        ORDER BY cr.created_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
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
        logger.error(f"获取报告列表失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


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
        WHERE pt.task_id = %s
        """

        results = mysql.execute(sql, {"task_id": task_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到任务: {task_id}"
            }), 404

        task = results[0]

        params_json = task.get('params_json')
        if isinstance(params_json, str):
            import json
            try:
                task['params_json'] = json.loads(params_json)
            except:
                pass

        result_json = task.get('result_json')
        if isinstance(result_json, str):
            import json
            try:
                task['result_json'] = json.loads(result_json)
            except:
                pass

        return jsonify({
            "success": True,
            "data": task
        })

    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500
