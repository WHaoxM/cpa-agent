"""Owner-scoped, versioned career resources for the main Agent.

The list/read contract follows the locally vendored Codex/OpenCode MCP resource
pattern.  It is an adapter over existing business repositories, not a second
copy of resume or portrait state.
"""

from __future__ import annotations

import json
from typing import Any

from .pipeline_reference import resolve_pipeline_task_ref
from .tool_egress import redact_text


RESUME_URI = "career://me/resume"
PORTRAIT_URI = "career://me/portrait"
OVERVIEW_URI = "career://me/overview"
_LIST_SECTIONS = {"skills", "projects", "competitions"}
_ENTITY_LINKS_SECTION = "entity_links"
_DIMENSIONS = {"D1": "D1_basic", "D2": "D2_skill", "D3": "D3_quality", "D4": "D4_potential"}


def _payload(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def _safe(value: Any) -> Any:
    """Project only known business fields; redact contact details inside prose."""
    if isinstance(value, str):
        return redact_text(value, max_len=None)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    return value


def _current(mysql, student_id: str) -> tuple[dict, dict, dict]:
    from ..pipeline import require_confirmed_profile

    rows = mysql.fetch_all(
        "SELECT profile_json FROM student_profile WHERE student_id=:sid LIMIT 1",
        {"sid": student_id},
    )
    if not rows:
        return {}, {}, {}
    profile = require_confirmed_profile(mysql, student_id)
    confirmation_id = profile["_resume_confirmation"]["task_id"]
    linked = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
        "WHERE student_id=:sid AND pipeline='L2_generate_portrait' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.source_confirmation_task_id'))=:tid "
        "ORDER BY id DESC LIMIT 1",
        {"sid": student_id, "tid": confirmation_id},
    )
    row = linked[0] if linked else {}
    result = _payload(row.get("result_json"))
    valid = (
        row.get("student_id") == student_id
        and row.get("pipeline") == "L2_generate_portrait"
        and row.get("status") in {"partial", "completed"}
        and result.get("source_confirmation_task_id") == confirmation_id
        and result.get("status") == row.get("status")
        and result.get("task_id") in (None, row.get("task_id"))
    )
    if valid:
        from ..student_self_report import portrait_current
        candidate = _payload(result.get("portrait" if row["status"] == "completed" else "portrait_candidate"))
        valid = portrait_current(profile, candidate)
    if valid and result.get("snapshot_id"):
        from ..student_portrait_snapshot import read_snapshot_for_task
        snapshot = read_snapshot_for_task(mysql, student_id=student_id,
                                          source_task_id=row["task_id"])
        if (not snapshot or snapshot["snapshot_id"] != result["snapshot_id"]
                or snapshot["portrait"] != candidate):
            raise ValueError("当前画像快照未通过权威记录校验")
    if valid and row["status"] == "completed":
        # Completed is only usable after persisted portrait readback.
        resolve_pipeline_task_ref(mysql, student_id=student_id, task_id=row["task_id"])
    if not valid:
        row, result = {}, {}
    return profile, row, result


def list_current_resources(mysql, *, student_id: str) -> dict:
    """Discover the current, not a stale, user's verified resources."""
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    profile, portrait_task, _ = _current(mysql, student_id)
    resources = []
    if profile:
        resources.append({
            "uri": OVERVIEW_URI,
            "title": "当前职业资料概览",
            "status": "completed",
            "version": profile["_resume_confirmation"]["task_id"],
            "sections": ["overview"],
        })
        resources.append({
            "uri": RESUME_URI,
            "title": "已确认简历资料",
            "status": "completed",
            "version": profile["_resume_confirmation"]["task_id"],
            "sections": ["overview", "skills", "projects", "competitions"] +
                        ([_ENTITY_LINKS_SECTION] if isinstance(profile.get("entity_linking"), dict) else []),
        })
    if portrait_task:
        resources.append({
            "uri": PORTRAIT_URI,
            "title": "当前简历对应的四维画像",
            "status": portrait_task["status"],
            "version": portrait_task["task_id"],
            "sections": ["overview", "evidence"],
        })
    return {"success": True, "status": "completed" if resources else "empty", "resources": resources}


