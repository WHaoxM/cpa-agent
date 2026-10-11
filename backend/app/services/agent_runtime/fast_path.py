"""Normalize legacy frontend profile wrappers at the conversation boundary.

Semantic intent is resolved by the model-backed router in ``mode_runtime``.
Do not add keyword tables or fixed replies here.
"""

from __future__ import annotations

import re


_PROFILE_PREFIX_RE = re.compile(
    r"^\[用户概况\][\s\S]*?\|\s*询问：\s*",
    re.UNICODE,
)


def visible_user_text(message: str) -> str:
    """Drop an obsolete synthetic prefix without interpreting the user's intent."""
    text = (message or "").strip()
    if not text:
        return ""
    cleaned = _PROFILE_PREFIX_RE.sub("", text).strip()
    return cleaned or text
