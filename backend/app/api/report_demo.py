"""
Demo Report API - 带 Mock 降级的报告 API
"""

import uuid
from flask import jsonify
from . import report_bp

MOCK_REPORT = {
    "report_id": "report_demo_001",
    "student_id": "demo_student_001",
    "job_portrait_id": "portrait_job_001",
    "title": "张三 - Python后端开发工程师 - 职业发展报告",
    "status": "completed",
    "chapters": {
        "ch1": {"title": "个人画像摘要", "content": "张三，清华大学计算机科学与技术专业..."},
        "ch2": {"title": "目标岗位分析", "content": "Python后端开发工程师，需要掌握..."},
        "ch3": {"title": "人岗匹配度分析", "content": "综合匹配度 78.4%，等级：良好..."},
        "ch4": {"title": "职业发展路径", "content": "推荐发展路径：初级→中级→高级..."},
        "ch5": {"title": "差距与学习规划", "content": "需要学习 Docker、Redis 等关键技能..."},
        "ch6": {"title": "评估周期", "content": "建议 3 个月后进行第一次评估..."}
    },
    "html_path": "/reports/report_demo_001.html",
    "pdf_path": "/reports/report_demo_001.pdf",
    "elapsed_seconds": 12.5,
    "created_at": "2026-04-17T10:00:00"
}


@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """生成报告 - Demo 模式"""
    report_id = f"report_{uuid.uuid4().hex[:12]}"
    return jsonify({
        "success": True,
        "data": {
            "report_id": report_id,
            "status": "completed",
            "message": "Demo 模式: Mock 报告"
        }
    })


@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """获取报告 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_REPORT,
        "mode": "demo"
    })


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """报告列表 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "reports": [MOCK_REPORT],
            "count": 1
        },
        "mode": "demo"
    })


@report_bp.route('/<report_id>/export', methods=['GET'])
def export_report(report_id: str):
    """导出报告 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "report_id": report_id,
            "format": "html",
            "download_url": f"/reports/{report_id}.html",
            "message": "Demo 模式: Mock 导出"
        }
    })
