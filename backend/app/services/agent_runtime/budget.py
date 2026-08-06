"""Slot budgets for Supervisor injection (anti-pollution hard caps).

Also exposes per-run USD / tool-call caps aligned with LiteLLM gateway ops.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Protocol

from .modes import InteractionMode

# Approximate chars ≈ tokens for CJK-heavy text (conservative: 1.5 chars/token → use chars)
TOKENS_USER_MEMORY = 1500
TOKENS_SUMMARY = 1000
TOKENS_BLACKBOARD_CARD = 800
K_TURNS = 8  # dialogue turns → ~16 messages
MAX_HISTORY_MESSAGES = K_TURNS * 2

# char budgets (roughly tokens * 2 for mixed CN/EN)
CHARS_USER_MEMORY = TOKENS_USER_MEMORY * 2
CHARS_SUMMARY = TOKENS_SUMMARY * 2
CHARS_BLACKBOARD_CARD = TOKENS_BLACKBOARD_CARD * 2


class TextEstimator(Protocol):
    """Replaceable text-size estimator; defaults to simple character counts."""

    def estimate(self, text: str) -> int:
        ...


@dataclass(frozen=True)
class CharEstimator:
    """Conservative mixed Chinese/English estimator used until tokenizers are needed."""

    def estimate(self, text: str) -> int:
        return len(text or "")


@dataclass(frozen=True)
class SlotBudget:
    max_chars: int


@dataclass(frozen=True)
class ContextBudget:
    """Hard per-slot caps for model-context assembly.

    Slots are independently capped so history, memory, attachments, and cards
    cannot push out safety rules or stable prompt heads.
    """

    summary: SlotBudget
    history: SlotBudget
    blackboard_card: SlotBudget
    memory_digest: SlotBudget
    attachment_context: SlotBudget
    estimator: TextEstimator = CharEstimator()

    def fit(self, slot: str, text: str) -> str:
        budget = getattr(self, slot)
        return truncate_slot(text or "", budget.max_chars)


CONTEXT_BUDGETS: Dict[InteractionMode, ContextBudget] = {
    InteractionMode.ASK: ContextBudget(
        summary=SlotBudget(CHARS_SUMMARY),
        history=SlotBudget(6000),
        blackboard_card=SlotBudget(400),
        memory_digest=SlotBudget(800),
        attachment_context=SlotBudget(1800),
    ),
    InteractionMode.AGENT: ContextBudget(
        summary=SlotBudget(800),
        history=SlotBudget(0),
        blackboard_card=SlotBudget(CHARS_BLACKBOARD_CARD),
        memory_digest=SlotBudget(800),
        attachment_context=SlotBudget(600),
    ),
    InteractionMode.EXPERTS: ContextBudget(
        summary=SlotBudget(800),
        history=SlotBudget(0),
        blackboard_card=SlotBudget(CHARS_BLACKBOARD_CARD),
        memory_digest=SlotBudget(800),
        attachment_context=SlotBudget(600),
    ),
}


def context_budget_for(mode: InteractionMode) -> ContextBudget:
    return CONTEXT_BUDGETS[mode]


@dataclass(frozen=True)
class RunSpendBudget:
    max_usd: float
    max_tool_calls: int
    max_iterations: int


def run_spend_budget() -> RunSpendBudget:
    """Hard caps for a single chat/run (env-overridable)."""
    try:
        max_usd = float(os.environ.get("CPA_AGENT_MAX_USD", "0.50"))
    except ValueError:
        max_usd = 0.50
    try:
        max_tools = int(os.environ.get("CPA_AGENT_MAX_TOOL_CALLS") or os.environ.get("AGENT_MAX_TOOL_CALLS") or "5")
    except ValueError:
        max_tools = 5
    try:
        max_iter = int(os.environ.get("CPA_AGENT_MAX_ITERATIONS") or os.environ.get("AGENT_MAX_ITERATIONS") or "8")
    except ValueError:
        max_iter = 8
    return RunSpendBudget(
        max_usd=max(0.0, max_usd),
        max_tool_calls=max(1, max_tools),
        max_iterations=max(1, max_iter),
    )


def spend_exceeded(spent_usd: float, tool_calls: int = 0) -> bool:
    b = run_spend_budget()
    if spent_usd > b.max_usd:
        return True
    if tool_calls > b.max_tool_calls:
        return True
    return False


def truncate_slot(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def apply_memory_budget(user_md: str, memory_md: str) -> str:
    combined = ""
    if user_md:
        combined += f"### USER.md\n{user_md.strip()}\n\n"
    if memory_md:
        combined += f"### MEMORY.md\n{memory_md.strip()}\n"
    return truncate_slot(combined.strip(), CHARS_USER_MEMORY)


def apply_summary_budget(summary: str) -> str:
    return truncate_slot(summary or "", CHARS_SUMMARY)
