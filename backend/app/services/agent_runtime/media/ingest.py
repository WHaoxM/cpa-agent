"""Ingest: bytes → normalized text + artifacts (excerpt / full / chunks).

Architecture (Claude Files):
  Upload HTTP = persist bytes + file_id (fast).
  Ingest worker = extract text (async). Never run MinerU/L0 on the request thread.

Chat purposes use light engines only (pypdf / python-docx). Heavy L0 is for
pipeline/resume parse, or explicit CPA_CHAT_INGEST_HEAVY=1 as async upgrade.
"""

from __future__ import annotations

import os
from zipfile import ZipFile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ....utils.logger import get_logger
from .types import ArtifactKind, CHARS_EXCERPT

logger = get_logger("cpa-agent.agent_runtime.media.ingest")

TEXT_EXTS = frozenset({"txt", "md", "markdown", "json", "csv", "log", "yml", "yaml"})
DOC_EXTS = frozenset({"pdf", "docx", "doc", "png", "jpg", "jpeg", "bmp", "webp", "tiff"})
AUDIO_EXTS = frozenset({"wav", "mp3", "m4a", "webm", "ogg"})
# Types that must yield text for chat use; empty extract → ingest failed
REQUIRE_TEXT_EXTS = frozenset({"pdf", "docx", "doc", "txt", "md", "markdown", "json", "csv"})

# Chat / Ask attachments — never block UI on MinerU
_CHAT_PURPOSES = frozenset({
    "chat_attachment", "chat", "ask", "voice", "attachment", "",
})
# Pipeline / research — may use L0 (still async on upload path)
_HEAVY_PURPOSES = frozenset({
    "pipeline", "resume_parse", "l0", "full_parse", "job_parse",
})


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def purpose_allows_heavy(purpose: str) -> bool:
    """
    Whether worker may invoke L0/MinerU after light extract fails.
    HTTP thread never runs heavy — only the Celery media worker.
    Chat default: escalate (many CN resumes are image-PDF). Opt-out: CPA_CHAT_INGEST_HEAVY=0.
    """
    p = (purpose or "").strip().lower()
    if p in _HEAVY_PURPOSES:
        return True
    if p in _CHAT_PURPOSES:
        return _env_bool("CPA_CHAT_INGEST_HEAVY", True)
    return False


def purpose_prefers_light(purpose: str) -> bool:
    """Chat always light-first; pipeline may heavy-first."""
    p = (purpose or "").strip().lower()
    if p in _HEAVY_PURPOSES:
        return False
    return True


def _ext(filename: str) -> str:
    return Path(filename or "").suffix.lstrip(".").lower()


def _fallback_docx(path: str) -> Tuple[str, str]:
    try:
        from ....utils.docx_text import extract_docx_text

        return extract_docx_text(path), "mammoth+python-docx"
    except Exception as e:
        return "", f"docx_extract:{e}"


def _fallback_pdf(path: str) -> Tuple[str, str]:
    # pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        if parts:
            return "\n".join(parts), "pypdf"
    except Exception as e:
        logger.debug("pypdf failed: %s", e)
    # pdfplumber
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t)
        if parts:
            return "\n".join(parts), "pdfplumber"
    except Exception as e:
        logger.debug("pdfplumber failed: %s", e)
    return "", "pdf_fallback_failed"


def _docx_has_unreadable_shapes(path: str) -> bool:
    """Structural risk only: raster text and grouped shapes need separate OCR."""
    try:
        with ZipFile(path) as package:
            if any(name.startswith("word/media/") for name in package.namelist()):
                return True
            body = package.read("word/document.xml")
            return b"<v:group" in body or b"<w:txbxContent" in body
    except Exception:
        # A damaged package must not be certified complete by a text fallback.
        return True


