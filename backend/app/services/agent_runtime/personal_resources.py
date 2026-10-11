"""Read-only, owner-scoped personal resources for the main Agent.

This is a thin domain adapter over existing MySQL records.  It follows the
list/read URI contract already used for career resources, not a second plan,
favorite, or learning-progress store.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .tool_egress import redact_text


ACTION_PLANS_URI = "career://me/action-plans"
LEARNING_PROGRESS_URI = "career://me/learning-progress"
SAVED_JOBS_URI = "career://me/saved-jobs"
TARGET_ROLES_URI = "career://me/target-roles"
PERSONAL_COLLECTION_URIS = frozenset({
    ACTION_PLANS_URI, LEARNING_PROGRESS_URI, SAVED_JOBS_URI, TARGET_ROLES_URI,
})
_PLAN_URI = re.compile(r"^career://me/action-plans/(plan_[A-Za-z0-9_-]+)$")
_BODY_CHUNK_CHARS = 1200


def accepts_personal_resource(uri: str) -> bool:
    return uri in PERSONAL_COLLECTION_URIS or bool(_PLAN_URI.fullmatch(uri or ""))


def _check_owner(student_id: str) -> None:
    if not student_id:
        raise PermissionError("缺少服务端学生身份")


def _check_page(offset: int, limit: int) -> None:
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 3:
        raise ValueError("limit 必须为 1 至 3")


def _coverage(total: int, offset: int, returned: int) -> dict:
    next_offset = offset + returned
    has_more = next_offset < total
    return {
        "total": total, "offset": offset, "returned": returned,
        "complete": not has_more, "has_more": has_more,
        "next_offset": next_offset if has_more else None,
    }


def _clean(value: Any) -> str:
    return redact_text(str(value or ""), max_len=None)


def _plan_filter() -> str:
    # MySQL verifies the persisted body hash without pulling every plan body
    # into the Agent process.  The index must point at the same owner/session.
    return """
        FROM agent_action_plan p
        WHERE p.student_id=:sid AND SHA2(p.body_text, 256)=p.body_sha256
          AND EXISTS (
            SELECT 1 FROM agent_artifact a
            WHERE a.resource_type='agent_action_plan' AND a.resource_id=p.plan_id
              AND a.kind='action_plan' AND a.status='completed'
              AND a.student_id=p.student_id AND a.session_id=p.session_id
          )
    """


def _summary(mysql, uri: str, student_id: str) -> dict:
    if uri == ACTION_PLANS_URI:
        sql = "SELECT COUNT(*) AS n, MAX(p.updated_at) AS changed " + _plan_filter()
    elif uri == LEARNING_PROGRESS_URI:
        sql = "SELECT COUNT(*) AS n, MAX(updated_at) AS changed FROM learning_progress WHERE user_id=:sid"
    elif uri == SAVED_JOBS_URI:
        sql = "SELECT COUNT(*) AS n, MAX(saved_at) AS changed FROM saved_job WHERE user_id=:sid"
    elif uri == TARGET_ROLES_URI:
        sql = "SELECT COUNT(*) AS n, MAX(updated_at) AS changed FROM target_role WHERE user_id=:sid"
    else:
        raise ValueError("未知个人资料 URI")
    rows = mysql.fetch_all(sql, {"sid": student_id})
    row = rows[0] if rows else {}
    count = int(row.get("n") or 0)
    return {"count": count, "version": f"{count}:{row.get('changed') or 'none'}"}


def list_personal_resources(mysql, *, student_id: str) -> list[dict]:
    """Discover only collections with persisted owner records.

    Catalog membership is not an assertion that a saved job's source, a
    learning course, or a draft plan's advice has been independently verified.
    """
    _check_owner(student_id)
    definitions = (
        (ACTION_PLANS_URI, "我的学习计划初稿", "draft", ["overview", "items"]),
        (LEARNING_PROGRESS_URI, "我的学习进度", "recorded", ["overview", "items"]),
        (SAVED_JOBS_URI, "我收藏的岗位", "user_submitted", ["overview", "items"]),
        (TARGET_ROLES_URI, "我的目标方向", "user_submitted", ["overview", "items"]),
    )
    resources = []
    for uri, title, status, sections in definitions:
        summary = _summary(mysql, uri, student_id)
        if summary["count"]:
            resources.append({
                "uri": uri, "title": title, "status": status,
                "version": summary["version"], "count": summary["count"],
                "sections": sections,
            })
    return resources


def _read_plan(mysql, *, student_id: str, uri: str, section: str,
               offset: int, limit: int) -> dict:
    plan_id = _PLAN_URI.fullmatch(uri).group(1)
    sql = """
        SELECT p.plan_id,p.title,p.status,p.body_text,p.body_sha256,
               p.created_at,p.updated_at,a.artifact_id,a.version AS artifact_version
        FROM agent_action_plan p JOIN agent_artifact a
          ON a.resource_type='agent_action_plan' AND a.resource_id=p.plan_id
         AND a.kind='action_plan' AND a.status='completed'
         AND a.student_id=p.student_id AND a.session_id=p.session_id
        WHERE p.student_id=:sid AND p.plan_id=:plan_id
          AND SHA2(p.body_text,256)=p.body_sha256
        ORDER BY a.id DESC LIMIT 1
    """
    rows = mysql.fetch_all(sql, {"sid": student_id, "plan_id": plan_id})
    if not rows:
        raise LookupError("当前用户没有可核验的学习计划")
    row = rows[0]
    body = str(row.get("body_text") or "")
    # Redact before chunking: splitting an email/phone across a page boundary
    # must not bypass the egress pattern.
    safe_body = _clean(body)
    version = f"{row['artifact_id']}:{row.get('artifact_version')}:{row['body_sha256']}"
    base = {
        "success": True, "status": "draft", "maturity": "draft", "published": False,
        "uri": uri, "version": version,
        "artifact_id": row["artifact_id"],
    }
    if section == "overview":
        if offset:
            raise ValueError("overview 不支持 offset")
        return {**base, "coverage": "overview_only", "data": {
            "plan_id": plan_id, "title": _clean(row.get("title")),
            "body_chars": len(body),
            "body_chunks": math.ceil(len(safe_body) / _BODY_CHUNK_CHARS),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }, "continuation": "按 section=body 分块读取初稿；初稿不是已核验的学习建议。"}
    if section != "body":
        raise ValueError("学习计划仅支持 overview/body")
    chunks = [safe_body[i:i + _BODY_CHUNK_CHARS] for i in range(0, len(safe_body), _BODY_CHUNK_CHARS)]
    page = chunks[offset:offset + limit]
    coverage = _coverage(len(chunks), offset, len(page))
    return {**base, "section": section, "coverage": coverage, "data": page,
            "continuation": "使用同一 URI 和 next_offset 继续读取" if coverage["has_more"] else None}


def read_personal_resource(
    mysql, *, student_id: str, uri: str, section: str = "overview",
    offset: int = 0, limit: int = 3,
) -> dict:
    """Read bounded pages; model-supplied owner IDs are never accepted."""
    _check_owner(student_id)
    _check_page(offset, limit)
    if _PLAN_URI.fullmatch(uri or ""):
        return _read_plan(mysql, student_id=student_id, uri=uri,
                          section=section, offset=offset, limit=limit)
    if uri not in PERSONAL_COLLECTION_URIS:
        raise ValueError("未知个人资料 URI")
    summary = _summary(mysql, uri, student_id)
    if not summary["count"]:
        raise LookupError("当前用户没有此类已记录资料")
    base = {"success": True, "uri": uri, "version": summary["version"]}
    if uri == ACTION_PLANS_URI:
        status = "draft"
        source_note = "仅为已保存的学习计划初稿，正文与建议未获外部事实核验。"
    elif uri == LEARNING_PROGRESS_URI:
        status = "recorded"
        source_note = "仅为当前用户记录的学习进度；课程内容源需逐项核验。"
    else:
        status = "user_submitted"
        source_note = "用户记录的偏好/收藏，不代表岗位目录或来源已认证。"
    if section == "overview":
        if offset:
            raise ValueError("overview 不支持 offset")
        return {**base, "status": status, "coverage": "overview_only",
                "data": {"count": summary["count"], "source_note": source_note},
                "continuation": "按 section=items 分页读取，不要把概览当作全部记录。"}
    if section != "items":
        raise ValueError("个人资料集合仅支持 overview/items")
    params = {"sid": student_id, "limit": limit, "offset": offset}
    if uri == ACTION_PLANS_URI:
        rows = mysql.fetch_all(
            "SELECT p.plan_id,p.title,p.status,p.created_at,p.updated_at " + _plan_filter()
            + " ORDER BY p.updated_at DESC,p.plan_id DESC LIMIT :limit OFFSET :offset", params,
        )
        data = [{"plan_id": r["plan_id"], "uri": f"{ACTION_PLANS_URI}/{r['plan_id']}",
                 "title": _clean(r.get("title")), "maturity": r.get("status"),
                 "updated_at": str(r.get("updated_at") or "")}
                for r in rows]
    elif uri == LEARNING_PROGRESS_URI:
        rows = mysql.fetch_all(
            "SELECT lp.id,lp.course_id,lp.chapter_id,lp.progress,lp.completed,lp.updated_at,"
            " c.course_id AS source_course_id,c.title AS course_title,"
            "ch.title AS chapter_title,ch.id AS source_chapter_id "
            "FROM learning_progress lp LEFT JOIN course c ON c.course_id=lp.course_id "
            "LEFT JOIN chapter ch ON ch.id=lp.chapter_id AND ch.course_id=lp.course_id "
            "WHERE lp.user_id=:sid ORDER BY lp.updated_at DESC,lp.id DESC "
            "LIMIT :limit OFFSET :offset", params,
        )
        data = [{"record_id": r["id"], "course_id": r["course_id"],
                 "chapter_id": r["chapter_id"], "course_title": _clean(r.get("course_title")),
                 "chapter_title": _clean(r.get("chapter_title")),
                 "progress": r.get("progress"), "completed": bool(r.get("completed")),
                 "source_status": "course_and_chapter_found" if r.get("source_course_id") and r.get("source_chapter_id") else "source_missing",
                 "updated_at": str(r.get("updated_at") or "")}
                for r in rows]
    elif uri == SAVED_JOBS_URI:
        rows = mysql.fetch_all(
            "SELECT id,job_id,job_title,company,industry,salary,location,saved_at "
            "FROM saved_job WHERE user_id=:sid ORDER BY saved_at DESC,id DESC "
            "LIMIT :limit OFFSET :offset", params,
        )
        data = [{"record_id": r["id"], "job_id": r["job_id"],
                 "job_title": _clean(r.get("job_title")), "company": _clean(r.get("company")),
                 "industry": _clean(r.get("industry")), "salary": _clean(r.get("salary")),
                 "location": _clean(r.get("location")), "source_verified": False,
                 "saved_at": str(r.get("saved_at") or "")}
                for r in rows]
    else:
        rows = mysql.fetch_all(
            "SELECT id,role_name,priority,created_at,updated_at FROM target_role "
            "WHERE user_id=:sid ORDER BY priority DESC,id DESC LIMIT :limit OFFSET :offset", params,
        )
        data = [{"record_id": r["id"], "role_name": _clean(r.get("role_name")),
                 "priority": r.get("priority"), "source": "user_preference",
                 "updated_at": str(r.get("updated_at") or "")}
                for r in rows]
    coverage = _coverage(summary["count"], offset, len(data))
    return {**base, "status": status, "section": "items", "coverage": coverage,
            "data": data, "source_note": source_note,
            "continuation": "使用相同 URI 和 next_offset 继续读取" if coverage["has_more"] else None}
