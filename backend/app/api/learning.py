"""
Learning API - 学习相关 API
"""

import uuid
from flask import request, jsonify

from . import learning_bp
from ._helpers import clamp_pagination, parse_json_field, safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.learning')

_CHAPTER_COLS_READY = False


def _ensure_chapter_video_columns() -> None:
    """为已有库补 video_url / video_provider 列（无业务文案）。"""
    global _CHAPTER_COLS_READY
    if _CHAPTER_COLS_READY:
        return
    mysql = get_mysql_client()
    cols = {
        r["COLUMN_NAME"]
        for r in mysql.execute(
            """
            SELECT COLUMN_NAME FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapter'
            """
        )
    }
    if "video_url" not in cols:
        mysql.execute_write(
            "ALTER TABLE chapter ADD COLUMN video_url VARCHAR(500) DEFAULT NULL"
        )
    if "video_provider" not in cols:
        mysql.execute_write(
            "ALTER TABLE chapter ADD COLUMN video_provider VARCHAR(64) DEFAULT NULL"
        )
    _CHAPTER_COLS_READY = True


@learning_bp.route('/courses', methods=['GET'])
def list_courses():
    """获取课程列表"""
    try:
        mysql = get_mysql_client()
        category = request.args.get('category')
        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

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
        return jsonify(safe_error("获取课程列表", e)), 500


@learning_bp.route('/courses/<course_id>', methods=['GET'])
def get_course(course_id: str):
    """获取课程详情（含章节 video_url）"""
    try:
        _ensure_chapter_video_columns()
        mysql = get_mysql_client()
        sql = "SELECT * FROM course WHERE course_id = :course_id"
        results = mysql.execute(sql, {"course_id": course_id})

        if not results:
            return jsonify({"success": False, "error": "课程不存在"}), 404

        course = results[0]
        chapters = mysql.execute(
            """
            SELECT id, course_id, title, sequence, duration_hours, content_summary,
                   video_url, video_provider, created_at
            FROM chapter
            WHERE course_id = :course_id
            ORDER BY sequence ASC
            """,
            {"course_id": course_id},
        )
        course["chapters"] = chapters
        return jsonify({"success": True, "data": course})

    except Exception as e:
        return jsonify(safe_error("获取课程详情", e)), 500


@learning_bp.route('/learning/progress/<user_id>', methods=['GET'])
def get_learning_progress(user_id: str):
    """获取学习进度"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM learning_progress WHERE user_id = :user_id"
        results = mysql.execute(sql, {"user_id": user_id})

        return jsonify({"success": True, "data": results})

    except Exception as e:
        return jsonify(safe_error("获取学习进度", e)), 500


@learning_bp.route('/learning/progress', methods=['POST'])
def update_progress():
    """更新学习进度"""
    try:
        data = request.get_json() or {}
        user_id = data.get('user_id')
        course_id = data.get('course_id')
        chapter_id = data.get('chapter_id')
        progress = data.get('progress')
        if not user_id or not course_id or chapter_id is None or progress is None:
            return jsonify({"success": False, "error": "user_id, course_id, chapter_id, progress 必填"}), 400
        mysql = get_mysql_client()

        sql = """
        INSERT INTO learning_progress
        (user_id, course_id, chapter_id, progress, completed, updated_at)
        VALUES (:user_id, :course_id, :chapter_id, :progress, :completed, NOW())
        ON DUPLICATE KEY UPDATE progress = :progress2, completed = :completed2, updated_at = NOW()
        """

        mysql.execute_write(sql, {
            "user_id": user_id,
            "course_id": course_id,
            "chapter_id": chapter_id,
            "progress": progress,
            "completed": data.get('completed', False),
            "progress2": progress,
            "completed2": data.get('completed', False)
        })

        return jsonify({"success": True, "data": {"message": "进度已更新"}})

    except Exception as e:
        return jsonify(safe_error("更新学习进度", e)), 500


@learning_bp.route('/learning/report/<user_id>', methods=['GET'])
def get_learning_report(user_id: str):
    """获取学习报告"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            SUM(progress * ch.duration_hours) as total_study_time,
            COUNT(DISTINCT lp.course_id) as completed_courses,
            AVG(lp.progress) as average_progress
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
        return jsonify(safe_error("获取学习报告", e)), 500


