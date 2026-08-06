"""
Prepare a user turn: content parts → mount → visible STM text.

Shared by API (persist history) and ModeRuntime (model prefix).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .context_mount import (
    build_attachment_mount_card,
    normalize_content_parts,
    user_visible_text,
)
from .session_resources import resolve_attachment_ids
from .voice_turn import get_voice_turn_service


def prepare_user_turn(
    message: str,
    *,
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    attachment_ids: Optional[List[str]] = None,
    content_parts: Optional[List[Dict[str, Any]]] = None,
    voice_turn_id: Optional[str] = None,
    mount: bool = True,
) -> Dict[str, Any]:
    """
    Returns:
      visible_text, parts, turn_file_ids, attach_card, mount_errors, voice_deduped
    """
    parts: List[Dict[str, Any]] = list(content_parts or [])
    voice_deduped = False
    msg = (message or "").strip()

    if voice_turn_id:
        vp = get_voice_turn_service().as_content_part(voice_turn_id)
        if vp:
            vt = (vp.get("text") or "").strip()
            # Avoid duplicating transcript already present in message
            if vt and msg and (vt == msg or vt in msg):
                voice_deduped = True
                # keep turn_id metadata only — don't append second voice part
                if not any(p.get("type") == "voice_transcript" for p in parts):
                    parts.append({
                        "type": "voice_transcript",
                        "text": "",  # empty: already in text part
                        "turn_id": voice_turn_id,
                        "file_id": vp.get("file_id") or "",
                    })
            else:
                parts.append(vp)
                if not msg and vt:
                    msg = vt

    parts = normalize_content_parts(msg, parts, attachment_ids)

    # Clean empty voice_transcript text parts for visible text
    mount_errors: List[str] = []
    turn_ids: List[str] = []
    for p in parts:
        fid = p.get("file_id")
        if fid and fid not in turn_ids:
            turn_ids.append(str(fid))

    if mount and session_id and turn_ids:
        try:
            resolve_attachment_ids(session_id, turn_ids, student_id=student_id)
        except Exception as e:
            mount_errors.append(str(e))

    # Build visible STM without empty [语音] lines
    visible_parts = []
    for p in parts:
        if p.get("type") == "voice_transcript" and not (p.get("text") or "").strip():
            continue
        visible_parts.append(p)
    visible = user_visible_text(visible_parts) if visible_parts else (msg or "")

    card = build_attachment_mount_card(
        session_id, parts, turn_file_ids=turn_ids, student_id=student_id,
    )

    return {
        "visible_text": visible,
        "parts": parts,
        "turn_file_ids": turn_ids,
        "attach_card": card,
        "mount_errors": mount_errors,
        "voice_deduped": voice_deduped,
    }
