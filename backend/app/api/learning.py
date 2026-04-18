"""
Learning API - 学习相关 API
"""

import uuid
from flask import request, jsonify

from . import learning_bp
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.learning')


@learning_bp.route('/courses', methods=['GET'])
def list_courses():
    """获取课程列表"""
    try:
        mysql = get_mysql_client()
        category = request.args.get('category')
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        if category:
            sql = "SELECT * FROM course WHERE category = :category ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
            results = mysql.execute(sql, {"category": category, "limit": limit, "offset": offset})
        else:
            sql = "SELECT * FROM course ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
            results = mysql.execute(sql, {"limit": limit, "offset": offset})

        return jsonify({
            "success": True,
            "data": {"courses": results, "count": len(results)}
        })

    except Exception as e:
        logger.error(f"获取课程列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@learning_bp.route('/courses/<course_id>', methods=['GET'])
def get_course(course_id: str):
    """获取课程详情"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM course WHERE id = :course_id"
        results = mysql.execute(sql, {"course_id": course_id})

        if not results:
            return jsonify({"success": False, "error": "课程不存在"}), 404

        return jsonify({"success": True, "data": results[0]})

    except Exception as e:
        logger.error(f"获取课程详情失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@learning_bp.route('/learning/progress/<user_id>', methods=['GET'])
def get_learning_progress(user_id: str):
    """获取学习进度"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM learning_progress WHERE user_id = :user_id"
        results = mysql.execute(sql, {"user_id": user_id})

        return jsonify({"success": True, "data": results})

    except Exception as e:
        logger.error(f"获取学习进度失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@learning_bp.route('/learning/progress', methods=['POST'])
def update_progress():
    """更新学习进度"""
    try:
        data = request.get_json()
        mysql = get_mysql_client()

        sql = """
        INSERT INTO learning_progress
        (user_id, course_id, chapter_id, progress, completed, updated_at)
        VALUES (:user_id, :course_id, :chapter_id, :progress, :completed, NOW())
        ON DUPLICATE KEY UPDATE progress = :progress2, completed = :completed2, updated_at = NOW()
        """

        mysql.execute_write(sql, {
            "user_id": data['user_id'],
            "course_id": data['course_id'],
            "chapter_id": data['chapter_id'],
            "progress": data['progress'],
            "completed": data.get('completed', False),
            "progress2": data['progress'],
            "completed2": data.get('completed', False)
        })

        return jsonify({"success": True, "data": {"message": "进度已更新"}})

    except Exception as e:
        logger.error(f"更新学习进度失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@learning_bp.route('/learning/report/<user_id>', methods=['GET'])
def get_learning_report(user_id: str):
    """获取学习报告"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            SUM(progress * chapter_duration) as total_study_time,
            COUNT(DISTINCT course_id) as completed_courses,
            AVG(progress) as average_progress
        FROM learning_progress lp
        JOIN chapter ch ON lp.chapter_id = ch.id
        WHERE lp.user_id = :user_id
        """
        results = mysql.execute(sql, {"user_id": user_id})

        return jsonify({
            "success": True,
            "data": results[0] if results else {}
        })

    except Exception as e:
        logger.error(f"获取学习报告失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
