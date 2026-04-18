"""
Demo Graph API - 带 Mock 降级的图谱 API
"""

from flask import jsonify
from . import graph_bp

MOCK_GRAPH_OVERVIEW = {
    "stats": {
        "skill_count": 128,
        "portrait_count": 25,
        "career_path_count": 45,
        "relationship_count": 512
    },
    "categories": ["前端", "后端", "移动端", "算法", "运维", "通用"]
}

MOCK_GRAPH_QUERY = {
    "nodes": [
        {"id": "skill_001", "name": "Python", "category": "后端", "tier": "foundation"},
        {"id": "skill_002", "name": "Django", "category": "后端", "tier": "junior"},
        {"id": "skill_003", "name": "Flask", "category": "后端", "tier": "junior"},
    ],
    "edges": [
        {"from": "skill_002", "to": "skill_001", "type": "prerequisite", "confidence": 0.9},
        {"from": "skill_003", "to": "skill_001", "type": "prerequisite", "confidence": 0.85},
    ],
    "answer": "Python 后端开发需要掌握 Python 基础，以及 Django 或 Flask 框架。建议学习路线: Python → Django/Flask → 深入理解 Web 框架 → 微服务架构。"
}


@graph_bp.route('/overview', methods=['GET'])
def graph_overview():
    """获取图谱概览 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_GRAPH_OVERVIEW,
        "mode": "demo"
    })


@graph_bp.route('/query', methods=['POST'])
def graph_query():
    """自然语言图谱查询 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_GRAPH_QUERY,
        "mode": "demo"
    })


@graph_bp.route('/career-path/<job_id>', methods=['GET'])
def career_path(job_id: str):
    """获取职业路径 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "job_id": job_id,
            "current": "Python 后端开发工程师",
            "paths": [
                {"step": 1, "position": "初级 Python 开发", "years": 0, "skills": ["Python", "MySQL"]},
                {"step": 2, "position": "中级 Python 开发", "years": 2, "skills": ["Django", "Redis", "微服务"]},
                {"step": 3, "position": "高级 Python 开发", "years": 4, "skills": ["架构设计", "团队管理"]},
                {"step": 4, "position": "技术专家/架构师", "years": 6, "skills": ["系统架构", "技术决策"]},
            ],
            "lateral_options": ["全栈工程师", "DevOps 工程师", "数据工程师"]
        },
        "mode": "demo"
    })


@graph_bp.route('/skill-neighbors/<skill_name>', methods=['GET'])
def skill_neighbors(skill_name: str):
    """获取技能邻居 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "skill": skill_name,
            "prerequisites": [
                {"name": "编程基础", "tier": "foundation"},
                {"name": "数据结构", "tier": "foundation"},
            ],
            "related_skills": [
                {"name": "Java", "similarity": 0.75},
                {"name": "Go", "similarity": 0.70},
            ],
            "next_steps": [
                {"name": "Django", "tier": "junior"},
                {"name": "Flask", "tier": "junior"},
            ]
        },
        "mode": "demo"
    })