def _page(items: list, *, offset: int, limit: int) -> tuple[list, dict]:
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 3:
        raise ValueError("limit 必须为 1 至 3")
    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    return page, {
        "total": len(items), "offset": offset, "returned": len(page),
        "complete": next_offset >= len(items),
        "has_more": next_offset < len(items),
        "next_offset": next_offset if next_offset < len(items) else None,
    }


def _section_item(section: str, item: Any) -> Any:
    if section == "skills":
        if isinstance(item, str):
            return _safe(item)
        return _safe({"name": item.get("name")}) if isinstance(item, dict) else None
    if not isinstance(item, dict):
        return None
    fields = ("name", "role", "is_team", "desc") if section == "projects" else ("name", "level", "award")
    return _safe({key: item.get(key) for key in fields if key in item})


def _entity_review(mysql, student_id, source_task_id, linking):
    """Read the existing review authority, never reconstruct its state machine.

    Unknown review state is NOT 'not reviewed': a failed journal read must not
    cause an Agent to reuse a proposal the user may already have rejected.
    """
    from ..resume_entity_review import read_entity_review
    try:
        snapshot = read_entity_review(mysql, student_id=student_id)
    except Exception:
        return {"status": "unavailable", "revision": None,
                "next_action": "retry_entity_review_read"}, {}
    if (snapshot.get("source_confirmation_task_id") != source_task_id
            or snapshot.get("source_sha256") != linking.get("source_sha256")):
        raise ValueError("简历版本在读取审阅时发生变化，请从第一页重新读取")
    return {"status": "available", "revision": snapshot["revision"],
            "source_confirmation_task_id": snapshot["source_confirmation_task_id"],
            "source_sha256": snapshot["source_sha256"],
            "graph_publication_status": snapshot["graph_publication_status"]}, {
                item["mention_index"]: item for item in snapshot["reviews"]}


