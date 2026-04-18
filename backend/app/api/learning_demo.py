"""
Demo Learning API - 学习相关 Demo API
"""

import uuid
from flask import jsonify
from . import learning_bp

MOCK_COURSES = [
    {
        "id": "course_001",
        "title": "Python 入门与进阶",
        "description": "从零基础学习 Python 编程，掌握核心语法和实战技能",
        "cover": "https://example.com/python.jpg",
        "categoryId": "cat_python",
        "teacherId": "teacher_001",
        "teacherName": "张老师",
        "chapters": [
            {"id": "ch_001", "title": "Python 基础语法", "duration": 45, "order": 1},
            {"id": "ch_002", "title": "函数与模块", "duration": 60, "order": 2},
            {"id": "ch_003", "title": "面向对象编程", "duration": 55, "order": 3},
        ],
        "totalDuration": 160,
        "studentCount": 1234,
        "rating": 4.8,
        "status": "published",
        "skillTags": ["Python", "编程基础"]
    },
    {
        "id": "course_002",
        "title": "前端开发实战",
        "description": "学习 Vue.js + React 框架，完成真实项目",
        "cover": "https://example.com/frontend.jpg",
        "categoryId": "cat_frontend",
        "teacherId": "teacher_002",
        "teacherName": "李老师",
        "chapters": [
            {"id": "ch_004", "title": "HTML/CSS 基础", "duration": 40, "order": 1},
            {"id": "ch_005", "title": "JavaScript 进阶", "duration": 65, "order": 2},
            {"id": "ch_006", "title": "Vue.js 框架", "duration": 80, "order": 3},
        ],
        "totalDuration": 185,
        "studentCount": 2345,
        "rating": 4.9,
        "status": "published",
        "skillTags": ["Vue.js", "React", "前端"]
    }
]

MOCK_PROGRESS = {
    "course_001": {
        "completedChapters": ["ch_001"],
        "totalProgress": 28,
        "lastPosition": 30
    }
}


@learning_bp.route('/courses', methods=['GET'])
def list_courses():
    """获取课程列表"""
    category = None
    return jsonify({
        "success": True,
        "data": {
            "courses": MOCK_COURSES,
            "count": len(MOCK_COURSES)
        }
    })


@learning_bp.route('/courses/<course_id>', methods=['GET'])
def get_course(course_id: str):
    """获取课程详情"""
    course = next((c for c in MOCK_COURSES if c['id'] == course_id), MOCK_COURSES[0])
    return jsonify({
        "success": True,
        "data": course
    })


@learning_bp.route('/courses/<course_id>/chapters', methods=['GET'])
def get_course_chapters(course_id: str):
    """获取课程章节"""
    course = next((c for c in MOCK_COURSES if c['id'] == course_id), MOCK_COURSES[0])
    return jsonify({
        "success": True,
        "data": course.get('chapters', [])
    })


@learning_bp.route('/learning/progress/<user_id>', methods=['GET'])
def get_learning_progress(user_id: str):
    """获取学习进度"""
    return jsonify({
        "success": True,
        "data": MOCK_PROGRESS
    })


@learning_bp.route('/learning/progress', methods=['POST'])
def update_progress():
    """更新学习进度"""
    return jsonify({
        "success": True,
        "data": {"message": "进度已更新"}
    })


@learning_bp.route('/learning/report/<user_id>', methods=['GET'])
def get_learning_report(user_id: str):
    """获取学习报告"""
    return jsonify({
        "success": True,
        "data": {
            "totalStudyTime": 1250,
            "completedChapters": 8,
            "totalChapters": 24,
            "averageScore": 85.5,
            "quizCount": 12,
            "weeklyProgress": [
                {"date": "2026-04-11", "duration": 120, "chapters": 2},
                {"date": "2026-04-12", "duration": 90, "chapters": 1},
                {"date": "2026-04-13", "duration": 150, "chapters": 3},
            ],
            "knowledgePoints": [
                {"name": "Python基础", "score": 88},
                {"name": "Web开发", "score": 75},
            ]
        }
    })
