"""
匹配与分析 API 路由
/api/match — 四维匹配、技能差距、职业路径推荐
"""

from flask import request, jsonify

from . import match_bp
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.match')


@match_bp.route('/run', methods=['POST'])
def run_match():
    """执行人岗匹配 (L3 管线)"""
    data = request.get_json()
    student_id = data.get('student_id')
    job_id = data.get('job_id')

    if not student_id or not job_id:
        return jsonify({"success": False, "error": "student_id 和 job_id 必填"}), 400

    # TODO: 触发 L3 四维匹配管线, 返回 match_result
    return jsonify({
        "success": True,
        "data": {"task_id": "", "status": "pending"}
    })


@match_bp.route('/result/<student_id>/<job_id>', methods=['GET'])
def get_match_result(student_id: str, job_id: str):
    """获取匹配结果"""
    # TODO: 从 MySQL 查询 match_result
    return jsonify({
        "success": True,
        "data": None
    })


@match_bp.route('/skill-gap/<student_id>/<job_id>', methods=['GET'])
def get_skill_gap(student_id: str, job_id: str):
    """获取技能差距分析"""
    # TODO: 从 MySQL 查询 skill_gap
    return jsonify({
        "success": True,
        "data": None
    })


@match_bp.route('/career-recommendation/<student_id>', methods=['GET'])
def get_career_recommendation(student_id: str):
    """获取职业路径推荐"""
    # TODO: 从 MySQL 查询 career_path_recommendation
    return jsonify({
        "success": True,
        "data": None
    })
