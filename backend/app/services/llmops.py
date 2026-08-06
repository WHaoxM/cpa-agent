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


@contextmanager
def trace_generation(
    name: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    input_preview: str = "",
) -> Iterator[Dict[str, Any]]:
    """Yield a mutable bag; callers may set output/usage. No-op without Langfuse."""
    bag: Dict[str, Any] = {"name": name, "metadata": metadata or {}}
    trace = None
    generation = None
    if langfuse_enabled():
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
                host=(os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com").strip(),
            )
            trace = client.trace(name=name, metadata=metadata or {})
            generation = trace.generation(
                name=name,
                input=input_preview[:2000] if input_preview else None,
            )
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
                gen.end(
                    output=(bag.get("output") or "")[:4000],
                    metadata=bag.get("end_metadata") or {},
                )
            client = bag.get("_lf_client")
            if client is not None and hasattr(client, "flush"):
                client.flush()
        except Exception as e:
            logger.debug("langfuse end skipped: %s", e)
