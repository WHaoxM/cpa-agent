"""
RAGFlow-class PDF parse Sidecar client.

CPA Flask posts the file; sidecar returns Markdown/text JSON.
Unavailable URL → DISABLED (never blocks process start).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger("cpa-agent.pdf_sidecar")


def sidecar_base_url() -> str:
    return (os.environ.get("CPA_L0_PARSE_SIDECAR_URL") or "").strip().rstrip("/")


def sidecar_timeout_sec() -> float:
    try:
        return float(os.environ.get("CPA_L0_PARSE_SIDECAR_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def probe_sidecar() -> Tuple[str, str]:
    """
    Returns (status, reason) where status in ready|disabled|failed.
    Does not block startup on network errors beyond a short health ping.
    """
    base = sidecar_base_url()
    if not base:
        return "disabled", "CPA_L0_PARSE_SIDECAR_URL unset"

    health = os.environ.get("CPA_L0_PARSE_SIDECAR_HEALTH") or f"{base}/v1/health"
    try:
        import httpx

        with httpx.Client(timeout=5.0) as client:
            r = client.get(health)
            if r.status_code < 500:
                return "ready", f"health {r.status_code}"
            return "failed", f"health HTTP {r.status_code}"
    except Exception as e:
        # Optional sidecar: treat connect errors as DISABLED so golden path uses PyMuPDF
        return "disabled", f"unreachable: {e}"


def parse_pdf_via_sidecar(file_path: str) -> Dict[str, Any]:
    """
    Upload PDF to sidecar; return {success, text, metadata, error}.
    Compatible with a thin RAGFlow-like endpoint:
      POST {base}/api/cpa/parse  multipart file=...
      → { "text"|"markdown": "...", "blocks"?: [...] }
    Override path with CPA_L0_PARSE_SIDECAR_PARSE_PATH.
    """
    base = sidecar_base_url()
    if not base:
        return {"success": False, "text": "", "error": "sidecar URL unset", "engine": "sidecar"}

    parse_path = (
        os.environ.get("CPA_L0_PARSE_SIDECAR_PARSE_PATH") or "/api/cpa/parse"
    ).strip()
    if not parse_path.startswith("/"):
        parse_path = "/" + parse_path
    url = f"{base}{parse_path}"
    path = Path(file_path)
    if not path.is_file():
        return {"success": False, "text": "", "error": f"file not found: {file_path}", "engine": "sidecar"}

    try:
        import httpx

        with path.open("rb") as fh, httpx.Client(timeout=sidecar_timeout_sec()) as client:
            r = client.post(
                url,
                files={"file": (path.name, fh, "application/pdf")},
            )
        if r.status_code >= 400:
            return {
                "success": False,
                "text": "",
                "error": f"sidecar HTTP {r.status_code}: {r.text[:300]}",
                "engine": "sidecar",
            }
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(data, dict):
            data = {"text": str(data)}
        text = (
            data.get("text")
            or data.get("markdown")
            or data.get("md")
            or data.get("content")
            or ""
        )
        if isinstance(text, list):
            text = "\n".join(str(x) for x in text)
        text = str(text or "").strip()
        if not text:
            return {
                "success": False,
                "text": "",
                "error": "sidecar returned empty text",
                "engine": "sidecar",
                "metadata": data,
            }
        return {
            "success": True,
            "text": text,
            "blocks": data.get("blocks") or [],
            "metadata": {
                "file": file_path,
                "engine": "sidecar",
                "sidecar_url": base,
            },
            "engine": "sidecar",
        }
    except Exception as e:
        logger.warning("sidecar parse failed: %s", e)
        return {"success": False, "text": "", "error": str(e), "engine": "sidecar"}
