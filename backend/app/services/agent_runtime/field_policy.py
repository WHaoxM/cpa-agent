"""
Field policy: never / model_ok / public projections.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Union

from .field_keys import NEVER_KEYS, PUBLIC_KEYS
from .tool_egress import redact_text


class FieldLevel(str, Enum):
    NEVER = "never"
    MODEL_OK = "model_ok"
    PUBLIC = "public"


def classify_key(key: str) -> FieldLevel:
    lk = str(key).lower()
    if lk in NEVER_KEYS or lk.endswith("_json") or lk.endswith("_token"):
        return FieldLevel.NEVER
    if lk in PUBLIC_KEYS:
        return FieldLevel.PUBLIC
    return FieldLevel.MODEL_OK


def project_row(row: Dict[str, Any], *, for_model: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (row or {}).items():
        level = classify_key(k)
        if level == FieldLevel.NEVER:
            out[k] = "[redacted]"
            continue
        if not for_model and level != FieldLevel.PUBLIC:
            continue
        if isinstance(v, (dict, list)):
            out[k] = "[complex]"
        else:
            s = str(v) if v is not None else ""
            out[k] = redact_text(s, max_len=120) if for_model else redact_text(s, max_len=60)
    return out


def project_tool_result_text(
    text: str,
    *,
    for_model: bool,
    max_len: Optional[int] = None,
) -> str:
    """Project unstructured tool text through PII redaction + json scrub."""
    if not text:
        return ""
    if max_len is None:
        max_len = 4000 if for_model else 200
    out = redact_text(str(text), max_len=max_len)
    # Strip inline never-key assignments often produced by db_query
    for key in NEVER_KEYS:
        out = re.sub(
            rf"{key}=([^\s,]+)",
            f"{key}=[redacted]",
            out,
            flags=re.IGNORECASE,
        )
    if not for_model:
        # UI: collapse to short status-oriented line when dump-like
        if "查询结果" in out or "phone=" in str(text).lower():
            m = re.search(r"查询结果\s*\((\d+)\s*条\)", str(text))
            n = m.group(1) if m else "?"
            return f"查询完成，{n} 条（已按字段策略投影）"
    return out


def project_document_access_for_model(text: str) -> str:
    """
    Document Access windows must not be silently head-sliced by generic tool egress.

    PII redaction only, up to one read-page (+ header). If still truncated, rewrite
    integrity flags so the model never sees complete=true with a chopped body.
    """
    if not text:
        return ""
    from .media.types import CHARS_READ_PAGE_MAX

    # Header (~400) + one page body
    budget = CHARS_READ_PAGE_MAX + 600
    raw = str(text)
    out = project_tool_result_text(raw, for_model=True, max_len=budget)
    if len(raw) > budget and out.endswith("…"):
        out = _invalidate_document_completeness(out)
    return out


def _invalidate_document_completeness(text: str) -> str:
    """If egress cut the body, honesty beats a lying complete=true header."""
    out = text
    out = re.sub(r"complete=true", "complete=false", out, count=1)
    out = re.sub(r"has_more=false", "has_more=true", out, count=1)
    out = out.replace(
        "【完整性：本段为附件全文，可据此作答】",
        "【完整性：出口层截断了窗口正文；请降低单次 max_chars 或继续分页，勿当全文】",
    )
    if "【完整性：出口层截断" not in out and "【完整性：" not in out:
        out = (
            "【完整性：工具出口截断了正文；标记已改为 has_more=true / complete=false】\n"
            + out
        )
    return out


def project_jsonish(data: Any, *, for_model: bool) -> str:
    if isinstance(data, dict):
        return json.dumps(project_row(data, for_model=for_model), ensure_ascii=False)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        rows = [project_row(r, for_model=for_model) for r in data[:5]]
        return json.dumps(rows, ensure_ascii=False)
    return project_tool_result_text(str(data), for_model=for_model)
