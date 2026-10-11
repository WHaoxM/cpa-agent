"""Read-only resource adapter over the existing L3/L4 evidence repositories.

Reference: OpenCode 388406238bd5ca15564a762840a2362c3a45bd9c,
packages/opencode/src/mcp/index.ts (list/read resources); Codex
a06545b311fe01e51ce855c7aa5d8da21e9e7aaf,
codex-rs/core/src/tools/handlers/mcp_resource.rs (scoped resource reads).
This is a domain projection, not another MCP server or scoring workflow.
"""

from __future__ import annotations

import json
import re

from ..job_publication import content_sha256
from .tool_egress import redact_text

MATCHES_URI = "career://me/matches"
REPORTS_URI = "career://me/reports"
_RESOURCE = re.compile(r"^career://me/(matches|reports)(?:/([A-Za-z0-9_-]{1,128}))?$")
_KINDS = {
    "matches": ("match_result", "id", "L3_run_match", "岗位匹配记录"),
    "reports": ("career_report", "report_id", "L4_generate_report", "职业报告"),
}


def handles_uri(uri: str) -> bool:
    parsed = _RESOURCE.fullmatch(uri) if isinstance(uri, str) else None
    return bool(parsed and (parsed.group(1) != "matches" or parsed.group(2) is None
                           or re.fullmatch(r"[1-9][0-9]{0,19}", parsed.group(2))))


def _object(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def _safe(value):
    if isinstance(value, str):
        return redact_text(value, max_len=None)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    return value


def _paging(offset, limit):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3:
        raise ValueError("limit 必须为 1 至 3")


def _coverage(total, offset, returned):
    more = offset + returned < total
    return {"total": total, "offset": offset, "returned": returned,
            "complete": not more, "has_more": more,
            "next_offset": offset + returned if more else None}


def _count(mysql, sid, kind):
    table, _, _, _ = _KINDS[kind]
    rows = mysql.fetch_all(f"SELECT COUNT(*) AS total FROM {table} WHERE student_id=:sid", {"sid": sid})
    return int(rows[0]["total"]) if rows else 0


def _latest_task(mysql, sid, kind):
    rows = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
        "WHERE student_id=:sid AND pipeline=:pipeline ORDER BY id DESC LIMIT 1",
        {"sid": sid, "pipeline": _KINDS[kind][2]},
    )
    if not rows:
        return {"status": "not_available", "reason": "no_pipeline_task", "business_artifact_verified": False}
    row = rows[0]
    data = _object(row.get("result_json"))
    if (row.get("student_id") != sid or row.get("pipeline") != _KINDS[kind][2]
            or data.get("student_id") not in (None, sid)
            or data.get("task_id") not in (None, row.get("task_id"))
            or data.get("status") not in (None, row.get("status"))):
        raise ValueError("管线状态记录未通过归属或状态校验")
    # A task is progress, not proof of a saved business artifact.
    return _safe({"task_id": row.get("task_id"), "status": row.get("status"),
                  "reason": data.get("reason"), "next_action": data.get("next_action"),
                  "business_artifact_verified": False})


def list_match_report_resources(mysql, *, student_id: str) -> dict:
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    resources = []
    for kind, uri in (("matches", MATCHES_URI), ("reports", REPORTS_URI)):
        count = _count(mysql, student_id, kind)
        resources.append({"uri": uri, "title": _KINDS[kind][3], "status": "available" if count else "empty",
                          "count": count, "sections": ["overview", "items"],
                          "coverage": "catalog_only", "business_artifact_verified": False})
    return {"success": True, "status": "completed", "resources": resources}


