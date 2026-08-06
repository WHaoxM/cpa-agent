"""Shared types for agent media (files / voice)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class FileStatus(str, Enum):
    UPLOADING = "uploading"
    INGESTING = "ingesting"
    READY = "ready"
    FAILED = "failed"


class ArtifactKind(str, Enum):
    EXCERPT = "excerpt"
    FULL_TEXT = "full_text"
    CHUNK = "chunk"
    BLOCK = "block"


class VoiceStatus(str, Enum):
    CAPTURING = "capturing"
    TRANSCRIBING = "transcribing"
    READY = "ready"
    FAILED = "failed"


class ContentPart(TypedDict, total=False):
    type: str  # text | document | image | voice_transcript
    text: str
    file_id: str
    turn_id: str
    title: str


CHARS_MOUNT_CARD = 1200
CHARS_EXCERPT = 400
CHARS_MODEL_SNIPPET = 800
# Document access (not silent head-slice):
# - page: one tool/window return
# - inline complete: if full doc ≤ this, Ask/Agent gets the entire text once
#   (DeepSeek-style long-context pack; aligns with ingest full_text ~80k cap,
#    Ask uses ~48k so typical resumes never force blind paging)
CHARS_READ_PAGE = 6000
CHARS_READ_PAGE_MAX = 8000
CHARS_INLINE_COMPLETE = 48000
MAX_MOUNTED_FILES = 8
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

ALLOWED_UPLOAD_EXT = frozenset({
    "pdf", "docx", "doc", "txt", "md", "markdown", "json", "csv",
    "png", "jpg", "jpeg", "webp", "bmp", "tiff",
    "wav", "mp3", "m4a", "webm", "ogg",
})