def read_current_resource(
    mysql, *, student_id: str, uri: str, section: str = "overview",
    offset: int = 0, limit: int = 3, dimension: str = "",
) -> dict:
    """Read a small structured page; never trust model-written owner or state."""
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    if uri not in {OVERVIEW_URI, RESUME_URI, PORTRAIT_URI}:
        raise ValueError("未知业务资料 URI")
    profile, portrait_task, result = _current(mysql, student_id)
    if not profile:
        raise LookupError("当前用户没有已确认简历")
    source_task_id = profile["_resume_confirmation"]["task_id"]
    if uri == OVERVIEW_URI:
        if section != "overview" or offset != 0:
            raise ValueError("职业资料概览只支持 overview")
        candidate = _payload(result.get("portrait" if portrait_task.get("status") == "completed" else "portrait_candidate"))
        dimensions = {}
        for code, key in _DIMENSIONS.items():
            value = _payload(candidate.get(key))
            score = value.get("dimension_score")
            dimensions[code] = score if isinstance(score, (int, float)) and not isinstance(score, bool) else None
        projects = profile.get("projects") or []
        projects = projects if isinstance(projects, list) else []
        return {
            "success": True, "status": portrait_task.get("status") or "completed", "uri": uri,
            "version": source_task_id, "coverage": "overview_only",
            "data": _safe({
                "name": profile.get("name"), "school": profile.get("school"),
                "major": profile.get("major"), "target_role": profile.get("target_role"),
                "counts": {key: len(profile.get(key) or []) for key in _LIST_SECTIONS},
                "first_projects": [
                    {"name": item.get("name"), "role": item.get("role")}
                    for item in projects[:2] if isinstance(item, dict)
                ],
                "portrait": {
                    "status": portrait_task.get("status") or "not_available",
                    "published": portrait_task.get("status") == "completed",
                    "reason_coverage": "not_read_in_overview",
                    "scores": dimensions,
                    "dimensions_with_score": [
                        {"dimension": code, "score": score} for code, score in dimensions.items()
                        if score is not None
                    ],
                    "dimensions_without_score": [code for code, score in dimensions.items() if score is None],
                    "missing_fields": result.get("missing_fields") or [],
                },
            }),
            "continuation": "此处仅为概览；简历项目/技能/竞赛及画像依据须从各自 URI 分页读取。",
        }
    if uri == RESUME_URI:
        if section == "overview":
            if offset != 0:
                raise ValueError("overview 不支持 offset")
            evidence = profile["_resume_confirmation"].get("source_evidence") or {}
            return {
                "success": True, "status": "completed", "uri": uri,
                "version": source_task_id, "coverage": "overview_only",
                "data": _safe({
                    "name": profile.get("name"), "school": profile.get("school"),
                    "major": profile.get("major"), "degree": profile.get("degree"),
                    "target_role": profile.get("target_role"),
                    "source_pages": evidence.get("page_count"),
                    "observed_pages": len(evidence.get("observed_pages") or []),
                    "counts": {key: len(profile.get(key) or []) for key in _LIST_SECTIONS},
                    "entity_linking_status": (profile.get("entity_linking") or {}).get("status")
                    if isinstance(profile.get("entity_linking"), dict) else "not_available",
                }),
                "continuation": "按 sections 分页读取技能、项目、竞赛和实体对齐候选；overview 不是简历全文。",
            }
        if section == _ENTITY_LINKS_SECTION:
            linking = profile.get("entity_linking")
            if not isinstance(linking, dict):
                page, coverage = _page([], offset=offset, limit=limit)
                return {"success": True, "uri": uri, "section": section, "version": source_task_id,
                        "status": "not_available", "coverage": coverage, "data": page,
                        "review": {"status": "not_available", "revision": None},
                        "reason": "当前历史简历没有实体提议，不能推断为用户已接受或拒绝。"}
            mentions = linking.get("mentions") or []
            candidates = linking.get("candidates") or []
            decisions = linking.get("decisions") or []
            if not all(isinstance(part, list) for part in (mentions, candidates, decisions)):
                raise ValueError("实体对齐记录格式无效")
            by_index = {decision.get("mention_index"): decision for decision in decisions
                        if isinstance(decision, dict) and type(decision.get("mention_index")) is int}
            review_snapshot, reviews = _entity_review(mysql, student_id, source_task_id, linking)
            items = []
            for index, mention in enumerate(mentions):
                if not isinstance(mention, dict):
                    continue
                decision = by_index.get(index) or {}
                candidate_index = decision.get("candidate_index")
                candidate = (candidates[candidate_index] if type(candidate_index) is int
                             and 0 <= candidate_index < len(candidates)
                             and isinstance(candidates[candidate_index], dict) else None)
                review = reviews.get(index)
                review_state = ({"accept": "accepted", "reject": "rejected", "defer": "deferred"}
                                .get(review.get("action")) if review else
                                "proposed" if review_snapshot["status"] == "available" else "unavailable")
                items.append({"mention_index": index,
                              "mention": {"name": mention.get("name"), "evidence": mention.get("evidence")},
                              "decision": decision.get("decision") or "unresolved",
                              "candidate": candidate, "rationale": decision.get("rationale"),
                              "review": review, "review_state": review_state,
                              "review_revision": review_snapshot["revision"],
                              "accepted_relation": review.get("relation") if review_state == "accepted" else None})
            page, coverage = _page(items, offset=offset, limit=limit)
            return {"success": True, "uri": uri, "section": section,
                    "status": linking.get("status") or "unresolved", "version": source_task_id,
                    "source_id": linking.get("source_id"), "source_sha256": linking.get("source_sha256"),
                    "review": review_snapshot,
                    "state_revision": review_snapshot["revision"],
                    "next_action": linking.get("next_action"), "reason": linking.get("reason"),
                    "coverage": coverage, "data": _safe(page),
                    "continuation": "模型decision与用户review分开：proposed未审阅，rejected/deferred不得当接受；"
                    "accepted的related仍仅相关，不是等价。审阅不改简历skills、不等于公共图发布。"
                    "review unavailable时不得猜用户决定。分页必须核对version和review.revision一致，变化需从第一页重读。"
                    + ("使用next_offset继续读取。" if coverage["has_more"] else "")}
        if section not in _LIST_SECTIONS:
            raise ValueError("简历资料仅支持目录列出的 section")
        raw_items = profile.get(section) or []
        items = raw_items if isinstance(raw_items, list) else []
        page, coverage = _page(items, offset=offset, limit=limit)
        return {
            "success": True, "status": "completed", "uri": uri, "section": section,
            "version": source_task_id, "coverage": coverage,
            "data": [_section_item(section, item) for item in page],
            "continuation": "使用相同 URI/section 和 next_offset 继续读取" if coverage["has_more"] else None,
        }

    if not portrait_task:
        raise LookupError("当前已确认简历尚无对应画像")
    status = portrait_task["status"]
    portrait = _payload(result.get("portrait" if status == "completed" else "portrait_candidate"))
    if not portrait:
        raise ValueError("画像任务缺少可核对的结构化结果")
    if section == "overview":
        if offset != 0:
            raise ValueError("overview 不支持 offset")
        dimensions = {}
        for code, key in _DIMENSIONS.items():
            value = _payload(portrait.get(key))
            score = value.get("dimension_score")
            dimensions[code] = {
                "label": key, "score": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                "evidence_count": len(value.get("evidence") or []),
            }
        summary = _payload(portrait.get("summary"))
        return {
            "success": True, "status": status, "uri": uri,
            "version": portrait_task["task_id"], "source_resume_version": source_task_id,
            "published": status == "completed", "coverage": "overview_only",
            "data": {
                "dimensions": dimensions,
                "dimensions_with_score": [
                    {"dimension": code, "score": value["score"]}
                    for code, value in dimensions.items() if value["score"] is not None
                ],
                "dimensions_without_score": [
                    code for code, value in dimensions.items() if value["score"] is None
                ],
                "total_score": summary.get("competitiveness_score") if status == "completed" else None,
                "missing_fields": _safe(result.get("missing_fields") or []),
            },
            "continuation": "按 evidence/D1-D4 分页读取维度依据；partial 不是正式发布画像。",
        }
    if section != "evidence" or dimension not in _DIMENSIONS:
        raise ValueError("画像依据需指定 section=evidence 与 dimension=D1-D4")
    evidence = _payload(portrait.get(_DIMENSIONS[dimension])).get("evidence") or []
    items = evidence if isinstance(evidence, list) else []
    if dimension == 'D3':
        # L2 stores self-reported examples on each trait, not on the aggregate
        # evidence list. Read only current, explicitly attributed observations;
        # neither a score nor the plan's instructions are personal evidence.
        items = list(items)
        for trait in ('communication', 'teamwork', 'responsibility', 'stress_tolerance'):
            observation = _payload(_payload(portrait.get('D3_quality')).get(trait))
            provenance = _payload(observation.get('provenance'))
            example = observation.get('evidence')
            if (provenance.get('kind') == 'user_self_report'
                    and type(provenance.get('revision')) is int and provenance['revision'] > 0
                    and provenance['revision'] == portrait.get('source_self_report_revision')
                    and isinstance(example, str) and example.strip()):
                items.append({'trait': trait, 'text': example, 'provenance': provenance})
    page, coverage = _page(items, offset=offset, limit=limit)
    return {
        "success": True, "status": status, "uri": uri, "section": section,
        "dimension": dimension, "version": portrait_task["task_id"],
        "source_resume_version": source_task_id, "published": status == "completed",
        "coverage": coverage, "data": _safe(page),
        "continuation": "使用相同维度和 next_offset 继续读取" if coverage["has_more"] else None,
    }


def build_ask_overview_card(mysql, *, student_id: str, max_chars: int = 1400) -> str:
    """Bounded cross-page overview for Ask's tool-free model path."""
    try:
        resource = read_current_resource(mysql, student_id=student_id, uri=OVERVIEW_URI)
    except LookupError:
        return ""
    card = (
        "【当前用户已确认的跨页面资料概览；不是简历全文】\n"
        + json.dumps(resource, ensure_ascii=False, separators=(",", ":"))
        + "\n仅据已给字段回答；项目详情与画像证据未提供，不能推断分数成因。"
    )
    if len(card) <= max_chars:
        return card
    # Do not let ContextBudget.fit silently slice a JSON object into an
    # apparently complete record.
    return (
        "【当前用户已确认的跨页面资料概览】资料存在，但概览超出 Ask 本轮预算，"
        "未向模型提供具体字段。不要声称已读完；请切到 Agent 按资源分页查询。"
    )
