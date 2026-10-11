"""Hard tool allowlists per subagent + shared read-only set."""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Set

SHARED_READONLY = frozenset({
    "career_data_read",
    "graph_query",
    "normalize_entity",
    "semantic_search",
    "attachment_read",
})

# Hard partitions (plan D)
TOOL_ALLOWLIST: Dict[str, FrozenSet[str]] = {
    "profile": frozenset({
        "career_data_read",
        "pipeline_run",
        "portrait_generate",
        "skill_extract",
        "normalize_entity",
        "db_query",
        "attachment_read",
    }),
    "match": frozenset({
        "career_data_read",
        "skill_match",
        "pipeline_run",
        "semantic_search",
        "db_query",
        "normalize_entity",
        "attachment_read",
    }),
    "report": frozenset({
        "career_data_read",
        "report_generate",
        "llm_reason",
        "db_query",
        "attachment_read",
    }),
    "planner": frozenset({
        "learning_path",
        "skill_recommend",
        "graph_query",
        "llm_reason",
    }) | SHARED_READONLY,
    "qa": frozenset({
        "career_data_read",
        "graph_query",
        "semantic_search",
        "db_query",
        "normalize_entity",
        "attachment_read",
    }),
    # Supervisor has NO business tools — only orchestration decisions
    "supervisor": frozenset(),
}


def tools_for(subagent: str) -> Set[str]:
    return set(TOOL_ALLOWLIST.get(subagent, frozenset()))


def assert_tool_allowed(subagent: str, tool_name: str) -> None:
    allowed = tools_for(subagent)
    if tool_name not in allowed:
        raise PermissionError(
            f"tool '{tool_name}' not allowed for subagent '{subagent}'"
        )


def describe_allowlist(subagent: str) -> List[str]:
    return sorted(tools_for(subagent))


def visible_tools_for_interaction(interaction_mode: str, subagent: str) -> Set[str]:
    """Ask → empty; otherwise subagent allowlist."""
    if (interaction_mode or "").lower() == "ask":
        return set()
    return tools_for(subagent)