def _verify_match(mysql, sid, match, match_id):
    """Reuse source gates; a stored number or a task flag alone is not proof."""
    if not match or match.get("student_id") != sid:
        raise ValueError("匹配快照缺失或归属不一致")
    tasks = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
        "WHERE task_id=:tid AND student_id=:sid LIMIT 1",
        {"tid": match.get("source_match_task_id"), "sid": sid},
    )
    task = tasks[0] if tasks else {}
    outcome = _object(task.get("result_json"))
    if (task.get("task_id") != match.get("source_match_task_id")
            or task.get("student_id") != sid or task.get("pipeline") != "L3_run_match"
            or task.get("status") not in {"partial", "completed"}
            or outcome.get("status") != task.get("status") or outcome.get("verified") is not True
            or outcome.get("student_id") != sid
            or str(outcome.get("match_id")) != str(match_id)
            or outcome.get("resource_type") != "match_result"
            or str(outcome.get("resource_id")) != str(match_id)
            or outcome.get("match_sha256") != content_sha256(match)):
        raise ValueError("匹配快照未通过任务、归属与内容摘要校验")
    from ..pipeline import require_authoritative_match_sources, require_confirmed_profile
    profile = require_confirmed_profile(mysql, sid)
    sources = require_authoritative_match_sources(mysql, sid, match.get("job_portrait_id"), profile)
    if match.get("sources") != sources["references"]:
        raise ValueError("匹配来源版本已变化，请重新匹配")
    return task["status"]


def _read_record(mysql, sid, kind, row):
    _, id_key, _, _ = _KINDS[kind]
    rid = str(row.get(id_key) or "")
    if row.get("student_id") != sid:
        raise PermissionError("成果记录不属于当前用户")
    if not rid or not handles_uri(f"career://me/{kind}/{rid}"):
        raise ValueError("成果记录 ID 无效")
    base = {"uri": f"career://me/{kind}/{rid}", "resource_id": rid,
            "resource_type": "match_result" if kind == "matches" else "career_report",
            "sections": ["overview", "missing_skills", "sources"]}
    from ..pipeline import PipelineSourceUnavailable
    try:
        report = {}
        if kind == "reports":
            report = _object(row.get("report_json"))
            match = _object(report.get("match_snapshot"))
            sources = _object(report.get("sources"))
            if (report.get("schema_version") != "evidence-snapshot-v1"
                    or report.get("report_id") != rid or report.get("student_id") != sid
                    or row.get("job_portrait_id") != match.get("job_portrait_id")
                    or report.get("status") != row.get("status")
                    or row.get("status") not in {"partial", "completed"}
                    or sources.get("match_sha256") != content_sha256(match)
                    or {k: v for k, v in sources.items() if k not in {"match_id", "match_sha256"}} != match.get("sources")):
                raise ValueError("报告未通过持久化证据快照校验")
            tasks = mysql.fetch_all(
                "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
                "WHERE student_id=:sid AND pipeline='L4_generate_report' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.report_id'))=:rid ORDER BY id DESC LIMIT 1",
                {"sid": sid, "rid": rid},
            )
            task = tasks[0] if tasks else {}
            outcome = _object(task.get("result_json"))
            if (task.get("student_id") != sid or task.get("pipeline") != "L4_generate_report"
                    or task.get("status") != report["status"] or outcome.get("status") != task.get("status")
                    or outcome.get("verified") is not True or outcome.get("student_id") != sid
                    or outcome.get("report_id") != rid or outcome.get("resource_id") != rid
                    or outcome.get("resource_type") != "career_report" or outcome.get("sources") != sources):
                raise ValueError("报告未通过权威任务回读")
            _verify_match(mysql, sid, match, sources.get("match_id"))
            status = report["status"]
        else:
            match = _object(row.get("result_json"))
            if match.get("job_portrait_id") != row.get("job_portrait_id"):
                raise ValueError("岗位 ID 与匹配记录不一致")
            status = _verify_match(mysql, sid, match, rid)
        if match.get("quality_status") != "validated" or report.get("missing_sections"):
            status = "partial"
        return {**base, "status": status, "business_artifact_verified": True,
                "version": content_sha256(report or match), "match": match, "report": report}
    except (ValueError, LookupError, PipelineSourceUnavailable) as error:
        # No stale scores or unverified legacy report text enters model context.
        return {**base, "status": "not_available", "business_artifact_verified": False,
                "reason": getattr(error, "reason", "unverified_or_stale_record"),
                "detail": str(error), "next_action": getattr(error, "next_action", "refresh_source_records")}


