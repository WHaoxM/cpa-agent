"""
LiteLLM gateway + optional Langfuse tracing hooks.

When LITELLM_BASE_URL is set, LLMClient should prefer that OpenAI-compatible proxy.
Langfuse is best-effort: missing keys → no-op.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.llmops")


def litellm_enabled() -> bool:
    return bool((os.environ.get("LITELLM_BASE_URL") or "").strip())


def litellm_config() -> Dict[str, Any]:
    return {
        "base_url": (os.environ.get("LITELLM_BASE_URL") or "").strip().rstrip("/"),
        "api_key": (os.environ.get("LITELLM_API_KEY") or os.environ.get("LLM_API_KEY") or "sk-litellm").strip(),
        "model": (os.environ.get("LITELLM_MODEL") or os.environ.get("LLM_MODEL_NAME") or "gpt-4o-mini").strip(),
        "timeout": int(os.environ.get("LITELLM_TIMEOUT") or os.environ.get("LLM_TIMEOUT") or "60"),
    }


def langfuse_enabled() -> bool:
    return bool(
        (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
        and (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    )


def langfuse_content_capture_enabled() -> bool:
    """Whether an operator explicitly permits prompt/answer export to Langfuse.

    Provider accounting (model, finish state, and token usage) is enough for
    default production observability.  Agent prompts can include a student's
    resume, contact details, or owner-scoped evidence, so content must not be
    exported merely because tracing credentials happen to be configured.
    """
    return (os.environ.get("CPA_LANGFUSE_CAPTURE_CONTENT") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


@contextmanager
def trace_generation(
    name: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    input_preview: str = "",
) -> Iterator[Dict[str, Any]]:
    """Yield an accounting bag; content export requires explicit operator opt-in."""
    bag: Dict[str, Any] = {"name": name, "metadata": metadata or {}}
    trace = None
    generation = None
    capture_content = langfuse_content_capture_enabled()
    if langfuse_enabled():
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
                host=(os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com").strip(),
            )
            trace = client.trace(name=name, metadata=metadata or {})
            generation_args: Dict[str, Any] = {"name": name}
            if capture_content and input_preview:
                generation_args["input"] = input_preview[:2000]
            generation = trace.generation(**generation_args)
            bag["_lf_generation"] = generation
            bag["_lf_client"] = client
        except Exception as e:
            logger.debug("langfuse init skipped: %s", e)
    try:
        yield bag
    finally:
        try:
            gen = bag.get("_lf_generation")
            if gen is not None:
                end_args: Dict[str, Any] = {"metadata": bag.get("end_metadata") or {}}
                if capture_content and bag.get("output"):
                    end_args["output"] = str(bag["output"])[:4000]
                gen.end(**end_args)
            client = bag.get("_lf_client")
            if client is not None and hasattr(client, "flush"):
                client.flush()
        except Exception as e:
            logger.debug("langfuse end skipped: %s", e)
