"""
Learning API - 学习相关 API
"""

import uuid
from flask import request, jsonify

from . import learning_bp
from ._helpers import (
    ScopeError, clamp_pagination, parse_json_field, require_student_path,
    resolve_request_scope, safe_error, scope_error_response,
)
from ..services.db import get_mysql_client
from ..services.course_catalog import category_label, category_options
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
        keyword = (request.args.get('keyword') or '').strip()
        if len(keyword) > 200:
            return jsonify({"success": False, "error": "课程标题关键词不能超过 200 字"}), 400
        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

        filters, params = [], {}
        if category == '__uncategorized__':
            filters.append("(category IS NULL OR category = '')")
        elif category == '__needs_review__':
            raw_categories = mysql.execute('SELECT DISTINCT category FROM course')
            pending = [r['category'] for r in raw_categories if category_label(r['category']) == '分类待整理']
            names = []
            for index, value in enumerate(pending):
                key = f'pending_category_{index}'
                params[key] = value
                names.append(':' + key)
            filters.append('category IN (' + ','.join(names) + ')' if names else '1=0')
        elif category:
            filters.append("category = :category")
            params["category"] = category
        if keyword:
            # Literal substring search: user '%'/'_' are not wildcard operators.
            filters.append("LOCATE(:keyword, title) > 0")
            params["keyword"] = keyword
        where = " WHERE " + " AND ".join(filters) if filters else ""
        total = mysql.execute("SELECT COUNT(*) AS total FROM course" + where, params)[0]["total"]
        results = mysql.execute(
            "SELECT course.*, (SELECT COUNT(*) FROM chapter ch WHERE ch.course_id=course.course_id) AS chapter_count FROM course" + where + " ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset})
        for row in results:
            row['category_label'] = category_label(row.get('category'))
        categories = mysql.execute(
            "SELECT category, COUNT(*) AS course_count FROM course GROUP BY category ORDER BY category")
        options = category_options(categories)

        return jsonify({
            "success": True,
            "data": {"courses": results, "count": len(results), "total": int(total),
                     "categories": [row["category"] for row in categories if row['category']],
                     "category_options": options,
                     "catalog_summary": {"total": sum(row['course_count'] for row in options), "scope": "all_courses"},
                     "limit": limit, "offset": offset}
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
        course['category_label'] = category_label(course.get('category'))
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
        require_student_path(user_id, resolve_request_scope())
        mysql = get_mysql_client()
        sql = "SELECT * FROM learning_progress WHERE user_id = :user_id"
        results = mysql.execute(sql, {"user_id": user_id})

        return jsonify({"success": True, "data": results})

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取学习进度", e)), 500


@learning_bp.route('/learning/progress', methods=['POST'])
def update_progress():
    """更新学习进度"""
    try:
        user_id = resolve_request_scope().student_id
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
        course_id = data.get('course_id')
        chapter_id = data.get('chapter_id')
        progress = data.get('progress')
        if not course_id or chapter_id is None or progress is None:
            return jsonify({"success": False, "error": "course_id, chapter_id, progress 必填"}), 400
        if isinstance(progress, bool):
            return jsonify({"success": False, "error": "progress 必须是 0 至 100 的数值"}), 400
        try:
            progress = float(progress)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "progress 必须是 0 至 100 的数值"}), 400
        if not 0 <= progress <= 100:
            return jsonify({"success": False, "error": "progress 必须是 0 至 100 的数值"}), 400
        completed = data.get('completed', False)
        if not isinstance(completed, bool):
            return jsonify({"success": False, "error": "completed 必须是布尔值"}), 400
        mysql = get_mysql_client()

        # learning_progress stores chapter.id as text.  Verify the authoritative
        # course/chapter pair before writing so the page API and Agent resource
        # cannot create an orphan record that later masquerades as course-backed
        # progress.  The owner still comes exclusively from RequestScope.
        source = mysql.execute(
            "SELECT c.course_id, ch.id AS chapter_id "
            "FROM course c JOIN chapter ch ON ch.course_id=c.course_id "
            "WHERE c.course_id=:course_id AND ch.id=:chapter_id LIMIT 1",
            {"course_id": str(course_id), "chapter_id": str(chapter_id)},
        )
        if not source:
            return jsonify({
                "success": False,
                "status": "source_not_found",
                "error": "课程或章节不存在，或章节不属于该课程",
            }), 404

        sql = """
        INSERT INTO learning_progress
        (user_id, course_id, chapter_id, progress, completed, updated_at)
        VALUES (:user_id, :course_id, :chapter_id, :progress, :completed, NOW())
        ON DUPLICATE KEY UPDATE progress = :progress2, completed = :completed2, updated_at = NOW()
        """

        mysql.execute_write(sql, {
            "user_id": user_id,
            "course_id": str(course_id),
            "chapter_id": str(chapter_id),
            "progress": progress,
            "completed": completed,
            "progress2": progress,
            "completed2": completed
        })

        persisted = mysql.execute(
            "SELECT user_id, course_id, chapter_id, progress, completed "
            "FROM learning_progress WHERE user_id = :user_id AND chapter_id = :chapter_id LIMIT 1",
            {"user_id": user_id, "chapter_id": str(chapter_id)},
        )
        if (
            not persisted
            or str(persisted[0].get("user_id")) != str(user_id)
            or str(persisted[0].get("chapter_id")) != str(chapter_id)
            or str(persisted[0].get("course_id")) != str(course_id)
            or abs(float(persisted[0].get("progress")) - float(progress)) > 0.001
            or bool(persisted[0].get("completed")) != completed
        ):
            return jsonify({"success": False, "error": "学习进度写入后未能核验"}), 500

        return jsonify({"success": True, "data": {"message": "进度已更新", "progress": persisted[0]}})

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("更新学习进度", e)), 500


@learning_bp.route('/learning/report/<user_id>', methods=['GET'])
def get_learning_report(user_id: str):
    """获取学习报告"""
    try:
        require_student_path(user_id, resolve_request_scope())
        mysql = get_mysql_client()

        sql = """
        SELECT
            SUM((lp.progress / 100.0) * ch.duration_hours) as total_study_time,
            COUNT(DISTINCT CASE WHEN lp.completed = 1 THEN lp.course_id END) as completed_courses,
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

    except ScopeError as e:
        return scope_error_response(e)
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
        scope = resolve_request_scope()
        weak_skills_str = request.args.get('weak_skills', '')
        target_role = request.args.get('target_role', '')
        student_id = scope.student_id
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

    except ScopeError as e:
        return scope_error_response(e)
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
