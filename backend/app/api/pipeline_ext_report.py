"""
Pipeline_ext Report 子模块

L4 报告列表与状态查询 API (2 路由):
- GET /report/list               查询职业发展报告列表
- GET /report/<report_id>/status 查询报告导出状态

依赖: ..services.db (MySQL)
"""

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/report/list', methods=['GET'])
def list_career_reports():
    """
    L4: 查询职业发展报告列表

    Query:
        ?student_id=xxx&limit=10&offset=0&status=all
    """
    student_id = request.args.get('student_id', '')
    limit = request.args.get('limit', 10, type=int)
    offset = request.args.get('offset', 0, type=int)
    status = request.args.get('status', 'all')

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if student_id and status != 'all':
            sql = """
            SELECT report_id, student_id, job_portrait_id, title, status,
                   elapsed_seconds, created_at, updated_at
            FROM career_report
            WHERE student_id = :student_id AND status = :status
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "student_id": student_id,
                "status": status,
                "limit": limit,
                "offset": offset
            })
        elif student_id:
            sql = """
            SELECT report_id, student_id, job_portrait_id, title, status,
                   elapsed_seconds, created_at, updated_at
            FROM career_report
            WHERE student_id = :student_id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "student_id": student_id,
                "limit": limit,
                "offset": offset
            })
        else:
            sql = """
            SELECT report_id, student_id, job_portrait_id, title, status,
                   elapsed_seconds, created_at, updated_at
            FROM career_report
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "limit": limit,
                "offset": offset
            })

        return jsonify({
            "success": True,
            "data": {
                "list": results,
                "count": len(results),
                "limit": limit,
                "offset": offset
            }
        })

    except Exception as e:
        return jsonify(safe_error("查询报告列表", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/report/<report_id>/status', methods=['GET'])
def get_report_status(report_id: str):
    """
    L4: 查询报告导出状态

    Path:
        report_id: 报告ID
    """
    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        sql = """
        SELECT report_id, title, status,
               html_path, pdf_path,
               elapsed_seconds,
               created_at, updated_at
        FROM career_report
        WHERE report_id = :report_id
        """
        row = mysql.execute_single(sql, {"report_id": report_id})

        if not row:
            return jsonify({
                "success": False,
                "error": "报告不存在"
            }), 404

        return jsonify({
            "success": True,
            "data": {
                "report_id": row.get('report_id', ''),
                "title": row.get('title', ''),
                "status": row.get('status', ''),
                "html_path": row.get('html_path', ''),
                "pdf_path": row.get('pdf_path', ''),
                "elapsed_seconds": row.get('elapsed_seconds'),
                "created_at": str(row.get('created_at', '')),
                "updated_at": str(row.get('updated_at', ''))
            }
        })

    except Exception as e:
        return jsonify(safe_error("查询报告状态", e, message="服务器内部错误")), 500
