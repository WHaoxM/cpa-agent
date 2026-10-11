"""Owner-scoped Agent projection of the resume diagnostic and live job catalog.

The business service remains the single verifier of confirmation version and
persisted content hash.  This adapter only offers small, pageable reads.  Its
list/read shape follows the local OpenCode MCP resource implementation
(388406238b, packages/opencode/src/mcp/index.ts) and Codex resource handlers
(a06545b, codex-rs/core/src/tools/handlers/mcp_resource.rs); it does not
introduce another report store or an Agent-owned copy of the resume.
"""

from __future__ import annotations

import re

from .tool_egress import redact_text


RESUME_REPORTS_URI = "career://me/resume-reports"
_RESOURCE = re.compile(r"^career://me/resume-reports(?:/(resume_[0-9a-f]{32}))?$")


def handles_uri(uri: str) -> bool:
    return isinstance(uri, str) and _RESOURCE.fullmatch(uri) is not None


def _safe(value):
    if isinstance(value, str):
        return redact_text(value, max_len=None)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    return value


def _page(items, offset, limit):
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("limit 必须为 1 至 3")
    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    return page, {
        "total": len(items), "offset": offset, "returned": len(page),
        "complete": next_offset >= len(items), "has_more": next_offset < len(items),
        "next_offset": next_offset if next_offset < len(items) else None,
    }


def _current(mysql, student_id):
    from ..resume_diagnostic_report import read_resume_diagnostic

    return read_resume_diagnostic(mysql, student_id=student_id)


def list_resume_report_resources(mysql, *, student_id: str) -> dict:
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    from ..resume_diagnostic_report import ResumeReportNeedsInput

    try:
        report = _current(mysql, student_id)
    except (LookupError, ResumeReportNeedsInput):
        report = None
    resources = [{
        "uri": RESUME_REPORTS_URI, "title": "简历诊断报告", "status": report["status"] if report else "empty",
        "count": 1 if report else 0, "sections": ["overview", "items"],
        "coverage": "catalog_only", "business_artifact_verified": False,
    }]
    if report:
        resources.append({
            "uri": f"{RESUME_REPORTS_URI}/{report['report_id']}",
            "title": "当前已确认简历诊断", "status": report["status"],
            "version": report["source_confirmation_task_id"],
            "sections": ["overview", "chapters", "analysis", "limitations", "job_catalog"] + [
                f"chapter:{chapter['id']}" for chapter in report.get("chapters", [])
                if isinstance(chapter, dict) and isinstance(chapter.get("id"), str)
            ],
            "section_details": [{"section": f"chapter:{chapter['id']}",
                                 "title": chapter.get("title"),
                                 "item_count": len(chapter.get("items") or [])}
                                for chapter in report.get("chapters", [])
                                if isinstance(chapter, dict) and isinstance(chapter.get("id"), str)] +
                               [{"section": "job_catalog", "title": "当前已发布岗位目录",
                                 "item_count": (report.get("job_catalog") or {}).get("total", 0)}],
            "coverage": "catalog_only", "business_artifact_verified": True,
        })
    return {"success": True, "status": "completed", "resources": resources}


