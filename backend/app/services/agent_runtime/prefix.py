"""
Stable prompt prefixes per interaction mode.

Goal: keep manual + mode system (+ optional tool schema digest) stable within a
session so future prompt-cache layers can hit; do not reshuffle tool lists mid-session.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .modes import InteractionMode
from .prompt_selector import PromptSelectionKey, get_prompt_selector
from .tool_policy import describe_allowlist

# Session-scoped cache: (mode, tool_digest_key) -> prefix
_PREFIX_CACHE: Dict[str, str] = {}


def tool_schema_digest(subagent: str) -> str:
    names = describe_allowlist(subagent)
    # Stable sorted list — never reorder across turns
    return ", ".join(sorted(names)) if names else "(none)"


def build_mode_prefix(
    mode: InteractionMode,
    *,
    memory_mount: str = "",
    summary: str = "",
    blackboard_card: str = "",
    agent_tool_digest: str = "",
) -> str:
    """
    Assemble stable prefix. Variable slots (memory/summary/card) append AFTER
    the frozen head so the head bytes stay cacheable.
    """
    cache_key = f"{mode.value}|tools:{agent_tool_digest}"
    head = _PREFIX_CACHE.get(cache_key)
    if head is None:
        bundle = get_prompt_selector().select(
            PromptSelectionKey(
                engine="supervisor",
                interaction_mode=mode,
                content_kind="text",
            )
        )
        parts = [bundle.system.strip()]
        if mode == InteractionMode.AGENT and agent_tool_digest:
            parts.append(f"【本会话工具表摘要】\n{agent_tool_digest}")
        if mode == InteractionMode.EXPERTS:
            parts.append("【Experts】仅执行白名单计划步骤；每步成功才 commit。")
        head = "\n\n".join(p for p in parts if p)
        _PREFIX_CACHE[cache_key] = head

    tail: List[str] = []
    if memory_mount:
        tail.append("【挂载记忆只读】\n" + memory_mount)
    if summary:
        tail.append("【对话摘要】\n" + summary)
    if blackboard_card:
        tail.append(blackboard_card)
    if not tail:
        return head
    return head + "\n\n" + "\n\n".join(tail)


def clear_prefix_cache() -> None:
    _PREFIX_CACHE.clear()
