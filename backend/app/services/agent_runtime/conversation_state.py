"""Validated, source-anchored continuity state for long conversations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConversationFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=160)
    source_message_id: str = Field(min_length=1, max_length=96)
    source_quote: str = Field(min_length=1, max_length=160)


class ConversationSupersession(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    superseded: ConversationFact
    replacement: ConversationFact


class ConversationStateV1(BaseModel):
    """Only user-asserted facts with exact original quotes are admissible."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=1)
    through_message_count: int = Field(ge=0)
    current_goal: Optional[ConversationFact] = None
    active_constraints: List[ConversationFact] = Field(default_factory=list, max_length=8)
    confirmed_decisions: List[ConversationFact] = Field(default_factory=list, max_length=8)
    open_questions: List[ConversationFact] = Field(default_factory=list, max_length=8)
    superseded: List[ConversationSupersession] = Field(default_factory=list, max_length=8)


def validate_conversation_state(
    value: Any,
    messages: List[Dict[str, Any]],
    *,
    expected_through_count: Optional[int] = None,
) -> ConversationStateV1:
    """Validate schema and prove every state fact points to exact user text."""
    state = ConversationStateV1.model_validate(value)
    if state.through_message_count > len(messages):
        raise ValueError("conversation state extends beyond stored history")
    if expected_through_count is not None and state.through_message_count != expected_through_count:
        raise ValueError("conversation state does not cover the processed prefix")

    positions: Dict[str, int] = {}
    for index, message in enumerate(messages[:state.through_message_count]):
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message.get("role") == "user":
            positions[message_id] = index

    facts: List[ConversationFact] = []
    if state.current_goal:
        facts.append(state.current_goal)
    facts.extend(state.active_constraints)
    facts.extend(state.confirmed_decisions)
    facts.extend(state.open_questions)
    for relation in state.superseded:
        facts.extend((relation.superseded, relation.replacement))

    for fact in facts:
        source_index = positions.get(fact.source_message_id)
        if source_index is None:
            raise ValueError("conversation state source is not an earlier user message")
        source_content = str(messages[source_index].get("content") or "")
        if fact.source_quote not in source_content:
            raise ValueError("conversation state quote is not present in its source message")

    for relation in state.superseded:
        if positions[relation.superseded.source_message_id] >= positions[relation.replacement.source_message_id]:
            raise ValueError("superseded state must precede its replacement")
    return state


def load_stored_conversation_state(value: Any) -> Optional[Dict[str, Any]]:
    """Drop malformed Redis snapshots instead of letting them steer a turn."""
    try:
        return ConversationStateV1.model_validate(value).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return None
