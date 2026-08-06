"""
Budgeted attachment mount card for Supervisor (not a full-text dump).

Mirrors Claude document blocks + Qoder mounted resources.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .file_store import get_agent_file_service
from .session_resources import list_resources
from .types import CHARS_MOUNT_CARD, ContentPart, MAX_MOUNTED_FILES


def normalize_content_parts(
    message: str,
    content_parts: Optional[List[Dict[str, Any]]] = None,
    attachment_ids: Optional[List[str]] = None,
) -> List[ContentPart]:
    parts: List[ContentPart] = []
    if content_parts:
        for p in content_parts:
            if not isinstance(p, dict):
                continue
            t = (p.get("type") or "").lower()
            if t == "text" and p.get("text"):
                parts.append({"type": "text", "text": str(p["text"])})
            elif t in ("document", "image", "file") and p.get("file_id"):
                parts.append({
                    "type": "document" if t != "image" else "image",
                    "file_id": str(p["file_id"]),
                    "title": str(p.get("title") or ""),
                })
            elif t == "voice_transcript":
                parts.append({
                    "type": "voice_transcript",
                    "text": str(p.get("text") or ""),
                    "turn_id": str(p.get("turn_id") or ""),
                    "file_id": str(p.get("file_id") or ""),
                })
    for fid in attachment_ids or []:
        if fid and not any(p.get("file_id") == fid for p in parts):
            parts.append({"type": "document", "file_id": str(fid)})
    if message and not any(p.get("type") == "text" for p in parts):
        parts.insert(0, {"type": "text", "text": message})
    elif message and parts and parts[0].get("type") != "text":
        parts.insert(0, {"type": "text", "text": message})
    return parts


def user_visible_text(parts: List[ContentPart]) -> str:
    """Plain text for session history / STM (no full attachment bodies)."""
    texts = []
    for p in parts:
        if p.get("type") == "text" and p.get("text"):
            texts.append(p["text"])
        elif p.get("type") == "voice_transcript" and p.get("text"):
            texts.append(f"[语音] {p['text']}")
        elif p.get("file_id"):
            title = p.get("title") or p["file_id"]
            texts.append(f"[附件:{title}]")
    return "\n".join(texts).strip() or "[空消息]"


def build_attachment_mount_card(
    session_id: Optional[str],
    parts: Optional[List[ContentPart]] = None,
    *,
    turn_file_ids: Optional[List[str]] = None,
    student_id: Optional[str] = None,
    max_chars: int = CHARS_MOUNT_CARD,
    allow_attachment_read: bool = True,
) -> str:
    files = get_agent_file_service()
    lines = ["【本回合已挂载附件】"]
    if allow_attachment_read:
        lines.append(
            "规则: 此卡仅目录。正文由 Document Access 注入或 "
            "attachment_read(catalog|find|section|read) 读取；"
            "禁止把下方 excerpt 当全文。find/section 为局部；"
            "完整结论须 complete=true。禁止复述电话/邮箱。"
        )
    else:
        lines.append(
            "规则: 此卡为目录；正文由文档访问层注入（含完整性标记）。"
            "禁止把 excerpt 当全文。禁止复述电话/邮箱。"
        )
    seen = set()
    focus = set(turn_file_ids or [])

    # Prefer this-turn ids; fall back to session mounts for those ids
    mounted = {}
    if session_id and student_id:
        try:
            mounted = {
                r.get("file_id"): r
                for r in list_resources(session_id, student_id=student_id)
            }
        except PermissionError:
            mounted = {}

    ordered_ids: List[str] = []
    for fid in turn_file_ids or []:
        if fid and fid not in ordered_ids:
            ordered_ids.append(fid)
    for p in parts or []:
        fid = p.get("file_id")
        if fid and fid not in ordered_ids:
            ordered_ids.append(str(fid))
    if not ordered_ids:
        for fid in list(mounted.keys())[:MAX_MOUNTED_FILES]:
            ordered_ids.append(str(fid))

    for fid in ordered_ids[:MAX_MOUNTED_FILES]:
        if fid in seen:
            continue
        seen.add(fid)
        meta = files.get(fid) or {}
        r = mounted.get(fid) or {}
        name = r.get("filename") or meta.get("filename") or fid
        path = r.get("mount_path") or f"/data/{fid}"
        status = meta.get("status") or "unknown"
        tag = "本回合" if (not focus or fid in focus) else "会话内"
        # Do not embed excerpt body — models latched onto the 280-char head
        # and claimed the resume was truncated (education lives at the end).
        lines.append(
            f"- [{tag}] {name} | id={fid} | status={status} | path={path}"
        )

    if len(seen) == 0:
        return ""

    card = "\n".join(lines)
    return card if len(card) <= max_chars else card[: max_chars - 1] + "…"


def build_client_content_parts(
    message: str,
    *,
    file_ids: Optional[List[str]] = None,
    filenames: Optional[Dict[str, str]] = None,
    voice_turn_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Helper for API/tests — mirror of what frontend should send."""
    parts: List[Dict[str, Any]] = []
    if message:
        parts.append({"type": "text", "text": message})
    for fid in file_ids or []:
        parts.append({
            "type": "document",
            "file_id": fid,
            "title": (filenames or {}).get(fid, fid),
        })
    if voice_turn_id:
        parts.append({"type": "voice_transcript", "turn_id": voice_turn_id, "text": ""})
    return parts
