"""Read a persisted, typed component snapshot from the bound Ask session.

The session and owner are captured by the server when the tool is built. The
model may select a component/entity identity, but cannot choose another user or
session. This is a read-only continuation path for data cards omitted from the
bounded prompt context.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


_COMPONENT_ID = re.compile(r"cmp_[a-f0-9]{24}")
_ENTITY_ID = re.compile(r"ent_[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")


class ComponentSnapshotReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    component_id: str = Field(..., description="当前授权会话中的 data_card component_id")
    schema_version: int = Field(..., description="组件 schema 版本；当前只支持 1")
    entity_id: Optional[str] = Field(None, description="可选：卡片内稳定实体 ID")
    entity_version: Optional[str] = Field(None, description="可选：实体快照版本；与 entity_id 同时提供")


def create_component_snapshot_read_tool(
    *,
    student_id: Optional[str],
    session_id: Optional[str],
) -> StructuredTool:
    """Create a tool whose authorization scope is immutable for one turn."""

    def _run(
        component_id: str,
        schema_version: int,
        entity_id: Optional[str] = None,
        entity_version: Optional[str] = None,
    ) -> str:
        if not student_id or not session_id:
            return json.dumps({"status": "unavailable", "reason": "session_scope_missing"})
        if not _COMPONENT_ID.fullmatch(component_id or ""):
            return json.dumps({"status": "invalid_request", "reason": "invalid_component_id"})
        if type(schema_version) is not int or schema_version != 1:
            return json.dumps({"status": "unavailable", "reason": "unsupported_schema_version"})
        if entity_id is not None and not _ENTITY_ID.fullmatch(entity_id):
            return json.dumps({"status": "invalid_request", "reason": "invalid_entity_id"})
        if entity_version is not None and (
            entity_id is None or not isinstance(entity_version, str) or not 1 <= len(entity_version) <= 80
        ):
            return json.dumps({"status": "invalid_request", "reason": "invalid_entity_version"})

        from ..session_manager import get_session_manager
        from ..agent_runtime.turn_contract import normalize_message_parts

        manager = get_session_manager()
        get_component = getattr(manager, "get_owner_scoped_component_message_by_id", None)
        if callable(get_component):
            message = get_component(session_id, student_id, component_id)
            if message is None:
                return json.dumps({"status": "unavailable", "reason": "session_not_available"})
            raw_parts = message.get("parts_v1")
            candidates = [(message, card) for card in normalize_message_parts(raw_parts or [])]
        else:
            # Compatibility for simple injected session-manager fakes.
            state = manager.get_session(session_id)
            if state is None or state.student_id != student_id:
                return json.dumps({"status": "unavailable", "reason": "session_not_available"})
            candidates = []
            for legacy_message in reversed(state.messages):
                if not isinstance(legacy_message, dict) or legacy_message.get("role") != "assistant":
                    continue
                metadata = legacy_message.get("metadata")
                raw_parts = metadata.get("parts_v1") if isinstance(metadata, dict) else None
                if isinstance(raw_parts, list):
                    candidates.extend((legacy_message, card) for card in normalize_message_parts(raw_parts))

        for message, card in candidates:
            if card.get("type") == "data_card" and card.get("component_id") == component_id:
                if card.get("schema_version") != schema_version:
                    return json.dumps({"status": "unavailable", "reason": "schema_mismatch"})
                if entity_id is None:
                    result = {
                        "status": "completed",
                        "snapshot_kind": "historical_read_only",
                        "message_id": str(message.get("message_id") or "")[:96],
                        "component": card,
                    }
                    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

                for item in card.get("items") or []:
                    if item.get("entity_id") != entity_id:
                        continue
                    if entity_version is not None and item.get("entity_version") != entity_version:
                        return json.dumps({
                            "status": "stale",
                            "reason": "entity_version_mismatch",
                            "component_id": component_id,
                            "schema_version": schema_version,
                        }, ensure_ascii=False)
                    result = {
                        "status": "completed",
                        "snapshot_kind": "historical_read_only",
                        "message_id": str(message.get("message_id") or "")[:96],
                        "component_id": component_id,
                        "schema_version": schema_version,
                        "source_refs": card.get("source_refs") or [],
                        "generated_at": card.get("generated_at"),
                        "entity": item,
                    }
                    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                return json.dumps({"status": "unavailable", "reason": "entity_not_in_snapshot"})
        return json.dumps({"status": "unavailable", "reason": "component_not_in_session"})

    return StructuredTool.from_function(
        func=_run,
        name="component_snapshot_read",
        description=(
            "Read one previously persisted data_card snapshot from this authorized Ask session. "
            "Use the exact component_id/schema_version in the server-provided component index; "
            "for a card item, include its stable entity_id and entity_version when known. "
            "This only reads a historical snapshot and never refreshes or changes it."
        ),
        args_schema=ComponentSnapshotReadInput,
    )
