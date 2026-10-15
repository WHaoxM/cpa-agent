"""Read-only, owner-scoped projection of the current student workbench.

This does not change L0/L2 task states or the L3/L4 scoring gate.  In
particular an L2 partial result is a readable result with unknown dimensions,
not a published four-dimensional score.
"""

import json

from .pipeline import require_confirmed_profile
from .resume_diagnostic_report import ResumeReportStale, read_resume_diagnostic
from .student_self_report import portrait_scoring_current, revision as self_report_revision
from .agent_runtime.pipeline_reference import resolve_pipeline_task_ref


def _payload(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


class WorkbenchSourceInvalid(ValueError):
    """A stored result cannot safely be presented as the current version."""


_DIMENSIONS = (("D1", "D1_basic"), ("D2", "D2_skill"),
               ("D3", "D3_quality"), ("D4", "D4_potential"))


def _portrait_state(mysql, student_id: str, profile: dict, confirmation_id: str) -> dict:
    rows = mysql.fetch_all(
        "SELECT task_id,student_id,pipeline,status,result_json FROM pipeline_task "
        "WHERE student_id=:sid AND pipeline='L2_generate_portrait' "
        "AND (JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.source_confirmation_task_id'))=:tid "
        "OR JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.source_confirmation_task_id'))=:tid) "
        "ORDER BY id DESC LIMIT 1", {"sid": student_id, "tid": confirmation_id})
    empty = {"state": "not_started", "task_id": None, "snapshot_id": None, "missing_fields": [],
             "dimensions": {key: None for key, _ in _DIMENSIONS},
             "scoring_eligible": False, "next_action": "generate_portrait"}
    if not rows:
        return empty
    row = rows[0]
    result = _payload(row.get("result_json"))
    if not isinstance(result, dict):
        raise WorkbenchSourceInvalid("画像任务来源未通过校验")
    if result.get("task_id") not in (None, row["task_id"]) or result.get("student_id") not in (None, student_id):
        raise WorkbenchSourceInvalid("画像任务归属未通过校验")
    status = row["status"]
    if status in {"pending", "running"}:
        return {**empty, "state": "processing", "task_id": row["task_id"],
                "next_action": "wait_for_portrait"}
    if status == "failed":
        return {**empty, "state": "failed", "task_id": row["task_id"],
                "next_action": "retry_portrait"}
    if result.get("source_confirmation_task_id") != confirmation_id:
        raise WorkbenchSourceInvalid("画像任务来源未通过校验")
    if status not in {"partial", "completed"} or result.get("status") != status:
        raise WorkbenchSourceInvalid("画像任务状态未通过校验")
    if status == "partial" and (result.get("resource_type") != "pipeline_task"
                                or result.get("resource_id") != row["task_id"]
                                or result.get("portrait_id") is not None):
        raise WorkbenchSourceInvalid("部分画像来源未通过校验")
    portrait = result.get("portrait") if status == "completed" else result.get("portrait_candidate")
    if not isinstance(portrait, dict) or not portrait or not portrait_scoring_current(portrait):
        return {**empty, "state": "stale", "task_id": row["task_id"],
                "next_action": "regenerate_portrait"}
    if (portrait.get("source_confirmation_task_id") != confirmation_id
            or portrait.get("source_self_report_revision", 0) != self_report_revision(profile)):
        return {**empty, "state": "stale", "task_id": row["task_id"],
                "next_action": "regenerate_portrait"}
    from .school_tier_review import portrait_review_current
    if not portrait_review_current(mysql, student_id=student_id,
                                   confirmation_task_id=confirmation_id, portrait=portrait):
        return {**empty, "state": "stale", "task_id": row["task_id"],
                "next_action": "regenerate_portrait"}
    if status == "completed":
        try:
            resolve_pipeline_task_ref(mysql, student_id=student_id, task_id=row["task_id"])
        except (LookupError, ValueError) as exc:
            raise WorkbenchSourceInvalid("画像成果未通过权威记录校验") from exc
    from .student_portrait_snapshot import PortraitSnapshotInvalid, read_snapshot_for_task
    try:
        snapshot = read_snapshot_for_task(mysql, student_id=student_id,
                                          source_task_id=row["task_id"])
    except PortraitSnapshotInvalid as exc:
        raise WorkbenchSourceInvalid("画像快照未通过权威记录校验") from exc
    if (snapshot and (snapshot["snapshot_id"] != result.get("snapshot_id")
                      or snapshot["portrait"] != portrait)
            or not snapshot and result.get("snapshot_id") is not None):
        raise WorkbenchSourceInvalid("画像快照与任务结果不一致")
    dimensions = {}
    for key, source_key in _DIMENSIONS:
        item = portrait.get(source_key)
        score = item.get("dimension_score") if isinstance(item, dict) else None
        dimensions[key] = score if type(score) in (int, float) and 0 <= score <= 100 else None
    if status == "partial" and all(score is not None for score in dimensions.values()):
        raise WorkbenchSourceInvalid("部分画像与维度状态不一致")
    if status == "completed" and any(score is None for score in dimensions.values()):
        raise WorkbenchSourceInvalid("已完成画像缺少可核验的维度分数")
    return {"state": "ready_scored" if status == "completed" else "ready_with_unknowns",
            "task_id": row["task_id"], "portrait_id": result.get("portrait_id") if status == "completed" else None,
            "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
            "source_confirmation_task_id": confirmation_id,
            "dimensions": dimensions,
            "missing_fields": result.get("missing_fields") or [],
            "scoring_eligible": status == "completed",
            "next_action": result.get("next_action"), "summary": result.get("summary")}


def _diagnostic_state(mysql, student_id: str, confirmation_id: str) -> dict:
    rows = mysql.fetch_all(
        "SELECT task_id,status FROM pipeline_task "
        "WHERE student_id=:sid AND pipeline='R1_resume_diagnostic' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.source_confirmation_task_id'))=:tid "
        "ORDER BY id DESC LIMIT 1", {"sid": student_id, "tid": confirmation_id})
    job = rows[0] if rows else None
    try:
        report = read_resume_diagnostic(mysql, student_id=student_id)
        return {"state": "ready" if report["status"] == "completed" else "partial",
                "report_id": report["report_id"], "task_id": job["task_id"] if job else None,
                "source_confirmation_task_id": confirmation_id,
                "missing_sections": report.get("missing_sections") or []}
    except ResumeReportStale:
        return {"state": "stale", "report_id": None,
                "next_action": "regenerate_resume_diagnostic"}
    except LookupError:
        if job:
            if job["status"] in {"pending", "running"}:
                return {"state": "processing", "report_id": None,
                        "task_id": job["task_id"], "next_action": "wait_for_resume_diagnostic"}
            if job["status"] == "failed":
                return {"state": "failed", "report_id": None,
                        "task_id": job["task_id"], "next_action": "retry_resume_diagnostic"}
        return {"state": "not_started", "report_id": None}


def read_student_workbench(mysql, *, student_id: str) -> dict:
    """Compose current task and independent artifacts from authoritative rows."""
    latest = mysql.fetch_all(
        "SELECT task_id,student_id,pipeline,status,result_json FROM pipeline_task "
        "WHERE student_id=:sid AND pipeline='L0_parse_resume' ORDER BY id DESC LIMIT 1",
        {"sid": student_id})
    run = {"state": "not_started", "task_id": None}
    if latest:
        row = latest[0]
        status = row["status"]
        run = {"state": ("queued" if status == "pending" else "running" if status == "running"
                         else "waiting_for_user" if status in {"needs_input", "partial"}
                         else "settled" if status == "completed" else "failed"),
               "task_id": row["task_id"], "l0_status": status}
    rows = mysql.fetch_all("SELECT profile_json FROM student_profile WHERE student_id=:sid LIMIT 1",
                           {"sid": student_id})
    if not rows:
        return {"run": run, "source_confirmation_task_id": None,
                "based_on_previous_upload": False,
                "analysis_run": {"state": "not_started", "portrait_task_id": None,
                                 "diagnostic_task_id": None, "has_failures": False},
                "resume_diagnostic": {"state": "not_started", "report_id": None},
                "portrait": {"state": "not_started", "task_id": None,
                             "snapshot_id": None,
                             "dimensions": {key: None for key, _ in _DIMENSIONS},
                             "missing_fields": [], "scoring_eligible": False}}
    try:
        profile = require_confirmed_profile(mysql, student_id)
    except ValueError as exc:
        raise WorkbenchSourceInvalid("当前简历确认来源未通过校验") from exc
    confirmation_id = profile["_resume_confirmation"]["task_id"]
    based_on_previous_upload = bool(run["task_id"] and run["task_id"] != confirmation_id)
    portrait = _portrait_state(mysql, student_id, profile, confirmation_id)
    report_state = _diagnostic_state(mysql, student_id, confirmation_id)
    outcomes = {portrait["state"], report_state["state"]}
    if "processing" in outcomes:
        analysis_state = "running"
    elif outcomes == {"not_started"}:
        analysis_state = "not_started"
    else:
        analysis_state = "settled"
    analysis_run = {"state": analysis_state,
                    "portrait_task_id": portrait.get("task_id"),
                    "diagnostic_task_id": report_state.get("task_id"),
                    "has_failures": bool(outcomes & {"failed", "stale"})}
    return {"run": run, "source_confirmation_task_id": confirmation_id,
            "based_on_previous_upload": based_on_previous_upload,
            "analysis_run": analysis_run,
            "resume_diagnostic": report_state, "portrait": portrait}
