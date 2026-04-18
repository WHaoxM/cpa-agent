"""
Demo Profile API - 带 Mock 降级的画像 API
当数据库不可用时返回 Mock 数据
"""

from flask import jsonify
from . import profile_bp

MOCK_STUDENT = {
    "student_id": "demo_student_001",
    "name": "张三",
    "phone": "138****1234",
    "email": "zhangsan@example.com",
    "degree": "本科",
    "school": "清华大学",
    "major": "计算机科学与技术",
    "graduation_year": 2025,
    "profile_json": {
        "skills": ["Python", "JavaScript", "React", "Node.js", "MySQL"],
        "projects": [
            {"name": "校园二手平台", "role": "技术负责人", "tech": "Vue.js + Spring Boot"}
        ]
    },
    "completeness": 0.85,
    "source_file": "resume_demo.pdf"
}

MOCK_STUDENT_4D = {
    "student_id": "demo_student_001",
    "target_job_id": "portrait_job_001",
    "dimensions": {
        "D1": {"name": "基础要求", "score": 85.5, "passed": True, "description": "学历、学校、专业等基础条件匹配度"},
        "D2": {"name": "职业技能", "score": 78.0, "skill_coverage": 0.72, "description": "技术技能掌握程度和项目经验"},
        "D3": {"name": "职业素养", "score": 82.0, "description": "沟通、团队、领导力等软技能"},
        "D4": {"name": "发展潜力", "score": 88.5, "description": "学习能力、成长空间、潜力评估"}
    },
    "total_score": 82.8,
    "level": "良好",
    "completeness": 0.85,
    "skill_coverage": 0.72,
    "skill_count": 5
}

MOCK_JOBS = [
    {
        "portrait_id": "portrait_job_001",
        "position_name": "Python 后端开发工程师",
        "jd_count": 50,
        "required_degree": "本科",
        "required_skill_cnt": 8,
        "weights": {"D1": 0.25, "D2": 0.35, "D3": 0.20, "D4": 0.20}
    },
    {
        "portrait_id": "portrait_job_002",
        "position_name": "前端开发工程师",
        "jd_count": 45,
        "required_degree": "本科",
        "required_skill_cnt": 6,
        "weights": {"D1": 0.25, "D2": 0.35, "D3": 0.20, "D4": 0.20}
    },
    {
        "portrait_id": "portrait_job_003",
        "position_name": "算法工程师",
        "jd_count": 30,
        "required_degree": "硕士",
        "required_skill_cnt": 10,
        "weights": {"D1": 0.30, "D2": 0.30, "D3": 0.20, "D4": 0.20}
    }
]


@profile_bp.route('/student/<student_id>', methods=['GET'])
def get_student_profile(student_id: str):
    """获取学生基本信息 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "profile": MOCK_STUDENT,
            "mode": "demo"
        }
    })


@profile_bp.route('/student/<student_id>/4d', methods=['GET'])
def get_student_4d(student_id: str):
    """获取学生四维评分详情 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": MOCK_STUDENT_4D,
        "mode": "demo"
    })


@profile_bp.route('/job/list', methods=['GET'])
def list_job_portraits():
    """获取岗位画像列表 - Demo 模式"""
    return jsonify({
        "success": True,
        "data": {
            "jobs": MOCK_JOBS,
            "count": len(MOCK_JOBS)
        },
        "mode": "demo"
    })


@profile_bp.route('/job/<portrait_id>', methods=['GET'])
def get_job_portrait(portrait_id: str):
    """获取岗位四维需求画像 - Demo 模式"""
    job = next((j for j in MOCK_JOBS if j['portrait_id'] == portrait_id), MOCK_JOBS[0])
    return jsonify({
        "success": True,
        "data": job,
        "mode": "demo"
    })
