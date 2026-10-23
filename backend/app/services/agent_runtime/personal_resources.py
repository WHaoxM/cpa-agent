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
        SELECT p.plan_id,p.student_id,p.session_id,p.title,p.status,p.body_text,p.body_sha256,
               p.report_uri,p.report_version,p.resume_version,p.gap_summary,p.exploration_round_id,
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
    source_status = None
    source_note = "普通文本计划没有结构化来源记录，不能视为已核验建议。"
    gap_summary = str(row.get("gap_summary") or "")
    if row.get('exploration_round_id'):
        from ..career_exploration import ExplorationService
        source_status = ExplorationService(mysql).plan_source(row['exploration_round_id'], student_id)['status']
        source_note = '学生为验证职业兴趣制定的尝试计划，不是岗位胜任或已核验简历事实。'
        if source_status != 'current':
            source_note += ' 原参考资料已变化或不可核对，只作历史尝试复盘。'
    if row.get("report_uri"):
        try:
            from .action_plan_store import _action_plan_source_status

            source_status = _action_plan_source_status(
                mysql, student_id=student_id, report_uri=str(row.get("report_uri")),
                report_version=str(row.get("report_version") or ""),
                resume_version=str(row.get("resume_version") or ""),
            )
        except Exception:
            source_status = "unavailable"
        source_note = (
            "计划关联的原报告与已确认简历仍匹配当前版本。"
            if source_status == "current" else
            "计划来源已过期或暂时无法核验；以下只能作为历史记录，不能用于当前求职判断。"
        )
    if section == "overview":
        if offset:
            raise ValueError("overview 不支持 offset")
        return {**base, "coverage": "overview_only", "data": {
            "plan_id": plan_id, "title": _clean(row.get("title")),
            "body_chars": len(body),
            "body_chunks": math.ceil(len(safe_body) / _BODY_CHUNK_CHARS),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "source_status": source_status,
        }, "source_note": source_note,
            "continuation": "按 section=body 分块读取计划正文，按 section=steps/evidence 读取步骤和学生提交记录。"}
    if section in {"steps", "evidence"}:
        rows = mysql.fetch_all("""
            SELECT s.step_id,s.position,s.title,s.detail,s.completed,s.completed_at,
                   e.summary AS evidence_summary,e.attachment_file_id,
                   e.created_at AS evidence_created_at,e.updated_at AS evidence_updated_at
            FROM agent_action_plan_step s
            LEFT JOIN agent_action_plan_step_evidence e
              ON e.step_id=s.step_id AND e.plan_id=s.plan_id AND e.student_id=:sid
            WHERE s.plan_id=:plan_id
            ORDER BY s.position ASC LIMIT :limit OFFSET :offset
        """, {"sid": student_id, "plan_id": plan_id, "limit": limit, "offset": offset})
        total_rows = mysql.fetch_all(
            "SELECT COUNT(*) AS n FROM agent_action_plan_step WHERE plan_id=:plan_id",
            {"plan_id": plan_id},
        )
        total = int((total_rows[0] if total_rows else {}).get("n") or 0)
        data = []
        for step in rows:
            entry = {
                "position": int(step.get("position") or 0),
                "title": _clean(step.get("title")),
                "detail": _clean(step.get("detail")),
                "completed": bool(step.get("completed")),
                "completed_at": str(step.get("completed_at") or "") or None,
                "practice_record": None,
            }
            if step.get("evidence_summary") is not None:
                record = {
                    "summary": _clean(step.get("evidence_summary")),
                    "source": "student_submitted",
                    "verified": False,
                    "updated_at": str(step.get("evidence_updated_at") or ""),
                    "attachment": None,
                }
                file_id = step.get("attachment_file_id")
                if file_id:
                    record["attachment"] = _read_student_attachment(
                        student_id=student_id, session_id=str(row.get("session_id") or ""),
                        file_id=str(file_id), query=gap_summary,
                        include_content=(section == "evidence"),
                    )
                entry["practice_record"] = record
            if row.get('exploration_round_id') and section == 'evidence':
                # Do not couple an assignment title with completed=true in the
                # model's practice evidence. The checkbox is not execution proof.
                entry = {'position': entry['position'],
                         'progress_self_marked': entry['completed'],
                         'practice_record': entry['practice_record'],
                         'boundary': '勾选仅为本人标记的进度；实际做过什么只能依据本人文字和已读附件，不得从计划步骤推断。'}
            data.append(entry)
        coverage = _coverage(total, offset, len(data))
        return {**base, "status": "user_submitted", "section": section,
                "coverage": coverage,
                "data": [{"historical_gap": _clean(gap_summary),
                          "source_status": source_status,
                          "source_note": source_note,
                          "step": item,
                          "evidence_boundary": "练习说明和附件均由学生提交，未经能力/经历核验；不更新简历、画像或匹配分数。"}
                         for item in data],
                "source_note": source_note,
                "continuation": "使用同一 URI 和 next_offset 继续读取步骤" if coverage["has_more"] else None}
    if section != "body":
        raise ValueError("学习计划仅支持 overview/body/steps/evidence")
    chunks = [safe_body[i:i + _BODY_CHUNK_CHARS] for i in range(0, len(safe_body), _BODY_CHUNK_CHARS)]
    page = chunks[offset:offset + limit]
    coverage = _coverage(len(chunks), offset, len(page))
    if row.get('exploration_round_id'):
        page = [{'task_instructions_not_execution_evidence': item,
                 'boundary': '这是尝试安排，不是已执行事实；不得据此认定学生做过这些动作。'} for item in page]
    return {**base, "section": section, "coverage": coverage, "data": page,
            "continuation": "使用同一 URI 和 next_offset 继续读取" if coverage["has_more"] else None}


def _read_student_attachment(*, student_id: str, session_id: str, file_id: str,
                             query: str, include_content: bool) -> dict:
    """Read only through the existing mounted, owner-scoped document pipeline."""
    try:
        from .media.file_store import get_agent_file_service

        service = get_agent_file_service()
        meta = service.get(file_id)
    except Exception:
        meta = None
        service = None
    if (not meta or meta.get("student_id") != student_id
            or meta.get("session_id") != session_id):
        return {"filename": None, "ingest_status": "unavailable",
                "read_status": "unavailable", "content": None}
    ingest_status = str(meta.get("status") or "unavailable")
    result = {"filename": _clean(meta.get("filename")),
              "ingest_status": ingest_status,
              "read_status": "processing" if ingest_status in {"queued", "ingesting"} else
                  "failed" if ingest_status == "failed" else "unavailable",
              "content": None}
    if not include_content or ingest_status != "ready":
        return result
    try:
        from .media.session_resources import mount_resources
        from .media.document_access import materialize_for_ask

        mount_resources(session_id, [{"type": "file", "file_id": file_id}],
                        student_id=student_id)
        packed, report = materialize_for_ask(
            [file_id], inline_budget=1800, query=query,
            student_id=student_id, session_id=session_id,
        )
        file_report = (report.get("files") or [{}])[0]
        if not int(file_report.get("total_chars") or 0):
            result["read_status"] = "unavailable"
            return result
        result["read_status"] = "read" if report.get("all_complete") else "partial"
        result["content"] = packed[:2200]
    except Exception:
        # A file can finish ingestion after this read; the next turn can retry.
        result["read_status"] = "unavailable"
    return result


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
