"""Discover persisted typed cards in the exact owner-bound conversation."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class ComponentSnapshotDiscoverInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field("", max_length=160, description="可选的卡片主题/项目名；留空列出近期快照")
    offset: int = Field(0, ge=0, le=1000, description="匹配快照分页偏移")
    limit: int = Field(5, ge=1, le=5)


def _search_terms(query: str) -> List[str]:
    terms = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]{2,}", query.casefold())
    return list(dict.fromkeys(terms))[:24]


def _card_metadata(card: Dict[str, Any], message: Dict[str, Any], ordinal: int) -> Dict[str, Any]:
    items = []
    for index, item in enumerate(card.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        entity_version = item.get("entity_version")
        if not isinstance(entity_id, str) or not re.fullmatch(r"ent_[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", entity_id):
            continue
        if not isinstance(entity_version, str) or not 1 <= len(entity_version) <= 80:
            continue
        label = str(item.get("label") or item.get("name") or item.get("title") or "")[:120]
        value = str(item.get("value") or item.get("summary") or item.get("description") or "")[:200]
        items.append({
            "item_ordinal": index,
            "label": label,
            "value_preview": value,
            "entity_id": entity_id,
            "entity_version": entity_version,
        })
    return {
        "component_ordinal": ordinal,
        "component_id": card.get("component_id"),
        "component_type": card.get("type"),
        "schema_version": card.get("schema_version"),
        "message_id": str(message.get("message_id") or "")[:96],
        "title": str(card.get("title") or card.get("card_type") or "历史数据卡片")[:120],
        "generated_at": card.get("generated_at"),
        "source_refs": card.get("source_refs") or [],
        "item_count": len(card.get("items") or []),
        "items": items[:20],
    }


def create_component_snapshot_discover_tool(
    *, student_id: Optional[str], session_id: Optional[str],
) -> StructuredTool:
    """Build a discovery tool whose session and owner cannot be model-selected."""

    def _run(query: str = "", offset: int = 0, limit: int = 5) -> str:
        if not student_id or not session_id:
            return json.dumps({"status": "unavailable", "reason": "session_scope_missing", "cards": []})
        if type(offset) is not int or not 0 <= offset <= 1000 or type(limit) is not int or not 1 <= limit <= 5:
            return json.dumps({"status": "invalid_request", "reason": "invalid_result_window", "cards": []})
        if not isinstance(query, str) or len(query) > 160:
            return json.dumps({"status": "invalid_request", "reason": "invalid_query", "cards": []})

        from ..session_manager import get_session_manager
        from ..agent_runtime.turn_contract import normalize_message_parts

        page = get_session_manager().get_owner_scoped_message_page(
            session_id,
            student_id,
            offset=0,
            limit=100,
            include_components=True,
        )
        if page.get("status") not in {"completed", "partial"}:
            return json.dumps({
                "status": page.get("status", "unavailable"),
                "reason": page.get("reason", "session_not_available"),
                "cards": [],
            }, ensure_ascii=False, separators=(",", ":"))

        all_cards: List[Dict[str, Any]] = []
        page_offset = 0
        total_messages = int(page.get("total_messages") or 0)
        # A single discovery call is bounded to 1000 messages. If it reaches
        # this ceiling, the result says partial and exposes the next page.
        max_scan = min(total_messages, 1000)
        while page_offset < max_scan:
            current = page if page_offset == 0 else get_session_manager().get_owner_scoped_message_page(
                session_id,
                student_id,
                offset=page_offset,
                limit=min(100, max_scan - page_offset),
                include_components=True,
            )
            if current.get("status") not in {"completed", "partial"}:
                break
            for message_index, message in enumerate(current.get("messages") or [], start=page_offset):
                if message.get("role") != "assistant":
                    continue
                cards = normalize_message_parts(message.get("parts_v1") or [])
                for card in cards:
                    if card.get("type") != "data_card":
                        continue
                    all_cards.append(_card_metadata(card, message, len(all_cards) + 1))
            page_offset += len(current.get("messages") or [])
            if not current.get("messages") or page_offset >= max_scan:
                break

        terms = _search_terms(query)
        if terms:
            scored = []
            for card in all_cards:
                searchable = " ".join([
                    card["title"],
                    *[str(item.get("label") or "") + " " + str(item.get("value_preview") or "")
                      for item in card["items"]],
                ]).casefold()
                score = sum(searchable.count(term) for term in terms)
                if score:
                    scored.append((score, card))
            candidates = [card for _, card in sorted(scored, key=lambda pair: (pair[0], pair[1]["component_ordinal"]), reverse=True)]
        else:
            candidates = all_cards

        total_matches = len(candidates)
        selected = candidates[offset:offset + limit]
        next_component_offset = offset + len(selected) if offset + len(selected) < total_matches else None
        scan_partial = total_messages > max_scan
        return json.dumps({
            "status": "partial" if scan_partial else "completed" if selected else "empty",
            "scanned_messages": max_scan,
            "total_messages": total_messages,
            "total_matching_components": total_matches,
            "component_offset": offset,
            "next_component_offset": next_component_offset,
            "older_message_offset": max(0, max_scan) if scan_partial else None,
            "cards": selected,
            "trust": "historical_snapshot_metadata_only",
        }, ensure_ascii=False, separators=(",", ":"))

    return StructuredTool.from_function(
        func=_run,
        name="component_snapshot_discover",
        description=(
            "Find persisted data_card snapshots in this exact authorized session when the card ID was omitted from current context. "
            "Leave query empty to list snapshots, or search a title/item label. Results preserve chronological component and item ordinals plus stable IDs/versions; "
            "use component_snapshot_read to inspect the exact snapshot or entity. If partial or paginated, do not infer that omitted snapshots do not exist. "
            "This reads historical snapshots only and never refreshes them."
        ),
        args_schema=ComponentSnapshotDiscoverInput,
    )
