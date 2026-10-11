"""One owner-scoped discovery/read entry point for platform business resources.

Domain adapters remain responsible for verifying their own authoritative rows.
This module only dispatches typed URIs; it does not mirror business state in an
Agent-specific database or let model-supplied identifiers select an owner.
"""

from __future__ import annotations

from typing import Any

from .business_resources import (
    OVERVIEW_URI, PORTRAIT_URI, RESUME_URI,
    list_current_resources, read_current_resource,
)


def _items(result: Any) -> list[dict]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict) and isinstance(result.get("resources"), list):
        return [item for item in result["resources"] if isinstance(item, dict)]
    raise ValueError("业务资料目录格式无效")


def list_platform_resources(mysql, *, student_id: str) -> dict:
    """Discover each domain separately so one unavailable service is visible."""
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    resources: list[dict] = []
    unavailable: list[dict] = []
    from .match_report_resources import list_match_report_resources
    from .personal_resources import list_personal_resources
    from .resume_report_resources import list_resume_report_resources

    providers = (
        ("resume_portrait", list_current_resources),
        ("match_report", list_match_report_resources),
        ("resume_report", list_resume_report_resources),
        ("personal", list_personal_resources),
    )
    for name, provider in providers:
        try:
            resources.extend(_items(provider(mysql, student_id=student_id)))
        except Exception:
            unavailable.append({"source": name, "status": "unavailable"})
    return {
        "success": True,
        "status": "partial" if unavailable else ("completed" if resources else "empty"),
        "completion_scope": "catalog_discovery_only",
        "business_artifact_verified": False,
        "resources": resources,
        "unavailable_sources": unavailable,
    }


def read_platform_resource(
    mysql, *, student_id: str, uri: str, section: str = "overview",
    offset: int = 0, limit: int = 3, dimension: str = "",
) -> dict:
    """Read one exact URI, always under the runtime Principal's student id."""
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    if not isinstance(uri, str) or len(uri) > 160:
        raise ValueError("无效业务资料 URI")
    if uri in {OVERVIEW_URI, RESUME_URI, PORTRAIT_URI}:
        return read_current_resource(
            mysql, student_id=student_id, uri=uri, section=section,
            offset=offset, limit=limit, dimension=dimension,
        )
    from .match_report_resources import handles_uri, read_match_report_resource
    if handles_uri(uri):
        return read_match_report_resource(
            mysql, student_id=student_id, uri=uri, section=section,
            offset=offset, limit=limit, dimension=dimension,
        )
    from .resume_report_resources import handles_uri as handles_resume_report_uri, read_resume_report_resource
    if handles_resume_report_uri(uri):
        return read_resume_report_resource(
            mysql, student_id=student_id, uri=uri, section=section,
            offset=offset, limit=limit, dimension=dimension,
        )
    from .personal_resources import accepts_personal_resource, read_personal_resource
    if accepts_personal_resource(uri):
        return read_personal_resource(
            mysql, student_id=student_id, uri=uri, section=section,
            offset=offset, limit=limit,
        )
    raise ValueError("未知业务资料 URI")
