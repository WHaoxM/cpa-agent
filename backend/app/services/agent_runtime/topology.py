"""
Agent topology freeze (pattern-upgrade grill lock).

Do not register new subagents / experts without an ADR.
Harden gates, budget, and evals instead of growing headcount.
"""

from __future__ import annotations

from typing import FrozenSet

# Closed set used by router / permissions. Expanding requires ADR.
FROZEN_SUBAGENTS: FrozenSet[str] = frozenset(
    {
        "qa",
        "planner",
        "profile",
        "profile_subagent",
        "match",
        "match_subagent",
        "report",
        "report_subagent",
        "supervisor",
        "lead",
        "experts_lead",
        "ask",  # interaction shell, not a business expert
    }
)

TOPOLOGY_FROZEN = True


def normalize_subagent(name: str) -> str:
    return (name or "").strip()


def is_subagent_allowed(name: str) -> bool:
    n = normalize_subagent(name)
    if not n:
        return False
    if n in FROZEN_SUBAGENTS:
        return True
    if n.endswith("_subagent") and n[: -len("_subagent")] in FROZEN_SUBAGENTS:
        return True
    if f"{n}_subagent" in FROZEN_SUBAGENTS:
        return True
    return False


def assert_subagent_allowed(name: str) -> str:
    key = normalize_subagent(name)
    if not is_subagent_allowed(key):
        raise ValueError(
            f"subagent '{key}' blocked: topology frozen "
            f"(allowed={sorted(FROZEN_SUBAGENTS)})"
        )
    return key