def _overview(record):
    public = {k: v for k, v in record.items() if k not in {"match", "report"}}
    if record["business_artifact_verified"]:
        match, report = record["match"], record["report"]
        public["data"] = {key: match.get(key) for key in (
            "job_portrait_id", "total_score", "d1_pass", "skill_coverage", "match_result",
            "match_level", "score_kind", "quality_status",
        )}
        public["data"]["position_name"] = _object(match.get("job_snapshot")).get("position_name")
        public["data"]["missing_sections"] = report.get("missing_sections", [])
        public["data"]["summary"] = report.get("summary")
        public["data"]["sources"] = report.get("sources") or match.get("sources")
        public["coverage"] = "overview_only"
        public["continuation"] = "规则估计不是录用概率；partial 不是完整职业报告。缺失技能和来源请分节读取。"
    return _safe(public)


def read_match_report_resource(mysql, *, student_id: str, uri: str, section: str = "overview",
                               offset: int = 0, limit: int = 3, dimension: str = "") -> dict:
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    parsed = _RESOURCE.fullmatch(uri) if isinstance(uri, str) else None
    if not parsed or not handles_uri(uri):
        raise ValueError("未知匹配/报告 URI")
    _paging(offset, limit)
    kind, rid = parsed.groups()
    table, id_key, _, _ = _KINDS[kind]
    if not rid:
        total = _count(mysql, student_id, kind)
        if section == "overview":
            if offset:
                raise ValueError("overview 不支持 offset")
            task = _latest_task(mysql, student_id, kind)
            return {"success": True, "uri": uri, "status": "available" if total else task["status"],
                    "coverage": "overview_only", "business_artifact_verified": False,
                    "data": {"count": total, "latest_task": task},
                    "continuation": "items 分页回读权威成果；任务完成标志本身不证明业务成果。"}
        if section != "items":
            raise ValueError("集合仅支持 overview/items")
        rows = mysql.fetch_all(f"SELECT * FROM {table} WHERE student_id=:sid ORDER BY id DESC LIMIT :limit OFFSET :offset",
                               {"sid": student_id, "limit": limit, "offset": offset})
        records = [_overview(_read_record(mysql, student_id, kind, row)) for row in rows]
        coverage = _coverage(total, offset, len(records))
        return {"success": True, "uri": uri, "status": "empty" if not total else "partial" if any(
            x["status"] != "completed" for x in records) else "completed", "data": records,
            "coverage": coverage, "continuation": "使用 next_offset 继续读取" if coverage["has_more"] else None}
    rows = mysql.fetch_all(f"SELECT * FROM {table} WHERE student_id=:sid AND {id_key}=:rid LIMIT 1",
                           {"sid": student_id, "rid": rid})
    if not rows:
        raise LookupError("成果不存在或不属于当前用户")
    record = _read_record(mysql, student_id, kind, rows[0])
    if section not in {"overview", "missing_skills", "sources"}:
        raise ValueError("成果仅支持 overview/missing_skills/sources")
    if section == "overview":
        if offset:
            raise ValueError("overview 不支持 offset")
        return {"success": True, **_overview(record)}
    if not record["business_artifact_verified"]:
        return {"success": True, **_overview(record)}
    match = record["match"]
    if section == "missing_skills":
        items = match.get("missing_skills") or []
        items = items if isinstance(items, list) else []
    else:
        job = _object(match.get("job_snapshot"))
        items = [{"field": field, "evidence": evidence, "source_uri": job.get("source_uri"),
                  "source_fetched_at": job.get("source_fetched_at"), "source_license": job.get("source_license")}
                 for field, evidence in _object(job.get("field_evidence")).items()]
    page = items[offset:offset + limit]
    coverage = _coverage(len(items), offset, len(page))
    return {"success": True, "uri": uri, "status": record["status"], "section": section,
            "version": record["version"], "business_artifact_verified": True,
            "data": _safe(page), "coverage": coverage,
            "continuation": "使用 next_offset 继续读取" if coverage["has_more"] else None}
