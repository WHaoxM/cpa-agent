"""Bounded, owner-scoped evidence retrieval for Ask, Agent, and Experts.

The resource catalog is authoritative for discovery and domain reads.  The
model only proposes a small retrieval plan from that catalog; it never gets a
business tool, owner identifier, SQL query, or mutable workflow handle.

Reuse boundary: OpenCode 388406238b ``mcp/index.ts`` and Codex a06545b
``mcp_resource.rs`` establish list/read URI resources and explicit cursors.
Their transport and general tool orchestration are deliberately not copied:
this adapter reads the existing career resource catalog and preserves its
Principal-scoped source/version/coverage contract.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from .resource_catalog import list_platform_resources, read_platform_resource
from ...utils.logger import get_logger
from .tool_egress import redact_text


_BAD_STATUS = frozenset({"unavailable", "not_available", "failed", "empty", "denied", "stale"})
_MAX_SELECTIONS = 4
logger = get_logger("cpa-agent.evidence_retrieval")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _select_plan(question: str, resources: list[dict]) -> dict | None:
    """One structured model call; ``None`` means planning unavailable.

    A caller-provided URI is not an instruction to this model.  The returned
    plan is subsequently checked against a server-discovered allowlist.
    """
    try:
        from ...utils.llm_client import get_llm_client
        from .model_router import route_model

        allowed = [
            {"id": f"r{i}", "title": r.get("title"), "status": r.get("status"),
             "sections": r.get("sections", ["overview"]),
             **({"section_details": r["section_details"]} if r.get("section_details") else {})}
            for i, r in enumerate(resources) if isinstance(r.get("uri"), str)
        ]
        ids = {f"r{i}": r["uri"] for i, r in enumerate(resources)
               if isinstance(r.get("uri"), str)}
        route = route_model("ask_answer")
        raw = get_llm_client().chat(
            messages=[
                {"role": "system", "content": (
                    "你只负责为用户当前问题选择最小必要的只读证据，不回答问题。"
                    "若问题询问用户已保存或已确认的个人资料，必须选择对应资源 id 和最具体 section；"
                    "如询问技能名称，应读 resume 的 skills，overview 只提供数量。"
                    "目录给出 section_details 时，优先根据章节标题选择与问题对应的 section；"
                    "询问章节条目数量时可读取该章节，使用返回的 coverage.total，而非猜测。"
                    "同时给出 answer_kind：只有问题仅询问一个章节的记录条数，或这个计数本身能/不能证明什么，"
                    "才用 section_count；若还要求项目质量、个人优势、能力、贡献评价、岗位建议或其他实质分析，"
                    "必须用 analysis。不确定也用 analysis。不要仅因问题含有数字就选 section_count。"
                    "对个人资料问题必须输出 needed=true 且 reads 非空。"
                    "只有问题与个人业务资料完全无关（如问候或通用知识）时，"
                    "才输出 needed=false 且 reads 为空。"
                    "Select from the supplied catalog only. "
                    "Return JSON only: {\"needed\":true,\"answer_kind\":\"analysis\",\"reads\":[{\"id\":\"r0\",\"section\":\"...\","
                    "\"dimension\":\"\"}]}. Select at most 4 reads. For a requested list "
                    "choose its list section, not overview. Do not invent a URI, owner, "
                    "section, score, or answer. No business operation is authorized."
                )},
                {"role": "user", "content": _json({"question": question[:2000], "catalog": allowed})},
            ],
            temperature=0.0, max_tokens=min(500, route["max_tokens"]),
            model_override=route["model"], response_format={"type": "json_object"},
        )
        parsed = json.loads(raw or "")
        reads = parsed.get("reads") if isinstance(parsed, dict) else None
        needed = parsed.get("needed") if isinstance(parsed, dict) else None
        if type(needed) is not bool or not isinstance(reads, list):
            logger.warning("invalid evidence selection shape: %s", redact_text(str(raw), max_len=400))
            return None
        selected = []
        for entry in reads[:_MAX_SELECTIONS]:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                selected.append({"uri": ids.get(entry["id"]),
                                 "section": entry.get("section"),
                                 "dimension": entry.get("dimension") or ""})
        if not needed:
            logger.info("model judged business evidence unnecessary: %s",
                        redact_text(str(raw), max_len=400))
        if needed and not selected:
            logger.warning("required evidence has no valid resource id: %s",
                           redact_text(str(raw), max_len=400))
        kind = ("section_count" if parsed.get("answer_kind") == "section_count"
                and needed and len(reads) == 1 and len(question) <= 2000 else "analysis")
        return {"needed": needed, "reads": selected, "answer_kind": kind}
    except Exception as exc:
        logger.warning("evidence selection unavailable: %s", type(exc).__name__)
        return None


def _validated_plan(raw: Sequence[Any], catalog: list[dict]) -> list[dict]:
    allowed = {r.get("uri"): set(r.get("sections") or ["overview"])
               for r in catalog if isinstance(r, dict) and isinstance(r.get("uri"), str)}
    plan: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in raw[:_MAX_SELECTIONS]:
        if not isinstance(entry, dict):
            continue
        uri, section = entry.get("uri"), entry.get("section", "overview")
        dimension = entry.get("dimension") or ""
        if not isinstance(uri, str) or not isinstance(section, str) or not isinstance(dimension, str):
            continue
        if section not in allowed.get(uri, set()):
            continue
        if dimension and (section != "evidence" or dimension not in {"D1", "D2", "D3", "D4"}):
            continue
        if section == "evidence" and not dimension and uri == "career://me/portrait":
            continue
        key = (uri, section, dimension)
        if key not in seen:
            plan.append({"uri": uri, "section": section, "dimension": dimension})
            seen.add(key)
    return plan


def _base() -> dict:
    return {
        "status": "unavailable", "items": [], "sources": [],
        "coverage": {"complete": False, "returned": 0, "total": None,
                     "reason": "not_read"},
        "next_offset": None, "has_more": False, "unavailable": [],
        "total": None, "continuations": [], "truncated": False,
        "serialized_chars": 0, "budget_exceeded": False,
    }


def _measure(pack: dict) -> int:
    """Include metadata and the size field itself in the wire-size measure."""
    for _ in range(4):
        size = len(_json(pack))
        if pack["serialized_chars"] == size:
            return size
        pack["serialized_chars"] = size
    return len(_json(pack))


def _finish_budget(pack: dict, max_chars: int) -> dict:
    """Never ship an oversized evidence body; expose an impossible tiny cap."""
    while _measure(pack) > max_chars and pack["items"]:
        removed = pack["items"].pop()
        key = {"uri": removed["uri"], "section": removed["section"],
               "dimension": removed.get("dimension", ""), "next_offset": removed["offset"],
               "version": removed.get("version")}
        if "state_revision" in removed:
            key["state_revision"] = removed["state_revision"]
        pack["continuations"] = [key] + [
            existing for existing in pack["continuations"]
            if (existing["uri"], existing["section"], existing.get("dimension", ""))
            != (key["uri"], key["section"], key["dimension"])
        ]
        pack["has_more"] = True
        pack["next_offset"] = key["next_offset"]
        pack["truncated"] = True
        pack["coverage"].update({"complete": False, "returned": len(pack["items"]),
                                 "reason": "char_budget"})
        pack["status"] = "partial" if pack["items"] else "unavailable"
    if _measure(pack) > max_chars:
        pack["budget_exceeded"] = True
        pack["coverage"].update({"complete": False, "reason": "budget_too_small"})
        pack["status"] = "unavailable"
        _measure(pack)
    return pack


def build_evidence_pack(
    mysql, *, student_id: str, question: str, resource_refs=(),
    max_items: int = 10, max_chars: int = 6000, continuation: dict | None = None,
) -> dict:
    """Read a bounded evidence pack from canonical owner-scoped resources.

    ``max_chars`` bounds the serialized whole pack whenever possible. If even
    an empty explanatory pack cannot fit, ``budget_exceeded`` is explicit and
    callers must not mount it. ``next_offset`` is the next unread source offset
    for a single selection; ``continuations`` disambiguates multiple sources.
    No item is sliced mid-field or silently dropped.
    """
    if not student_id:
        raise PermissionError("缺少服务端学生身份")
    if type(max_items) is not int or max_items < 1 or max_items > 100:
        raise ValueError("max_items 必须为 1 至 100")
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError("max_chars 必须为正整数")
    if not isinstance(question, str):
        raise ValueError("question 必须为字符串")

    pack = _base()
    try:
        discovered = list_platform_resources(mysql, student_id=student_id)
        catalog = [r for r in discovered.get("resources", []) if isinstance(r, dict)]
        pack["unavailable"].extend(discovered.get("unavailable_sources") or [])
    except Exception:
        catalog = []
        pack["unavailable"].append({"source": "catalog", "status": "unavailable"})

    refs = list(resource_refs or ())
    if len(refs) > _MAX_SELECTIONS:
        raise ValueError("单轮最多引用 4 个业务资料")
    # A task ref without a current canonical resource binding is intentionally
    # status-only. Fail the whole evidence request closed (including mixed
    # bound/unbound refs) rather than falling back to the current catalog or
    # silently omitting the unavailable task.
    unbound_tasks = [
        ref for ref in refs
        if isinstance(ref, dict)
        and ref.get("status") == "reference_not_current"
        and isinstance(ref.get("task_id"), str)
    ]
    malformed_refs = [
        ref for ref in refs
        if not (isinstance(ref, str) and ref.strip())
        and not (isinstance(ref, dict) and isinstance(ref.get("uri"), str) and ref.get("uri"))
        and ref not in unbound_tasks
    ]
    if unbound_tasks or malformed_refs:
        pack["unavailable"].extend(
            {"source": "task_reference", "task_id": ref["task_id"],
             "status": "reference_not_current"}
            for ref in unbound_tasks
        )
        if malformed_refs:
            pack["unavailable"].append({"source": "business_reference", "status": "invalid"})
        pack["coverage"].update({"complete": False, "reason": "reference_not_current"})
        return _finish_budget(pack, max_chars)
    expected_versions: dict[str, str] = {}
    for ref in refs:
        if isinstance(ref, dict) and isinstance(ref.get("uri"), str) and isinstance(ref.get("version"), str):
            uri, version = ref["uri"], ref["version"]
            if uri in expected_versions and expected_versions[uri] != version:
                pack["unavailable"].append({"source": uri, "status": "conflicting_reference_versions"})
                pack["coverage"].update({"complete": False, "reason": "reference_version_conflict"})
                return _finish_budget(pack, max_chars)
            expected_versions[uri] = version
    if continuation is not None:
        # This is a server-held session cursor, not a user/model supplied URI.
        # The domain adapter still rechecks Principal ownership on every read.
        uri = continuation.get("uri") if isinstance(continuation, dict) else None
        section = continuation.get("section") if isinstance(continuation, dict) else None
        dimension = continuation.get("dimension", "") if isinstance(continuation, dict) else None
        offset = continuation.get("next_offset") if isinstance(continuation, dict) else None
        version = continuation.get("version") if isinstance(continuation, dict) else None
        if (not isinstance(uri, str) or not isinstance(section, str)
                or not isinstance(dimension, str) or type(offset) is not int
                or offset < 1 or not isinstance(version, str) or not version):
            pack["coverage"]["reason"] = "continuation_invalid"
            return _finish_budget(pack, max_chars)
        plan = [{"uri": uri, "section": section, "dimension": dimension,
                 "next_offset": offset, "version": version}]
        if type(continuation.get("state_revision")) is int:
            plan[0]["state_revision"] = continuation["state_revision"]
    elif refs:
        # Explicit references have already been Principal-verified by turn
        # preparation. Re-read here; dynamic record URIs may not be listed in
        # the collection catalog. Untrusted section hints are still validated
        # by the authoritative read adapter below.
        raw_plan = []
        unresolved_uris = []
        for ref in refs:
            if isinstance(ref, str):
                unresolved_uris.append(ref)
            elif isinstance(ref, dict) and isinstance(ref.get("uri"), str):
                if ref.get("section"):
                    raw_plan.append({"uri": ref["uri"], "section": ref["section"],
                                     "dimension": ref.get("dimension") or "",
                                     "version": ref.get("version")})
                else:
                    unresolved_uris.append(ref["uri"])
        # A verified ref identifies the resource, not necessarily the needed
        # section. Let the model choose only among referenced catalog entries;
        # unknown dynamic record URIs retain their safe overview projection.
        referenced_catalog = [r for r in catalog if r.get("uri") in unresolved_uris]
        if referenced_catalog:
            selected = _select_plan(question, referenced_catalog)
            if isinstance(selected, dict):
                pack["answer_kind"] = selected.get("answer_kind", "analysis")
            proposed = selected.get("reads", []) if isinstance(selected, dict) else selected
            selected_plan = _validated_plan(proposed or [], referenced_catalog)
            for entry in selected_plan:
                if entry["uri"] in expected_versions:
                    entry["version"] = expected_versions[entry["uri"]]
            raw_plan.extend(selected_plan)
        selected_uris = {r["uri"] for r in raw_plan}
        raw_plan.extend({"uri": uri, "section": "overview"} for uri in unresolved_uris
                        if uri not in selected_uris)
        plan = []
        seen = set()
        for entry in raw_plan:
            key = (entry["uri"], entry["section"], entry.get("dimension", ""))
            if key not in seen:
                plan_entry = {"uri": key[0], "section": key[1], "dimension": key[2]}
                version = entry.get("version") or expected_versions.get(key[0])
                if version:
                    plan_entry["version"] = version
                plan.append(plan_entry)
                seen.add(key)
    else:
        selectable = [r for r in catalog if r.get("status") not in _BAD_STATUS]
        raw = _select_plan(question, selectable) if selectable else None
        if isinstance(raw, dict):
            pack["answer_kind"] = raw.get("answer_kind", "analysis")
        proposed = raw.get("reads", []) if isinstance(raw, dict) else raw
        plan = _validated_plan(proposed, selectable) if proposed is not None else []
        if not plan:
            if isinstance(raw, dict) and raw.get("needed") is False:
                pack["coverage"].update({"complete": True, "reason": "evidence_not_needed"})
                pack["status"] = "completed"
                return _finish_budget(pack, max_chars)
            pack["coverage"]["reason"] = "selection_unavailable" if raw is None else "no_valid_selection"
            if raw is None:
                pack["unavailable"].append({"source": "evidence_selection", "status": "unavailable"})
            return _finish_budget(pack, max_chars)

    if not plan:
        pack["coverage"]["reason"] = "no_valid_reference"
        return _finish_budget(pack, max_chars)

    # Only an existing server-declared chapter can be counted; neither client
    # hints nor a model-invented label establish the meaning of a section.
    if pack.get("answer_kind") == "section_count" and len(plan) == 1:
        chosen = plan[0]
        for resource in catalog:
            if resource.get("uri") != chosen["uri"]:
                continue
            for detail in resource.get("section_details") or []:
                if isinstance(detail, dict) and detail.get("section") == chosen["section"] and detail.get("title"):
                    pack["count_scope"] = {"uri": chosen["uri"], "section": chosen["section"],
                                           "title": str(detail["title"])}

    stop_reason = None
    totals: list[int] = []
    for index, selection in enumerate(plan):
        uri, section, dimension = selection["uri"], selection["section"], selection["dimension"]
        offset = selection.get("next_offset", 0)
        expected_version = selection.get("version")
        expected_state_revision = selection.get("state_revision")
        source = {"uri": uri, "section": section, "dimension": dimension,
                  "version": None, "status": "unavailable", "coverage": None}
        pack["sources"].append(source)
        while True:
            if len(pack["items"]) >= max_items:
                stop_reason = "item_limit"
                pack["continuations"].append({**selection, "next_offset": offset,
                                               "version": source["version"] or expected_version})
                break
            try:
                result = read_platform_resource(
                    mysql, student_id=student_id, uri=uri, section=section,
                    dimension=dimension, offset=offset,
                    limit=min(3, max_items - len(pack["items"])),
                )
                if not isinstance(result, dict) or result.get("success") is not True or result.get("status") in _BAD_STATUS:
                    raise LookupError("业务资料不可用")
            except (LookupError, ValueError, PermissionError):
                pack["unavailable"].append({"source": uri, "section": section, "status": "unavailable"})
                break
            except Exception:
                pack["unavailable"].append({"source": uri, "section": section, "status": "unavailable"})
                break

            if expected_version and result.get("version") != expected_version:
                pack["unavailable"].append({"source": uri, "section": section,
                                            "status": "version_changed"})
                break
            if expected_state_revision is not None and result.get("state_revision") != expected_state_revision:
                pack["unavailable"].append({"source": uri, "section": section,
                                            "status": "state_revision_changed"})
                break

            first_page = source["coverage"] is None
            source["version"] = result.get("version")
            if first_page and not expected_version:
                expected_version = source["version"]
            source["status"] = result.get("status")
            source["coverage"] = result.get("coverage")
            if type(result.get("state_revision")) is int:
                expected_state_revision = result["state_revision"]
                source["state_revision"] = expected_state_revision
                selection = {**selection, "state_revision": expected_state_revision}
            data = result.get("data")
            page = data if isinstance(data, list) else [data]
            coverage = result.get("coverage")
            if isinstance(coverage, dict) and type(coverage.get("total")) is int:
                if first_page:
                    totals.append(coverage["total"])
            for value in page:
                item = {"uri": uri, "version": result.get("version"),
                        "section": section, "offset": offset,
                        "data": value, "status": result.get("status")}
                if "state_revision" in source:
                    item["state_revision"] = source["state_revision"]
                if dimension:
                    item["dimension"] = dimension
                if len(_json(pack["items"] + [item])) > max_chars:
                    stop_reason = "char_budget"
                    pack["continuations"].append({**selection, "next_offset": offset,
                                                   "version": source["version"]})
                    break
                pack["items"].append(item)
                offset += 1
            if stop_reason:
                break
            if not isinstance(coverage, dict) or not coverage.get("has_more"):
                break
            expected = coverage.get("next_offset")
            if type(expected) is not int or expected != offset or not page:
                pack["unavailable"].append({"source": uri, "section": section, "status": "invalid_coverage"})
                break
        if stop_reason:
            for remaining in plan[index + 1:]:
                pack["continuations"].append({**remaining, "next_offset": 0,
                                               "version": None})
            break

    pack["has_more"] = bool(pack["continuations"])
    pack["truncated"] = stop_reason in {"item_limit", "char_budget"}
    pack["next_offset"] = (pack["continuations"][0]["next_offset"]
                           if pack["continuations"] else None)
    pack["total"] = sum(totals) if len(totals) == len(plan) else None
    complete = not pack["has_more"] and not pack["unavailable"]
    pack["coverage"] = {
        "complete": complete, "returned": len(pack["items"]), "total": pack["total"],
        "reason": stop_reason or ("source_unavailable" if pack["unavailable"] else "complete"),
    }
    pack["status"] = "completed" if complete else ("partial" if pack["items"] else "unavailable")
    return _finish_budget(pack, max_chars)
