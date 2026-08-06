"""Deterministic runtime prompt selection.

PromptSelector answers one question: which registered Prompt Bundles enter a
specific model call. It does not read blackboard, memory, files, or request
objects; callers pass only an enum-like key and already-budgeted variables.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.prompt_loader import PromptRegistryError, get_prompt_registry

from .modes import InteractionMode


class PromptSelectionError(RuntimeError):
    """Raised when runtime prompt routing violates the registry contract."""


@dataclass(frozen=True)
class PromptSelectionKey:
    engine: str
    interaction_mode: InteractionMode | str
    content_kind: str = "text"
    subagent: Optional[str] = None

    @property
    def mode_value(self) -> str:
        mode = self.interaction_mode
        if isinstance(mode, InteractionMode):
            return mode.value
        return str(mode or "").strip().lower()


@dataclass(frozen=True)
class RuntimePromptBundle:
    key: PromptSelectionKey
    prompt_ids: List[str]
    system: str
    cache_key: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class PromptSelector:
    def __init__(self, registry=None, *, demo_mode: Optional[bool] = None):
        self._registry = registry or get_prompt_registry()
        self._demo_mode = _is_demo_mode() if demo_mode is None else bool(demo_mode)

    def select(
        self,
        key: PromptSelectionKey,
        *,
        variables: Optional[Dict[str, Any]] = None,
    ) -> RuntimePromptBundle:
        variables = dict(variables or {})
        route = self._registry.get_runtime_route(
            engine=key.engine,
            mode=key.mode_value,
            content_kind=key.content_kind,
            subagent=key.subagent,
        )
        if not route:
            raise PromptSelectionError(
                "missing runtime prompt route: "
                f"engine={key.engine} mode={key.mode_value} "
                f"content_kind={key.content_kind} subagent={key.subagent or '-'}"
            )
        if route.get("deprecated") and not self._demo_mode:
            raise PromptSelectionError(f"deprecated runtime prompt route: {route}")

        prompt_ids = [str(pid) for pid in route.get("prompt_ids") or []]
        rendered: List[str] = []
        try:
            for prompt_id in prompt_ids:
                entry = self._registry.get_entry(prompt_id)
                declared = list(entry.get("variables") or [])
                missing = [name for name in declared if name not in variables]
                if missing:
                    raise PromptSelectionError(
                        f"prompt_id={prompt_id} missing variables: {missing}"
                    )
                scoped_vars = {name: variables[name] for name in declared}
                rendered.append(self._registry.load(prompt_id, **scoped_vars).strip())
        except PromptRegistryError as e:
            if not (self._demo_mode and route.get("demo_fallback")):
                raise PromptSelectionError(str(e)) from e
            rendered = [str(route.get("fallback_text") or "").strip()]

        system = "\n\n".join(p for p in rendered if p)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "key": {
                        "engine": key.engine,
                        "mode": key.mode_value,
                        "kind": key.content_kind,
                        "subagent": key.subagent or "",
                    },
                    "prompt_ids": prompt_ids,
                    "variables": {k: str(v) for k, v in sorted(variables.items())},
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return RuntimePromptBundle(
            key=key,
            prompt_ids=prompt_ids,
            system=system,
            cache_key=f"{key.engine}:{key.mode_value}:{key.content_kind}:{key.subagent or '-'}:{digest}",
            metadata={
                "description": route.get("description"),
                "demo_fallback": bool(route.get("demo_fallback")),
            },
        )


_DEFAULT_SELECTOR: Optional[PromptSelector] = None


def get_prompt_selector() -> PromptSelector:
    global _DEFAULT_SELECTOR
    if _DEFAULT_SELECTOR is None:
        _DEFAULT_SELECTOR = PromptSelector()
    return _DEFAULT_SELECTOR


def _is_demo_mode() -> bool:
    raw = os.getenv("CPA_DEMO_MODE")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    try:
        from ...config import Config

        return bool(getattr(Config, "DEMO_MODE", False))
    except Exception:
        return False
