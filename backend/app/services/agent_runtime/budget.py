"""Slot budgets for Supervisor injection (anti-pollution hard caps).

Also exposes per-run USD / tool-call caps aligned with LiteLLM gateway ops.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from .modes import InteractionMode


@dataclass(frozen=True)
class ModelCapability:
    """A model-specific window, never inherited after a model override."""

    provider: str
    model: str
    context_window_tokens: Optional[int]
    max_output_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    tokenizer: str = "unknown"
    source: str = "unknown"


@dataclass(frozen=True)
class ContextWindowStatus:
    provider: str
    model: str
    context_window_tokens: Optional[int]
    input_limit_tokens: Optional[int]
    estimated_input_tokens: Optional[int]
    remaining_input_tokens: Optional[int]
    requested_output_tokens: int
    fits: Optional[bool]
    estimate_method: str
    reason: str


# Request-local observation only: provider usage is authoritative, estimates are
# never substituted for missing usage. Background jobs need their own capture.
_active_usage_ledger: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "cpa_agent_usage_ledger", default=None,
)


@contextmanager
def capture_usage_ledger(mode: str, *, turn_token_cap: Optional[int] = None,
                         session_id: Optional[str] = None, student_id: Optional[str] = None):
    cap = turn_token_cap or _positive_env_int("CPA_AGENT_TURN_TOKEN_CAP") or 30000
    ledger: Dict[str, Any] = {
        "mode": mode, "turn_token_cap": cap, "expected_call_ids": [], "calls": [],
        "usage_turn_id": str(uuid4()), "session_id": session_id,
        "student_id": student_id, "background_jobs": [],
    }
    token = _active_usage_ledger.set(ledger)
    try:
        yield ledger
    finally:
        _active_usage_ledger.reset(token)


def current_usage_ledger() -> Optional[Dict[str, Any]]:
    return _active_usage_ledger.get()


def require_turn_token_capacity(requested_output_tokens: int, *, estimated_input_tokens: int = 0) -> None:
    ledger = current_usage_ledger()
    if ledger is None:
        return
    if ledger.get("stop_reason"):
        raise RuntimeError(ledger["stop_reason"])
    calls = ledger["calls"]
    if any(
        not isinstance(call.get("usage"), dict)
        or not all(
            type(call["usage"].get(key)) is int and call["usage"][key] >= 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        for call in calls
    ):
        ledger["stop_reason"] = "turn_token_usage_unverified"
        raise RuntimeError("turn_token_usage_unverified: previous provider omitted usage")
    used = sum(call["usage"]["total_tokens"] for call in calls)
    # Reserve the complete prospective call, not merely its output allowance.
    # Provider-reported usage replaces this reservation after the response.
    if used + max(0, int(estimated_input_tokens)) + max(0, int(requested_output_tokens)) > ledger["turn_token_cap"]:
        ledger["stop_reason"] = "turn_token_cap_exceeded"
        raise RuntimeError("turn_token_cap_exceeded")


def record_model_call(
    *, provider: str, model: str, stage: str, usage: Optional[Dict[str, int]],
    preflight: ContextWindowStatus, finish_reason: Optional[str],
) -> None:
    ledger = current_usage_ledger()
    if ledger is None:
        return
    call_id = f"{stage}:{len(ledger['calls']) + 1}"
    ledger["expected_call_ids"].append(call_id)
    ledger["calls"].append({
        "call_id": call_id, "stage": stage, "provider": provider, "model": model,
        "usage": dict(usage) if usage else None,
        "preflight": {
            "fits": preflight.fits,
            "final_messages_checked": True,
            "reason": preflight.reason,
            "estimated_input_tokens": preflight.estimated_input_tokens,
            "input_limit_tokens": preflight.input_limit_tokens,
        },
        "finish_reason": finish_reason,
    })
    if not usage or any(type(usage.get(k)) is not int or usage[k] < 0
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        ledger["stop_reason"] = "turn_token_usage_unverified"
    elif finish_reason == "length":
        ledger["stop_reason"] = "model_output_truncated"
    elif sum((call.get("usage") or {}).get("total_tokens", 0)
             for call in ledger["calls"]) > ledger["turn_token_cap"]:
        ledger["stop_reason"] = "turn_token_cap_exceeded"


def _positive_env_int(name: str) -> Optional[int]:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return None


def model_capability_for(provider: str, model: str) -> ModelCapability:
    """Read the exact preset capability or an explicit deployment override.

    The provider's preset does not describe a different model selected through
    LLM_MODEL_NAME/model_override. For self-hosted models, operators must declare
    the actual server window with CPA_LLM_CONTEXT_WINDOW_TOKENS_<PROVIDER>.
    """
    from config.integration_loader import get_llm_provider_presets

    provider = (provider or "").lower()
    model = model or ""
    env_suffix = "".join(char if char.isalnum() else "_" for char in provider.upper())
    env_window = _positive_env_int(f"CPA_LLM_CONTEXT_WINDOW_TOKENS_{env_suffix}")
    if env_window is not None:
        return ModelCapability(
            provider=provider,
            model=model,
            context_window_tokens=env_window,
            max_output_tokens=_positive_env_int(f"CPA_LLM_MAX_OUTPUT_TOKENS_{env_suffix}"),
            max_input_tokens=_positive_env_int(f"CPA_LLM_MAX_INPUT_TOKENS_{env_suffix}"),
            tokenizer="tiktoken" if provider == "openai" else "utf8_bytes_conservative",
            source="deployment_env",
        )

    preset = get_llm_provider_presets().get(provider) or {}
    capability = (preset.get("capability") or {}) if model == preset.get("model") else {}
    return ModelCapability(
        provider=provider,
        model=model,
        context_window_tokens=capability.get("context_window_tokens"),
        max_output_tokens=capability.get("max_output_tokens"),
        max_input_tokens=capability.get("max_input_tokens"),
        tokenizer=capability.get("tokenizer", "unknown"),
        source="preset_exact_model" if capability else "unknown",
    )


def _estimate_chat_tokens(messages: Sequence[Mapping[str, Any]], capability: ModelCapability) -> tuple[Optional[int], str]:
    """Estimate text-only Chat Completions input; never call this exact usage."""
    if any(not isinstance(message.get("content", ""), str) for message in messages):
        return None, "multimodal_or_nontext_unknown"
    if capability.tokenizer == "tiktoken":
        try:
            import tiktoken

            encoding = tiktoken.encoding_for_model(capability.model)
            total = 3  # OpenAI Cookbook Chat Completions reply priming estimate.
            for message in messages:
                total += 3
                for key, value in message.items():
                    payload = value if isinstance(value, str) else __import__("json").dumps(value, ensure_ascii=False)
                    total += len(encoding.encode(payload))
                    if key == "name":
                        total += 1
            return total, "tiktoken_chat_estimate"
        except (ImportError, KeyError):
            return None, "tiktoken_unavailable_or_model_unknown"
    if capability.tokenizer == "utf8_bytes_conservative":
        # For non-OpenAI models we do not claim tokenizer parity. Byte count is
        # deliberately pessimistic for UTF-8/CJK, with explicit framing slack.
        import json

        total = 16
        for message in messages:
            total += 32
            total += len(json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return total, "utf8_bytes_conservative_estimate"
    return None, "tokenizer_unknown"


def context_window_status(
    provider: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    max_output_tokens: int,
    *,
    safety_margin_tokens: int = 256,
) -> ContextWindowStatus:
    """OpenCode-style usable window plus Codex-style remaining-token signal.

    Unknown capability/tokenizer => fits=None; callers must not treat that as
    permission to submit a large prompt. Actual provider usage is authoritative.
    """
    capability = model_capability_for(provider, model)
    output = max(0, int(max_output_tokens))
    margin = max(0, int(safety_margin_tokens))
    if capability.context_window_tokens is None:
        return ContextWindowStatus(provider, model, None, None, None, None, output, None, "unknown", "model_capability_unknown")
    limit = max(0, capability.context_window_tokens - output - margin)
    if capability.max_input_tokens is not None:
        limit = min(limit, capability.max_input_tokens)
    estimate, method = _estimate_chat_tokens(messages, capability)
    if capability.max_output_tokens is not None and output > capability.max_output_tokens:
        return ContextWindowStatus(provider, model, capability.context_window_tokens, limit, estimate, None, output, False, method, "output_limit_exceeded")
    if estimate is None:
        return ContextWindowStatus(provider, model, capability.context_window_tokens, limit, None, None, output, None, method, "input_estimate_unknown")
    remaining = limit - estimate
    return ContextWindowStatus(
        provider, model, capability.context_window_tokens, limit, estimate, remaining,
        output, remaining >= 0, method,
        "fits_estimate" if remaining >= 0 else "estimated_input_over_window",
    )

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
    evidence_context: SlotBudget
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
        evidence_context=SlotBudget(6000),
    ),
    InteractionMode.AGENT: ContextBudget(
        summary=SlotBudget(800),
        history=SlotBudget(0),
        blackboard_card=SlotBudget(CHARS_BLACKBOARD_CARD),
        memory_digest=SlotBudget(800),
        attachment_context=SlotBudget(600),
        evidence_context=SlotBudget(0),
    ),
    InteractionMode.EXPERTS: ContextBudget(
        summary=SlotBudget(800),
        history=SlotBudget(0),
        blackboard_card=SlotBudget(CHARS_BLACKBOARD_CARD),
        memory_digest=SlotBudget(800),
        attachment_context=SlotBudget(600),
        # Same atomic, read-only EvidencePack contract as Ask.  This slot feeds
        # the Lead's bounded router context; it does not expand Experts tool or
        # blackboard-write permissions.
        evidence_context=SlotBudget(6000),
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
