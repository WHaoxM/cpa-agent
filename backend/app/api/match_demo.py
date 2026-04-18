"""
Demo Match API - 带 Mock 降级的匹配 API
"""

import uuid
from flask import jsonify
from . import match_bp

MOCK_MATCH_RESULT = {
    "student_id": "demo_student_001",
    "job_portrait_id": "portrait_job_001",
    "dimensions": {
        "D1": {"score": 85.5, "passed": True, "description": "基础要求匹配"},
        "D2": {"score": 72.0, "skill_coverage": 0.72, "description": "技能匹配"},
        "D3": {"score": 78.0, "description": "职业素养"},
        "D4": {"score": 82.0, "description": "发展潜力"}
    },
    "total_score": 78.4,
    "is_matched": True,
    "match_level": "良好"
}

MOCK_SKILL_GAP = {
    "student_id": "demo_student_001",
    "job_portrait_id": "portrait_job_001",
    "critical_count": 2,
    "quick_learn_count": 3,
    "total_gap_count": 5,
    "gaps": [
        {"skill": "Docker", "gap_type": "critical", "importance": 0.9, "learning_difficulty": "中等", "resource": "Docker 官方文档"},
        {"skill": "Redis", "gap_type": "critical", "importance": 0.85, "learning_difficulty": "简单", "resource": "Redis 教程"},
        {"skill": "Kubernetes", "gap_type": "quick_learn", "importance": 0.6, "learning_difficulty": "困难", "resource": "K8s 实战"},
        {"skill": "消息队列", "gap_type": "quick_learn", "importance": 0.55, "learning_difficulty": "中等", "resource": "RabbitMQ 教程"},
        {"skill": "API 设计", "gap_type": "bonus", "importance": 0.4, "learning_difficulty": "简单", "resource": "RESTful API 设计"}
    ]
}

MOCK_CAREER_RECOMMENDATION = {
    "student_id": "demo_student_001",
    "current_level": "初级 Python 开发",
    "recommended_dir": "后端开发工程师 → 高级后端开发 → 技术专家",
    "promotion_steps": 3,
    "lateral_options": 2,
    "paths": [
        {
            "step": 1,
            "position_name": "初级 Python 开发",
            "level": "junior",
            "years_to_promotion": 1,
            "skills_needed": ["Python 基础", "MySQL", "Redis 基础"]
        },
        {
            "step": 2,
            "position_name": "中级 Python 开发",
            "level": "mid",
            "years_to_promotion": 2,
            "skills_needed": ["Django/Flask", "微服务", "Docker", "Linux"]
        },
        {
            "step": 3,
            "position_name": "高级 Python 开发",
            "level": "senior",
            "years_to_promotion": 3,
            "skills_needed": ["架构设计", "团队管理", "K8s", "性能优化"]
        }
    ]
}


@match_bp.route('/run', methods=['POST'])
def run_match():
    """执行人岗匹配 - Demo 模式"""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "completed",
            "message": "Demo 模式: Mock 匹配结果"
        }
    })


@match_bp.route('/result/<student_id>/<job_portrait_id>', methods=['GET'])
def get_match_result(student_id: str, job_portrait_id: str):
    """获取匹配结果 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_MATCH_RESULT,
        "mode": "demo"
    })


@match_bp.route('/skill-gap/<student_id>/<job_portrait_id>', methods=['GET'])
def get_skill_gap(student_id: str, job_portrait_id: str):
    """获取技能差距 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_SKILL_GAP,
        "mode": "demo"
    })


@match_bp.route('/career-recommendation/<student_id>', methods=['GET'])
def get_career_recommendation(student_id: str):
    """获取职业推荐 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_CAREER_RECOMMENDATION,
        "mode": "demo"
    })