def read_resume_report_resource(
    mysql, *, student_id: str, uri: str, section: str = "overview",
    offset: int = 0, limit: int = 3, dimension: str = "",
) -> dict:
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    match = _RESOURCE.fullmatch(uri) if isinstance(uri, str) else None
    if not match:
        raise ValueError("未知简历报告 URI")
    _page([], offset, limit)
    from ..resume_diagnostic_report import read_resume_diagnostic

    report_id = match.group(1)
    if not report_id:
        try:
            report = read_resume_diagnostic(mysql, student_id=student_id)
        except LookupError:
            report = None
        if section == "overview":
            if offset:
                raise ValueError("overview 不支持 offset")
            return {"success": True, "uri": uri, "status": "available" if report else "empty",
                    "business_artifact_verified": False, "coverage": "overview_only",
                    "data": {"count": 1 if report else 0},
                    "continuation": "使用 items 回读当前确认版本的报告" if report else None}
        if section != "items":
            raise ValueError("报告集合仅支持 overview/items")
        items = ([{"uri": f"{uri}/{report['report_id']}", "status": report["status"],
                   "version": report["source_confirmation_task_id"],
                   "report_type": report["report_type"]}] if report else [])
        page, coverage = _page(items, offset, limit)
        return {"success": True, "uri": uri, "status": "empty" if not report else report["status"],
                "business_artifact_verified": bool(report), "data": page, "coverage": coverage,
                "continuation": "使用 next_offset 继续读取" if coverage["has_more"] else None}

    report = read_resume_diagnostic(mysql, student_id=student_id, report_id=report_id)
    base = {"success": True, "uri": uri, "status": report["status"],
            "version": report["source_confirmation_task_id"], "report_type": report["report_type"],
            "quality_status": report.get("quality_status"),
            "resource_id": report_id, "resource_type": "resume_diagnostic_report",
            "business_artifact_verified": True}
    if section == "job_catalog":
        from ..job_catalog_listing import list_published_job_candidates

        catalog = list_published_job_candidates(mysql, limit=limit, offset=offset)
        available = catalog["status"] in {"ready", "empty"}
        coverage = {"total": catalog["total"] if available else None,
                    "offset": offset, "returned": len(catalog["items"]),
                    "complete": available and not catalog["has_more"],
                    "has_more": catalog["has_more"] if available else False,
                    "next_offset": offset + len(catalog["items"])
                    if available and catalog["has_more"] else None,
                    "reason": "complete" if available else catalog.get("reason")}
        return _safe({**base, "status": catalog["status"],
                      "version": catalog.get("catalog_version"),
                      "source_confirmation_task_id": report["source_confirmation_task_id"],
                      "catalog_latest_published_at": catalog.get("catalog_latest_published_at"),
                      "vacancy_validity": "unknown",
                      "business_artifact_verified": available,
                      "section": section, "coverage": coverage, "data": catalog["items"],
                      "continuation": "使用相同 URI/section 和 next_offset 继续读取最新发布目录"
                      if catalog["has_more"] else None})
    if section == "overview":
        if offset:
            raise ValueError("overview 不支持 offset")
        return _safe({**base, "coverage": "overview_only", "data": {
            "chapter_count": len(report.get("chapters") or []),
            "chapters": [{"id": chapter.get("id"), "title": chapter.get("title"),
                          "item_count": len(chapter.get("items") or [])}
                         for chapter in report.get("chapters") or [] if isinstance(chapter, dict)],
            "source_confirmation_task_id": report["source_confirmation_task_id"],
            "source_coverage": report.get("coverage"),
            "missing_sections": report.get("missing_sections") or [],
            "job_catalog": {key: (report.get("job_catalog") or {}).get(key)
                            for key in ("status", "total", "catalog_version",
                                        "catalog_latest_published_at", "has_more")},
        }, "continuation": "按 chapter:<id> 读取岗位方向和简历证据；按 job_catalog 读取动态已发布岗位。"})
    if section == "chapters":
        items = [{"id": chapter.get("id"), "title": chapter.get("title"),
                  "item_count": len(chapter.get("items") or [])}
                 for chapter in report.get("chapters") or [] if isinstance(chapter, dict)]
    elif section == "analysis":
        analysis = report.get("analysis") or {}
        items = ([{"kind": "summary", **analysis["summary"]}]
                 if isinstance(analysis.get("summary"), dict) else [])
        for kind in ("strengths", "questions"):
            items.extend({"kind": kind, **item} for item in analysis.get(kind) or []
                         if isinstance(item, dict))
    elif section == "limitations":
        items = report.get("limitations") or []
    elif section.startswith("chapter:"):
        chapter_id = section.split(":", 1)[1]
        chapter = next((chapter for chapter in report.get("chapters") or []
                        if isinstance(chapter, dict) and chapter.get("id") == chapter_id), None)
        if chapter is None:
            raise ValueError("报告章节不存在")
        items = chapter.get("items") or []
    else:
        raise ValueError("报告仅支持 overview/chapters/analysis/limitations/job_catalog/chapter:<id>")
    if not isinstance(items, list):
        raise ValueError("报告章节格式无效")
    page, coverage = _page(items, offset, limit)
    return _safe({**base, "section": section, "coverage": coverage, "data": page,
                  "continuation": "使用相同 URI/section 和 next_offset 继续读取" if coverage["has_more"] else None})