def extract_text_from_path(
    path: str,
    filename: str,
    *,
    prefer_light: bool = False,
    allow_heavy: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """
    prefer_light: try pypdf/docx before L0.
    allow_heavy: if False, never call L0/MinerU (chat default).
    """
    ext = _ext(filename)
    meta: Dict[str, Any] = {
        "ext": ext,
        "engine": "none",
        "attempts": [],
        "allow_heavy": allow_heavy,
        "prefer_light": prefer_light,
    }

    if ext in AUDIO_EXTS:
        return "", {
            **meta,
            "engine": "audio_pending_stt",
            "note": "audio bytes stored; await VoiceTurn STT",
        }

    if ext in TEXT_EXTS or (not ext and Path(path).suffix == ""):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            return text, {**meta, "engine": "utf8"}
        except Exception as e:
            logger.warning("text read failed: %s", e)
            return "", {**meta, "engine": "utf8_failed", "error": str(e)}

    if ext in DOC_EXTS:
        if ext == "docx":
            # Both Agent attachments and L0 use the same real media adapter.
            # This runs in the ingest worker, never on the upload HTTP thread.
            try:
                from ....utils.docx_text import extract_docx_with_media

                text, coverage = extract_docx_with_media(path, allow_ocr=allow_heavy)
                return text, {**meta, **coverage, "attempts": [coverage["engine"]]}
            except Exception as exc:
                return "", {**meta, "engine": "extract_failed", "error": str(exc),
                            "source_coverage": "unverified", "retryable": True}

        def _try_light() -> Tuple[str, str]:
            if ext == "docx":
                return _fallback_docx(path)
            if ext == "pdf":
                return _fallback_pdf(path)
            return "", "light_skip"

        def _try_l0() -> Tuple[str, Dict[str, Any]]:
            try:
                from ....pipeline.entity_extractor.l0_full_parser import L0FullParser

                parser = L0FullParser()
                result = parser.parse_file(path)
                meta["attempts"].append("l0")
                if result.success and (result.text or "").strip():
                    from ..tool_egress import redact_text

                    blocks = []
                    for b in (result.blocks or []):
                        blocks.append({
                            "block_id": getattr(b, "block_id", None),
                            "block_type": getattr(b, "block_type", None),
                            "text": redact_text(getattr(b, "text", None) or "", max_len=None),
                            "page": getattr(b, "page", None),
                        })
                    metadata = getattr(result, "metadata", None) or {}
                    return result.text, {
                        "engine": getattr(result, "engine", "l0") or "l0",
                        "block_count": len(result.blocks or []),
                        "blocks": blocks,
                        **{key: metadata[key] for key in (
                            "source_coverage", "coverage_reason", "retryable", "reading_order",
                        ) if key in metadata},
                    }
                meta["l0_error"] = getattr(result, "error", "empty")
            except Exception as e:
                meta["attempts"].append("l0_exception")
                meta["l0_error"] = str(e)
                logger.warning("L0 parse failed: %s", e)
            return "", {}

        if prefer_light or not allow_heavy:
            text, eng = _try_light()
            meta["attempts"].append(eng)
            complex_docx = ext == "docx" and _docx_has_unreadable_shapes(path)
            if complex_docx:
                meta["source_coverage"] = "unverified"
                meta["coverage_reason"] = "docx_embedded_media_or_shape"
            # PDF body headings, keywords and character count cannot prove that
            # the light parser read every page. When heavy parsing is enabled,
            # verify every PDF regardless of its apparent semantic content.
            needs_heavy = allow_heavy and (ext == "pdf" or not text.strip())
            if text.strip() and not needs_heavy:
                if ext == "pdf":
                    meta["source_coverage"] = "unverified"
                    meta["coverage_reason"] = "heavy_parse_disabled"
                return text, {
                    **meta,
                    "engine": eng if eng in ("pypdf", "pdfplumber", "mammoth+python-docx") else "light",
                }
            if needs_heavy:
                logger.info(
                    "light extract for %s (chars=%s) — verifying with L0 in ingest worker",
                    filename,
                    len((text or "").strip()),
                )
                meta["escalated_heavy"] = True
                heavy_text, extra = _try_l0()
                if heavy_text.strip():
                    return heavy_text, {**meta, **extra}
                # Keep the useful light text, but never certify its source as
                # complete after the authoritative layout/OCR pass failed.
                if text.strip():
                    return text, {
                        **meta,
                        "engine": eng if eng in ("pypdf", "pdfplumber", "mammoth+python-docx") else "light",
                        "source_coverage": "unverified",
                        "coverage_reason": "heavy_parse_failed_light_fallback",
                    }
            return "", {
                **meta,
                "engine": "extract_failed",
                "error": (
                    meta.get("l0_error")
                    or (
                        "未能提取文本（轻量引擎无结果且未启用重型解析）。"
                        "可设 CPA_CHAT_INGEST_HEAVY=1，或上传可选中文本的 PDF/DOCX。"
                        if not allow_heavy
                        else "未能从文件提取文本，请换 PDF/DOCX/TXT 重试"
                    )
                ),
            }

        # Heavy-first (pipeline purposes)
        text, extra = _try_l0()
        if text.strip():
            if ext == "docx" and _docx_has_unreadable_shapes(path):
                meta["source_coverage"] = "unverified"
                meta["coverage_reason"] = "docx_embedded_media_or_shape"
            return text, {**meta, **extra}
        text, eng = _try_light()
        meta["attempts"].append(eng)
        if text.strip():
            return text, {
                **meta,
                "engine": eng if eng in ("pypdf", "pdfplumber", "mammoth+python-docx") else "light",
                "source_coverage": "unverified",
                "coverage_reason": "heavy_parse_failed_light_fallback",
            }

        return "", {
            **meta,
            "engine": "extract_failed",
            "error": meta.get("l0_error") or "未能从文件提取文本，请换 PDF/DOCX/TXT 重试",
        }

    return "", {**meta, "engine": "unsupported", "error": f"不支持的类型 .{ext}"}


def build_artifacts(raw_text: str, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = (raw_text or "").strip()
    artifacts: List[Dict[str, Any]] = []
    if not text and meta.get("engine") == "audio_pending_stt":
        artifacts.append({
            "kind": ArtifactKind.EXCERPT.value,
            "ordinal": 0,
            "body_text": "[音频附件已入库，等待转写]",
            "meta": meta,
        })
        return artifacts

    if not text:
        artifacts.append({
            "kind": ArtifactKind.EXCERPT.value,
            "ordinal": 0,
            "body_text": "[未能提取文本]",
            "meta": meta,
        })
        return artifacts

    # Store the full extracted text; only excerpts and model-facing windows are capped.
    from ..tool_egress import redact_text

    safe_full = redact_text(text, max_len=None)
    excerpt = safe_full[:CHARS_EXCERPT] + ("…" if len(safe_full) > CHARS_EXCERPT else "")

    artifacts.append({
        "kind": ArtifactKind.EXCERPT.value,
        "ordinal": 0,
        "body_text": excerpt,
        "meta": {"chars": len(safe_full)},
    })
    artifacts.append({
        "kind": ArtifactKind.FULL_TEXT.value,
        "ordinal": 0,
        "body_text": safe_full,
        "meta": {"chars": len(safe_full), "truncated": False},
    })

    # 使用配置驱动的语义分块
    chunks = _smart_chunk_text(safe_full, meta)
    for ordinal, (offset, chunk_text) in enumerate(chunks):
        artifacts.append({
            "kind": ArtifactKind.CHUNK.value,
            "ordinal": ordinal,
            "body_text": chunk_text,
            "meta": {"offset": offset},
        })

    for i, block in enumerate(meta.get("blocks") or []):
        bt = redact_text(str(block.get("text") or ""), max_len=None)
        if not bt:
            continue
        artifacts.append({
            "kind": ArtifactKind.BLOCK.value,
            "ordinal": i,
            "body_text": bt,
            "meta": {
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "page": block.get("page"),
            },
        })

    return artifacts


def _smart_chunk_text(text: str, meta: Dict[str, Any]) -> List[Tuple[int, str]]:
    """
    智能分块：基于配置的语义分块，避免切断关键信息

    Args:
        text: 待分块的文本
        meta: 文件元数据（包含ext等信息）

    Returns:
        分块列表 [(offset, chunk_text), ...]
    """
    try:
        from config.loader import get_chunking_config
        from app.utils.chunking import semantic_chunk_text

        config = get_chunking_config()

        # 获取文件类型
        ext = meta.get('ext', '')

        # 构建文件类型特定的配置
        file_type_config = config.get('file_types', {}).get(ext, {})
        chunk_config = {
            'defaults': {
                'chunk_size': file_type_config.get('chunk_size', config.get('defaults', {}).get('chunk_size', 1200)),
                'max_chunks': config.get('defaults', {}).get('max_chunks', 40),
            },
            'semantic': config.get('semantic', {}),
        }

        return semantic_chunk_text(text, chunk_config)

    except Exception:
        # 配置加载失败，回退到固定大小分块
        chunk_size = 1200
        max_chunks = 40
        chunks: List[Tuple[int, str]] = []
        for i in range(0, len(text), chunk_size):
            if len(chunks) >= max_chunks:
                break
            chunks.append((i, text[i:i + chunk_size]))
        return chunks


def ingest_succeeded(text: str, meta: Dict[str, Any], filename: str) -> bool:
    """Audio pending STT counts as ok; docs that require text must have body."""
    ext = _ext(filename)
    if meta.get("engine") == "audio_pending_stt":
        return True
    if ext in REQUIRE_TEXT_EXTS:
        return bool((text or "").strip())
    # images: L0 may OCR; empty is soft-fail → failed for chat
    if ext in DOC_EXTS:
        return bool((text or "").strip())
    return True


def ingest_file(
    path: str,
    filename: str,
    *,
    purpose: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    if not path or not os.path.isfile(path):
        meta = {"engine": "missing_file", "error": "文件不存在"}
        return build_artifacts("", meta), meta, False
    prefer_light = purpose_prefers_light(purpose)
    allow_heavy = purpose_allows_heavy(purpose)
    text, meta = extract_text_from_path(
        path,
        filename,
        prefer_light=prefer_light,
        allow_heavy=allow_heavy,
    )
    ok = ingest_succeeded(text, meta, filename)
    if not ok and not meta.get("error"):
        meta["error"] = "未能提取可用于对话的文本"
    return build_artifacts(text, meta), meta, ok
