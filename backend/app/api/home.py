"""Owner-scoped read-only projection for the student dashboard."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify

from . import home_bp
from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from ..config import Config
from ..services.db import get_mysql_client


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return MySQL DATETIME week boundaries in Asia/Shanghai local time."""
    local_now = now or datetime.now(SHANGHAI)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=SHANGHAI)
    else:
        local_now = local_now.astimezone(SHANGHAI)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    next_monday = monday + timedelta(days=7)
    return datetime.combine(monday, time.min), datetime.combine(next_monday, time.min)


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, time.min).isoformat()
    if value is None:
        return None
    return str(value)


def _sort_time(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.min
    return datetime.min


def _portrait_projection(mysql, student_id: str) -> dict:
    # Workbench validates the current confirmed-resume lineage, scoring version,
    # review state and snapshot against authoritative persisted records.
    from ..services.student_workbench import read_student_workbench

    workbench = read_student_workbench(mysql, student_id=student_id)
    portrait = workbench.get("portrait") or {}
    dimensions = portrait.get("dimensions") or {}
    state = portrait.get("state", "not_started")
    current_states = {"ready_scored", "ready_with_unknowns"}
    if state not in current_states:
        return {
            "state": state,
            "completeness": None,
            "dimensions": {key: None for key in ("D1", "D2", "D3", "D4")},
            "scoring_eligible": False,
        }, workbench.get("source_confirmation_task_id")

    clean_dimensions = {}
    for key in ("D1", "D2", "D3", "D4"):
        value = dimensions.get(key)
        clean_dimensions[key] = value if type(value) in (int, float) and 0 <= value <= 100 else None
    available = sum(value is not None for value in clean_dimensions.values())
    return {
        "state": state,
        "completeness": round(available / 4 * 100),
        "dimensions": clean_dimensions,
        "scoring_eligible": bool(portrait.get("scoring_eligible")),
    }, workbench.get("source_confirmation_task_id")


def _completed_courses(mysql, student_id: str) -> list[dict]:
    return mysql.execute(
        """
        SELECT c.course_id, c.title, MAX(lp.updated_at) AS occurred_at
        FROM course c
        JOIN chapter ch ON ch.course_id = c.course_id
        JOIN learning_progress lp
          ON lp.user_id = :student_id
         AND lp.course_id = c.course_id
         AND lp.chapter_id = CAST(ch.id AS CHAR)
         AND lp.completed = 1
        GROUP BY c.course_id, c.title
        HAVING COUNT(DISTINCT ch.id) = (
            SELECT COUNT(*) FROM chapter all_chapters
            WHERE all_chapters.course_id = c.course_id
        )
        ORDER BY occurred_at DESC
        """,
        {"student_id": student_id},
    )


def _weekly_completed_chapters(mysql, student_id: str, week_start: datetime,
                               week_end: datetime) -> int:
    rows = mysql.execute(
        """
        SELECT COUNT(*) AS total
        FROM learning_progress lp
        JOIN chapter ch
          ON ch.id = CAST(lp.chapter_id AS UNSIGNED)
         AND ch.course_id = lp.course_id
        WHERE lp.user_id = :student_id
          AND lp.completed = 1
          AND lp.updated_at >= :week_start
          AND lp.updated_at < :week_end
        """,
        {"student_id": student_id, "week_start": week_start, "week_end": week_end},
    )
    return int((rows[0] or {}).get("total") or 0) if rows else 0


def _completed_reports(mysql, student_id: str) -> list[dict]:
    rows = mysql.execute(
        """
        SELECT report_id, title, status, created_at, updated_at
        FROM career_report
        WHERE student_id = :student_id
        ORDER BY created_at DESC
        """,
        {"student_id": student_id},
    )
    if Config.DEMO_MODE:
        return [row for row in rows if row.get("status") == "completed"]

    from ..services.agent_runtime.match_report_resources import read_match_report_resource

    verified = []
    for report in rows:
        record = read_match_report_resource(
            mysql,
            student_id=student_id,
            uri=f"career://me/reports/{report['report_id']}",
        )
        if record.get("business_artifact_verified") is True and record.get("status") == "completed":
            verified.append(report)
    return verified


@home_bp.route("/dashboard", methods=["GET"])
def get_student_dashboard():
    """Read the current student's verified dashboard evidence; never accepts a student id."""
    try:
        scope = resolve_request_scope()
        mysql = get_mysql_client()
        week_start, week_end = _week_bounds()

        courses = _completed_courses(mysql, scope.student_id)
        weekly_chapters = _weekly_completed_chapters(mysql, scope.student_id, week_start, week_end)
        try:
            portrait, confirmation_task_id = _portrait_projection(mysql, scope.student_id)
        except Exception as error:
            from ..services.student_workbench import WorkbenchSourceInvalid
            if not isinstance(error, WorkbenchSourceInvalid):
                raise
            # A broken/stale profile source must not erase otherwise valid
            # learning evidence. Keep the portrait explicitly unavailable and
            # let the page guide the student to resolve the source.
            portrait = {
                "state": "source_invalid",
                "completeness": None,
                "dimensions": {key: None for key in ("D1", "D2", "D3", "D4")},
                "scoring_eligible": False,
                "error": str(error),
            }
            confirmation_task_id = None
        reports = _completed_reports(mysql, scope.student_id)

        activities = []
        for row in courses:
            activities.append({
                "id": f"course:{row['course_id']}",
                "type": "course_completed",
                "title": row.get("title") or "完成课程",
                "occurred_at": row.get("occurred_at"),
                "route": "/app/student/learning",
            })
        for row in reports:
            activities.append({
                "id": f"report:{row['report_id']}",
                "type": "career_report",
                "title": row.get("title") or "完成职业报告",
                "occurred_at": row.get("created_at") or row.get("updated_at"),
                "route": "/app/student/my-reports",
            })
        activities.sort(key=lambda item: _sort_time(item.get("occurred_at")), reverse=True)

        data = {
            "metrics": {
                "completed_courses": len(courses),
                "weekly_completed_chapters": weekly_chapters,
                "week_start": week_start.date().isoformat(),
            },
            "portrait": portrait,
            "source_confirmation_task_id": confirmation_task_id,
            "evidence": {"has_verified_completed_report": bool(reports)},
            "recent_activities": [
                {**item, "occurred_at": _iso(item.get("occurred_at"))}
                for item in activities[:5]
            ],
        }
        return jsonify({"success": True, "data": data})
    except ScopeError as error:
        return scope_error_response(error)
    except Exception as error:
        return jsonify(safe_error("读取学生首页", error)), 500
