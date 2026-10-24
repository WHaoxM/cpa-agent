"""Narrow Ask access to files already mounted in the current owner session."""

from __future__ import annotations

import re
from typing import List, Optional


_ATTACHMENT_REFERENCE_TERMS = (
    "附件", "文件", "简历", "上传的",
)
_ENGLISH_ATTACHMENT_REFERENCE = re.compile(
    r"\b(?:attachments?|attached\s+(?:files?|documents?)|"
    r"(?:my|the|this|that|your)\s+resume|resume\s+(?:file|document|content|text)|cv)\b|"
    r"\.(?:pdf|docx)\b",
    re.IGNORECASE,
)


def explicitly_references_attachment(message: Optional[str]) -> bool:
    """Return whether the current Ask turn names an attached-document subject.

    This only gates a read-only tool for files already mounted in this exact
    session. It does not itself read content or search other sessions.
    """
    text = str(message or "").casefold()
    return (
        any(term in text for term in _ATTACHMENT_REFERENCE_TERMS)
        or _ENGLISH_ATTACHMENT_REFERENCE.search(text) is not None
    )


def list_owner_session_attachment_ids(
    session_id: Optional[str], student_id: Optional[str],
) -> List[str]:
    """List only resources the existing session mount layer validates for owner."""
    if not session_id or not student_id:
        return []
    from .session_resources import list_resources

    resources = list_resources(session_id, student_id=student_id)
    result: List[str] = []
    for resource in resources:
        file_id = resource.get("file_id") if isinstance(resource, dict) else None
        if isinstance(file_id, str) and file_id and file_id not in result:
            result.append(file_id)
    return result


def ask_attachment_read_scope(
    message: Optional[str],
    *,
    session_id: Optional[str],
    student_id: Optional[str],
    current_turn_file_ids: Optional[List[str]] = None,
) -> List[str]:
    """Bound Ask attachment context to this turn or explicit same-session reference."""
    current = [fid for fid in (current_turn_file_ids or []) if isinstance(fid, str) and fid]
    if current:
        return list(dict.fromkeys(current))
    if not explicitly_references_attachment(message):
        return []
    return list_owner_session_attachment_ids(session_id, student_id)
