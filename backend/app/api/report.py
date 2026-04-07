"""
报告相关 API 路由
/api/report — 职业发展报告的生成、查询、导出
"""

from flask import request, jsonify

from . import report_bp
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.report')


@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """生成职业发展报告 (L4 管线)"""
    data = request.get_json()
    student_id = data.get('student_id')
    job_id = data.get('job_id')

    if not student_id or not job_id:
        return jsonify({"success": False, "error": "student_id 和 job_id 必填"}), 400

    # TODO: 触发 L4 报告生成管线, 返回 report_id
    return jsonify({
        "success": True,
        "data": {"report_id": "", "status": "pending"}
    })


@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """获取报告详情"""
    # TODO: 从 MySQL 查询 career_report
    return jsonify({
        "success": True,
        "data": None
    })


@report_bp.route('/<report_id>/export', methods=['GET'])
def export_report(report_id: str):
    """导出报告 (HTML/PDF)"""
    fmt = request.args.get('format', 'html')
    # TODO: Jinja2 渲染 → HTML / weasyprint → PDF
    return jsonify({
        "success": True,
        "data": {"download_url": ""}
    })


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """获取报告列表"""
    student_id = request.args.get('student_id')
    # TODO: 从 MySQL 查询报告列表
    return jsonify({
        "success": True,
        "data": [],
        "count": 0
    })
