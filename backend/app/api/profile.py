"""
画像相关 API 路由
/api/profile — 学生画像、岗位画像的 CRUD 和生成
"""

from flask import request, jsonify

from . import profile_bp
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.profile')


@profile_bp.route('/student/upload', methods=['POST'])
def upload_resume():
    """上传简历文件, 触发 L0 解析"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "未上传文件"}), 400

    file = request.files['file']
    # TODO: 保存文件 → 触发 L0 管线 (MinerU + Qwen3-SFT) → 返回 task_id
    return jsonify({
        "success": True,
        "data": {"task_id": "", "status": "pending"}
    })


@profile_bp.route('/student/<student_id>', methods=['GET'])
def get_student_profile(student_id: str):
    """获取学生四维画像"""
    # TODO: 从 MySQL 查询 student_portrait_4d
    return jsonify({
        "success": True,
        "data": None
    })


@profile_bp.route('/student/<student_id>/4d', methods=['GET'])
def get_student_4d(student_id: str):
    """获取学生四维评分详情 (D1-D4)"""
    # TODO: 返回 D1_basic, D2_skill, D3_quality, D4_potential + summary
    return jsonify({
        "success": True,
        "data": None
    })


@profile_bp.route('/job/list', methods=['GET'])
def list_job_portraits():
    """获取岗位画像列表"""
    # TODO: 从 MySQL 查询 job_portrait 列表
    return jsonify({
        "success": True,
        "data": [],
        "count": 0
    })


@profile_bp.route('/job/<portrait_id>', methods=['GET'])
def get_job_portrait(portrait_id: str):
    """获取岗位四维需求画像"""
    # TODO: 从 MySQL 查询 job_portrait_4d
    return jsonify({
        "success": True,
        "data": None
    })
