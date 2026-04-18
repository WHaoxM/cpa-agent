"""
Demo Favorites API - 收藏相关 Demo API
"""

import uuid
from flask import jsonify
from . import favorites_bp

MOCK_SAVED_JOBS = [
    {
        "id": "saved_001",
        "jobTitle": "Python 后端开发工程师",
        "company": "字节跳动",
        "industry": "互联网",
        "salary": "25K-40K",
        "location": "北京",
        "matchScore": 85,
        "requiredSkills": ["Python", "Django", "MySQL", "Redis"],
        "savedAt": "2026-04-10",
        "role": "后端开发"
    },
    {
        "id": "saved_002",
        "jobTitle": "前端开发工程师",
        "company": "阿里巴巴",
        "industry": "互联网",
        "salary": "20K-35K",
        "location": "杭州",
        "matchScore": 72,
        "requiredSkills": ["Vue.js", "React", "JavaScript", "CSS"],
        "savedAt": "2026-04-12",
        "role": "前端开发"
    }
]

MOCK_TARGET_ROLES = [
    {"role": "后端开发", "savedAt": "2026-04-01"},
    {"role": "前端开发", "savedAt": "2026-04-05"}
]


@favorites_bp.route('/saved-jobs', methods=['GET'])
def list_saved_jobs():
    """获取收藏的岗位"""
    return jsonify({
        "success": True,
        "data": MOCK_SAVED_JOBS
    })


@favorites_bp.route('/saved-jobs', methods=['POST'])
def save_job():
    """收藏岗位"""
    job_id = f"saved_{uuid.uuid4().hex[:8]}"
    return jsonify({
        "success": True,
        "data": {"id": job_id, "message": "岗位已收藏"}
    })


@favorites_bp.route('/saved-jobs/<job_id>', methods=['DELETE'])
def unsave_job(job_id: str):
    """取消收藏"""
    return jsonify({
        "success": True,
        "data": {"message": f"岗位 {job_id} 已取消收藏"}
    })


@favorites_bp.route('/target-roles', methods=['GET'])
def list_target_roles():
    """获取目标职业方向"""
    return jsonify({
        "success": True,
        "data": MOCK_TARGET_ROLES
    })


@favorites_bp.route('/target-roles', methods=['POST'])
def add_target_role():
    """添加目标职业方向"""
    return jsonify({
        "success": True,
        "data": {"message": "目标职业已添加"}
    })


@favorites_bp.route('/target-roles/<role>', methods=['DELETE'])
def remove_target_role(role: str):
    """移除目标职业方向"""
    return jsonify({
        "success": True,
        "data": {"message": f"目标职业 {role} 已移除"}
    })


@favorites_bp.route('/target-role-market/<role>', methods=['GET'])
def get_role_market(role: str):
    """获取职业市场信息"""
    return jsonify({
        "success": True,
        "data": {
            "role": role,
            "salaryRange": "15K-40K",
            "medianSalary": 25000,
            "demandLevel": "高",
            "hotCities": ["北京", "上海", "杭州", "深圳"],
            "industries": ["互联网", "金融", "教育"],
            "skillTags": ["Python", "Java", "SQL", "微服务"],
            "sampleJobs": ["后端开发", "算法工程师", "数据工程师"],
            "trendNote": "需求持续增长，平均薪资年涨幅 10%",
            "referenceMatch": 78
        }
    })