@learning_bp.route('/recommend', methods=['GET'])
def recommend_courses():
    """
    基于弱技能或目标岗位推荐课程

    Query params:
        weak_skills: 弱技能列表，逗号分隔 (可选)
        target_role: 目标岗位 (可选)
        student_id: 学生ID，用于查询历史画像 (可选)
        limit: 返回数量，默认10

    推荐逻辑:
        1. 优先根据 weak_skills 匹配课程标签
        2. 其次根据 target_role 匹配岗位相关课程
        3. 最后根据技能分类树推荐前置课程
    """
    try:
        weak_skills_str = request.args.get('weak_skills', '')
        target_role = request.args.get('target_role', '')
        student_id = request.args.get('student_id', '')
        limit, _ = clamp_pagination(request.args.get('limit', type=int), None, default_limit=10)

        mysql = get_mysql_client()

        weak_skills = [s.strip() for s in weak_skills_str.split(',') if s.strip()]
        recommended = []

        # 策略1: 根据弱技能精确匹配课程
        if weak_skills:
            conditions = []
            params = {}
            for i, s in enumerate(weak_skills):
                conditions.append(f"(title LIKE :skill{i} OR description LIKE :skill{i} OR category LIKE :skill{i})")
                params[f"skill{i}"] = f'%{s}%'
            params["limit"] = limit
            sql = f"""
            SELECT * FROM course
            WHERE {' OR '.join(conditions)}
            ORDER BY difficulty ASC, created_at DESC
            LIMIT :limit
            """
            results = mysql.execute(sql, params)
            for r in results:
                r["match_reason"] = f"补强弱技能"
                recommended.append(r)

        # 策略2: 根据目标岗位匹配课程
        if target_role and len(recommended) < limit:
            remaining = limit - len(recommended)
            sql = """
            SELECT c.* FROM course c
            JOIN learning_path_template lpt ON c.category = lpt.target_role
            WHERE lpt.target_role LIKE :role
            ORDER BY c.difficulty ASC
            LIMIT :limit
            """
            results = mysql.execute(sql, {"role": f"%{target_role}%", "limit": remaining})
            for r in results:
                r["match_reason"] = f"目标岗位'{target_role}'推荐"
                if r["id"] not in [c["id"] for c in recommended]:
                    recommended.append(r)

        # 策略3: 根据学生历史画像补充推荐
        if student_id and len(recommended) < limit:
            remaining = limit - len(recommended)
            # 查询学生最近的自评或画像
            sql = """
            SELECT weak_skills FROM student_self_assessment
            WHERE student_id = :student_id
            ORDER BY created_at DESC LIMIT 1
            """
            result = mysql.execute(sql, {"student_id": student_id})
            if result and result[0]["weak_skills"]:
                stored_weak = parse_json_field(result[0]["weak_skills"], default=[])
                if stored_weak:
                    conditions = []
                    params = {}
                    for i, s in enumerate(stored_weak[:3]):
                        conditions.append(f"(title LIKE :skill{i} OR description LIKE :skill{i} OR category LIKE :skill{i})")
                        params[f"skill{i}"] = f'%{s}%'
                    params["limit"] = remaining
                    sql = f"""
                    SELECT * FROM course
                    WHERE {' OR '.join(conditions)}
                    LIMIT :limit
                    """
                    results = mysql.execute(sql, params)
                    for r in results:
                        r["match_reason"] = "基于历史弱技能"
                        if r["id"] not in [c["id"] for c in recommended]:
                            recommended.append(r)

        # 兜底: 返回热门课程
        if not recommended:
            sql = "SELECT * FROM course ORDER BY created_at DESC LIMIT :limit"
            results = mysql.execute(sql, {"limit": limit})
            for r in results:
                r["match_reason"] = "热门课程"
                recommended.append(r)

        return jsonify({
            "success": True,
            "data": {
                "courses": recommended[:limit],
                "total": len(recommended),
                "query": {
                    "weak_skills": weak_skills,
                    "target_role": target_role,
                    "student_id": student_id
                }
            }
        })

    except Exception as e:
        return jsonify(safe_error("课程推荐", e)), 500


@learning_bp.route('/course-system-graph', methods=['GET'])
def course_system_graph():
    """课程体系富图（Neo4j）；无数据 404。"""
    try:
        from ..services.ability_graph_service import get_course_system_graph

        role = (request.args.get("role") or "").strip()
        if not role:
            return jsonify({"success": False, "error": "role required"}), 400
        data = get_course_system_graph(role)
        if not data or not data.get("nodes"):
            return jsonify({
                "success": False,
                "error": f"course system graph empty for role={role}",
                "data": None,
            }), 404
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify(safe_error("get course system graph", e)), 500
