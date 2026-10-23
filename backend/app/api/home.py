"""Owner-scoped read-only projection for the student dashboard."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, jsonify

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


def _portrait_projection(workbench: dict) -> tuple[dict, str | None]:
    # Workbench validates the current confirmed-resume lineage, scoring version,
    # review state and snapshot against authoritative persisted records.
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


def _resume_projection(workbench: dict, draft: dict) -> dict:
    """A small read projection, not another task lifecycle or submission ledger."""
    run = workbench.get("run") or {}
    portrait = workbench.get("portrait") or {}
    diagnostic = workbench.get("resume_diagnostic") or {}
    analysis = workbench.get("analysis_run") or {}
    confirmation = workbench.get("source_confirmation_task_id")
    portrait_ready = portrait.get("state") in {"ready_scored", "ready_with_unknowns"}
    diagnostic_ready = diagnostic.get("state") in {"ready", "partial"}
    state = workbench.get("read_state")
    if not state:
        state = {"waiting_for_user": "waiting_for_user", "queued": "queued",
                 "running": "parsing", "failed": "upload_failed"}.get(run.get("state"))
        if not state and confirmation:
            state = ("analysis_running" if analysis.get("state") == "running" else
                     "results_ready" if portrait_ready or diagnostic_ready else
                     "analysis_failed" if analysis.get("has_failures") else "confirmed")
        if not state:
            state = ("draft_only" if draft.get("state") == "saved" else
                     "unavailable" if draft.get("state") == "unavailable" else "empty")
    return {"state": state, "task_id": run.get("task_id"),
            "confirmation_task_id": confirmation,
            "based_on_previous_upload": bool(workbench.get("based_on_previous_upload")),
            "portrait_ready": portrait_ready, "diagnostic_ready": diagnostic_ready,
            "diagnostic_report_id": diagnostic.get("report_id") if diagnostic_ready else None,
            "has_analysis_failures": bool(analysis.get("has_failures")), "draft": draft}


def _draft_projection(mysql, student_id: str) -> dict:
    from ..services.resume_draft import read_draft

    try:
        draft = read_draft(mysql, student_id)
        saved = bool(draft.get("revision") and (draft.get("text") or "").strip())
        return {"state": "saved" if saved else "empty", "revision": draft.get("revision", 0),
                "updated_at": draft.get("updated_at")}
    except Exception:
        current_app.logger.warning("首页草稿摘要暂不可读取", exc_info=True)
        return {"state": "unavailable", "revision": None, "updated_at": None}


def _resume_activities(mysql, student_id: str, workbench: dict) -> list[dict]:
    """Only current, verified sources; timestamps are persisted, not inferred."""
    confirmation = workbench.get("source_confirmation_task_id")
    if not confirmation:
        return []
    params = {"student_id": student_id, "task_id": confirmation}
    activities = []
    row = mysql.fetch_one(
        "SELECT JSON_UNQUOTE(JSON_EXTRACT(profile_json, '$._resume_confirmation.confirmed_at')) AS confirmed_at "
        "FROM student_profile WHERE student_id=:student_id "
        "AND JSON_UNQUOTE(JSON_EXTRACT(profile_json, '$._resume_confirmation.task_id'))=:task_id LIMIT 1", params)
    route = "/app/student/career-navigation"
    if row and _sort_time(row.get("confirmed_at")) != datetime.min:
        activities.append({"id": f"resume:{confirmation}", "type": "resume_confirmed",
                           "title": "本人已确认简历", "occurred_at": row["confirmed_at"],
                           "route": route, "task_id": confirmation})
    diagnostic = workbench.get("resume_diagnostic") or {}
    if diagnostic.get("state") in {"ready", "partial"} and diagnostic.get("report_id"):
        report = mysql.fetch_one(
            "SELECT report_id,created_at FROM resume_diagnostic_report WHERE student_id=:student_id "
            "AND source_confirmation_task_id=:task_id AND report_id=:report_id LIMIT 1",
            {**params, "report_id": diagnostic["report_id"]})
        if report and _sort_time(report.get("created_at")) != datetime.min:
            activities.append({"id": f"diagnostic:{report['report_id']}", "type": "resume_diagnostic",
                               "title": "简历诊断已有可读内容", "occurred_at": report["created_at"],
                               "route": route, "task_id": confirmation, "report_id": report["report_id"],
                               "partial": diagnostic["state"] == "partial"})
    return activities


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


def _learning_continuation(mysql, student_id: str) -> dict:
    """Resume explicit personal records, never infer viewing history or ability."""
    try:
        rows = mysql.execute(
            """
            SELECT c.course_id, c.title, COUNT(DISTINCT ch.id) AS chapter_count,
                   COUNT(DISTINCT CASE WHEN lp.completed = 1 AND lp.progress = 100
                                       THEN ch.id END) AS completed_chapters,
                   MAX(lp.updated_at) AS updated_at
            FROM course c
            JOIN chapter ch ON ch.course_id = c.course_id
            LEFT JOIN learning_progress lp
              ON lp.user_id = :student_id AND lp.course_id = c.course_id
             AND lp.chapter_id = CAST(ch.id AS CHAR)
            GROUP BY c.course_id, c.title
            HAVING COUNT(lp.chapter_id) > 0 AND completed_chapters < chapter_count
            ORDER BY updated_at DESC, c.course_id ASC
            LIMIT 3
            """, {"student_id": student_id})
        courses = [{"course_id": row["course_id"], "title": row.get("title"),
                    "chapter_count": int(row["chapter_count"]),
                    "completed_chapters": int(row["completed_chapters"]),
                    "updated_at": _iso(row.get("updated_at"))} for row in rows]
        return {"state": "ready" if courses else "empty", "courses": courses}
    except Exception:
        current_app.logger.warning("首页课程续接暂不可读取", exc_info=True)
        return {"state": "unavailable", "courses": []}


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
        from ..services.student_workbench import WorkbenchSourceInvalid, read_student_workbench
        try:
            workbench = read_student_workbench(mysql, student_id=scope.student_id)
        except Exception as error:
            state = "source_invalid" if isinstance(error, WorkbenchSourceInvalid) else "unavailable"
            workbench = {"read_state": state, "portrait": {"state": state}}
            current_app.logger.warning("首页简历摘要暂不可读取", exc_info=True)
        portrait, confirmation_task_id = _portrait_projection(workbench)
        if workbench.get("read_state") == "source_invalid":
            portrait["error"] = "当前简历确认来源未通过校验"
        continuation = _resume_projection(workbench, _draft_projection(mysql, scope.student_id))
        reports = _completed_reports(mysql, scope.student_id)

        activities = []
        for row in courses:
            activities.append({
                "id": f"course:{row['course_id']}",
                "type": "course_completed",
                "title": row.get("title") or "完成课程",
                "occurred_at": row.get("occurred_at"),
                "route": "/app/student/learning",
                "course_id": row["course_id"],
            })
        for row in reports:
            activities.append({
                "id": f"report:{row['report_id']}",
                "type": "career_report",
                "title": row.get("title") or "完成职业报告",
                "occurred_at": row.get("created_at") or row.get("updated_at"),
                "route": "/app/student/my-reports",
                "report_id": row["report_id"],
            })
        try:
            activities.extend(_resume_activities(mysql, scope.student_id, workbench))
            activity_state = "partial" if workbench.get("read_state") else "ready"
        except Exception:
            activity_state = "partial"
            current_app.logger.warning("首页简历进展暂不可读取", exc_info=True)
        activities.sort(key=lambda item: _sort_time(item.get("occurred_at")), reverse=True)

        data = {
            "metrics": {
                "completed_courses": len(courses),
                "weekly_completed_chapters": weekly_chapters,
                "week_start": week_start.date().isoformat(),
            },
            "portrait": portrait,
            "source_confirmation_task_id": confirmation_task_id,
            "resume_continuation": continuation,
            "learning_continuation": _learning_continuation(mysql, scope.student_id),
            "recent_activities_state": activity_state,
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
