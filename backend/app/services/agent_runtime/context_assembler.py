"""Least-privilege context assembly for Ask / Agent / Experts model calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .blackboard import Blackboard
from .budget import MAX_HISTORY_MESSAGES, context_budget_for, truncate_slot
from .modes import InteractionMode
from .prefix import build_mode_prefix


@dataclass(frozen=True)
class TurnInputs:
    mode: InteractionMode
    message: str
    blackboard: Blackboard
    history: List[Dict[str, str]] = field(default_factory=list)
    conversation_summary: str = ""
    memory_digest: str = ""
    attachment_context: str = ""
    agent_tool_digest: str = ""


@dataclass(frozen=True)
class AssembledContext:
    system_head: str
    system_tail: str
    messages: List[Dict[str, str]]
    router_context: str
    memory_digest: str
    attachment_context: str

    @property
    def system_prompt(self) -> str:
        return "\n\n".join(p for p in (self.system_head, self.system_tail) if p)


class DefaultContextAssembler:
    """Build bounded model inputs without mixing access control concerns.

    RequestScope decides who can access data. This class decides which bytes are
    injected into each model call and slot.
    """

    def assemble(self, inputs: TurnInputs) -> AssembledContext:
        budget = context_budget_for(inputs.mode)
        summary = budget.fit("summary", inputs.conversation_summary)
        memory = budget.fit("memory_digest", inputs.memory_digest)
        attachment = budget.fit("attachment_context", inputs.attachment_context)

        user_facing_card = inputs.mode == InteractionMode.ASK
        card = inputs.blackboard.capability_card(
            max_chars=budget.blackboard_card.max_chars,
            user_facing=user_facing_card,
            filled_only=not user_facing_card,
        )
        card = budget.fit("blackboard_card", card)

        head = build_mode_prefix(
            inputs.mode,
            agent_tool_digest=inputs.agent_tool_digest if inputs.mode != InteractionMode.ASK else "",
        )

        if inputs.mode == InteractionMode.ASK:
            tail = self._ask_tail(
                summary=summary,
                memory=memory,
                card=card,
                attachment=attachment,
            )
            messages = self._ask_messages(inputs.history, inputs.message)
            return AssembledContext(
                system_head=head,
                system_tail=tail,
                messages=messages,
                router_context="",
                memory_digest=memory,
                attachment_context=attachment,
            )

        router_context = self._router_context(
            summary=summary,
            card=card,
            memory=memory,
            attachment=attachment,
            message=inputs.message,
        )
        return AssembledContext(
            system_head=head,
            system_tail="",
            messages=[],
            router_context=router_context,
            memory_digest=memory,
            attachment_context=attachment,
        )

    def _ask_tail(
        self,
        *,
        summary: str,
        memory: str,
        card: str,
        attachment: str,
    ) -> str:
        parts: List[str] = []
        if memory:
            parts.append("【挂载记忆只读】\n" + memory)
        if summary:
            parts.append("【对话摘要】\n" + summary)
        if card:
            parts.append(card)
        if attachment:
            parts.append(attachment)
        return "\n\n".join(parts)

    def _ask_messages(
        self,
        history: List[Dict[str, str]],
        message: str,
    ) -> List[Dict[str, str]]:
        current = (message or "").strip()
        cleaned: List[Dict[str, str]] = []
        for item in history[-MAX_HISTORY_MESSAGES:]:
            role = str(item.get("role") or "").strip()
            if role not in ("user", "assistant"):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            cleaned.append({"role": role, "content": content})

        if cleaned and cleaned[-1]["role"] == "user" and cleaned[-1]["content"] == current:
            cleaned = cleaned[:-1]
        if current:
            cleaned.append({"role": "user", "content": current})
        return cleaned[-(MAX_HISTORY_MESSAGES + 1):]

    def _router_context(
        self,
        *,
        summary: str,
        card: str,
        memory: str,
        attachment: str,
        message: str,
    ) -> str:
        parts = []
        if card:
            parts.append(card)
        if summary:
            parts.append("【摘要】\n" + summary)
        if memory:
            parts.append("【记忆摘要】\n" + memory)
        if attachment:
            parts.append("【附件摘要】\n" + attachment)
        parts.append("【当前用户】\n" + truncate_slot(message or "", 1200))
        return "\n\n".join(parts)
