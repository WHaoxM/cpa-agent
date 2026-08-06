"""
Document Access Layer — Claude/Qoder Files style.

Architecture contract:
- Artifacts hold the canonical full text (and chunks).
- The model never receives a silent head-slice that pretends to be the whole file.
- Access modes (layered):
  1) complete pack — when total_chars ≤ budget, deliver full text + complete=true
  2) catalog + section TOC — progressive disclosure index with offsets
  3) find / section — query-aware or section jump (coverage=partial, never complete)
  4) windowed read — sequential offset pages until exhausted (fallback)

Raising a magic number is not a fix; completeness + navigation is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .file_store import get_agent_file_service
from .section_index import (
    build_section_index,
    find_section,
    format_sections_for_catalog,
    score_query_against_sections,
)
from .types import (
    CHARS_INLINE_COMPLETE,
    CHARS_READ_PAGE,
    CHARS_READ_PAGE_MAX,
)

# Re-export for callers
__all__ = [
    "DocWindow",
    "document_catalog",
    "read_window",
    "read_section",
    "find_windows",
    "pack_for_prompt",
    "materialize_for_ask",
    "assert_document_access",
    "DocumentAccessFacade",
    "format_window_for_model",
    "format_find_for_model",
    "format_catalog_for_model",
]


@dataclass(frozen=True)
class DocWindow:
    file_id: str
    filename: str
    total_chars: int
    offset: int
    text: str
    next_offset: Optional[int]
    has_more: bool
    complete: bool  # True only when this payload is the entire document
    section_id: str = ""
    coverage: str = ""  # "" | partial | section | full


def _full_text(file_id: str) -> Tuple[str, Dict[str, Any]]:
    svc = get_agent_file_service()
    meta = dict(svc.get(file_id) or {})
    full = svc.get_artifact(file_id, "full_text") or ""
    if full:
        meta["_text_source"] = "full_text"
        return full, meta
    # Excerpt fallback is never a complete document — callers must not mark complete=true
    excerpt = svc.get_artifact(file_id, "excerpt") or ""
    meta["_text_source"] = "excerpt_fallback"
    return excerpt, meta


def _blocks_for(file_id: str) -> List[Dict[str, Any]]:
    svc = get_agent_file_service()
    try:
        arts = svc.list_artifacts(file_id)
    except Exception:
        return []
    return [a for a in arts if a.get("kind") == "block"]


def assert_document_access(
    file_id: str,
    *,
    student_id: Optional[str],
    session_id: Optional[str],
    require_mount: bool = True,
) -> None:
    """Fail closed unless the file belongs to student_id and is mounted to session_id."""
    if not student_id:
        raise PermissionError("student_id required for document access")
    if require_mount and not session_id:
        raise PermissionError("session_id required for mounted document access")

    svc = get_agent_file_service()
    meta = svc.get(file_id)
    if not meta:
        raise PermissionError("file not found")
    owner = meta.get("student_id")
    if not owner:
        raise PermissionError("file missing student_id owner")
    if str(owner) != str(student_id):
        raise PermissionError("file student_id mismatch")

    if require_mount:
        from .session_resources import list_resources

        mounted = {
            r.get("file_id")
            for r in list_resources(str(session_id), student_id=str(student_id))
        }
        if file_id not in mounted:
            raise PermissionError("file not mounted to current session")


@dataclass(frozen=True)
class DocumentAccessFacade:
    """Scoped facade for model-facing document materialization."""

    student_id: str
    session_id: str

    def materialize_for_ask(
        self,
        file_ids: List[str],
        *,
        inline_budget: int = CHARS_INLINE_COMPLETE,
        query: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        return materialize_for_ask(
            file_ids,
            inline_budget=inline_budget,
            query=query,
            student_id=self.student_id,
            session_id=self.session_id,
        )


def document_catalog(file_id: str) -> Dict[str, Any]:
    """Index card: size + section TOC — never a truncated body standing in for the file."""
    svc = get_agent_file_service()
    meta = svc.get(file_id) or {}
    full, _ = _full_text(file_id)
    try:
        arts = svc.list_artifacts(file_id)
    except Exception:
        arts = []
    blocks = [a for a in arts if a.get("kind") == "block"]
    chunks = [a for a in arts if a.get("kind") == "chunk"]
    sections = build_section_index(full, blocks=blocks)
    outline = [str(s.get("title") or "") for s in sections if s.get("title")]
    return {
        "file_id": file_id,
        "filename": meta.get("filename") or file_id,
        "status": meta.get("status"),
        "total_chars": len(full),
        "chunk_count": len(chunks),
        "block_count": len(blocks),
        "sections": sections,
        "outline": outline[:16],
        "complete_inline_budget": CHARS_INLINE_COMPLETE,
        "fits_inline": len(full) <= CHARS_INLINE_COMPLETE,
    }


def format_catalog_for_model(cat: Dict[str, Any]) -> str:
    sections = cat.get("sections") or []
    toc = format_sections_for_catalog(list(sections))
    return (
        f"文件 {cat.get('filename')} (file_id={cat.get('file_id')})\n"
        f"status={cat.get('status')} total_chars={cat.get('total_chars')} "
        f"chunks={cat.get('chunk_count')} sections={len(sections)} "
        f"fits_inline={cat.get('fits_inline')}\n"
        f"章节目录（含 offset，可 mode=section / mode=find / mode=read）：\n{toc}\n"
        "策略：问题指向某段 → mode=find(query=…) 或 mode=section(section_id=sN)；"
        "需要画像级完整结论 → mode=read 从 offset=0 分页至 complete=true。"
        "find/section 命中均为 coverage=partial，禁止据此声称已读完全文。"
    )


def read_window(
    file_id: str,
    *,
    offset: int = 0,
    max_chars: int = CHARS_READ_PAGE,
    hard_cap: Optional[int] = None,
) -> DocWindow:
    """
    Windowed read. Default hard_cap is one tool page (CHARS_READ_PAGE_MAX).
    Complete-pack callers pass hard_cap=inline_budget so the full doc can fit.
    """
    full, meta = _full_text(file_id)
    name = str(meta.get("filename") or file_id)
    total = len(full)
    off = max(0, int(offset or 0))
    limit = CHARS_READ_PAGE_MAX if hard_cap is None else max(int(hard_cap), 400)
    cap = min(max(int(max_chars or CHARS_READ_PAGE), 400), limit)
    excerpt_only = meta.get("_text_source") == "excerpt_fallback"
    if off >= total:
        return DocWindow(
            file_id=file_id,
            filename=name,
            total_chars=total,
            offset=off,
            text="",
            next_offset=None,
            has_more=False,
            complete=(total == 0 and not excerpt_only),
            coverage="full" if (total == 0 and not excerpt_only) else "partial",
        )
    end = min(total, off + cap)
    text = full[off:end]
    has_more = end < total
    complete = False if excerpt_only else (off == 0 and not has_more)
    return DocWindow(
        file_id=file_id,
        filename=name,
        total_chars=total,
        offset=off,
        text=text,
        next_offset=end if has_more else None,
        has_more=has_more,
        complete=complete,
        coverage="full" if complete else "partial",
    )


def read_section(
    file_id: str,
    *,
    section_id: str = "",
    title: str = "",
    max_chars: int = CHARS_READ_PAGE,
) -> DocWindow:
    full, meta = _full_text(file_id)
    name = str(meta.get("filename") or file_id)
    sections = build_section_index(full, blocks=_blocks_for(file_id))
    sec = find_section(sections, section_id=section_id, title=title)
    if not sec:
        return DocWindow(
            file_id=file_id,
            filename=name,
            total_chars=len(full),
            offset=0,
            text="",
            next_offset=None,
            has_more=bool(full),
            complete=False,
            coverage="partial",
        )
    off = int(sec["offset"])
    end = int(sec["end_offset"])
    cap = min(max(int(max_chars or CHARS_READ_PAGE), 400), CHARS_READ_PAGE_MAX)
    slice_end = min(end, off + cap)
    text = full[off:slice_end]
    section_more = slice_end < end
    doc_more = end < len(full) or section_more
    return DocWindow(
        file_id=file_id,
        filename=name,
        total_chars=len(full),
        offset=off,
        text=text,
        next_offset=slice_end if section_more else (end if end < len(full) else None),
        has_more=doc_more,
        complete=False,  # section read is never the whole document
        section_id=str(sec.get("id") or ""),
        coverage="section",
    )


def find_windows(
    file_id: str,
    *,
    query: str,
    top_k: int = 3,
    max_chars: int = 2400,
) -> List[Dict[str, Any]]:
    full, meta = _full_text(file_id)
    name = str(meta.get("filename") or file_id)
    sections = build_section_index(full, blocks=_blocks_for(file_id))
    hits = score_query_against_sections(
        query,
        sections,
        full,
        top_k=top_k,
        window=min(max(int(max_chars or 2400), 400), CHARS_READ_PAGE_MAX),
    )
    out: List[Dict[str, Any]] = []
    for h in hits:
        sec = h.get("section") or {}
        out.append({
            "file_id": file_id,
            "filename": name,
            "total_chars": len(full),
            "section_id": sec.get("id"),
            "title": sec.get("title"),
            "score": h.get("score"),
            "offset": h.get("offset"),
            "text": h.get("text") or "",
            "complete": False,
            "coverage": "partial",
            "has_more": True,
        })
    return out


def format_window_for_model(win: DocWindow) -> str:
    header = (
        f"文件 {win.filename} (file_id={win.file_id})\n"
        f"total_chars={win.total_chars} offset={win.offset} "
        f"returned={len(win.text)} has_more={str(win.has_more).lower()} "
        f"complete={str(win.complete).lower()}"
    )
    if win.section_id:
        header += f" section_id={win.section_id}"
    if win.coverage:
        header += f" coverage={win.coverage}"
    if win.complete:
        header += "\n【完整性：本段为附件全文，可据此作答】"
    elif win.coverage == "section":
        header += (
            f"\n【完整性：仅章节窗口（coverage=section），非全文；"
            f"若需全文请 mode=read 分页至 complete=true】"
        )
        if win.has_more and win.next_offset is not None:
            header += f"；本章未读完可用 offset={win.next_offset} 继续"
    elif win.has_more:
        header += (
            f"\n【完整性：以下仅为窗口，还有后续；"
            f"请用 offset={win.next_offset} 继续 attachment_read，读完再下结论】"
        )
    elif not win.complete:
        header += (
            "\n【完整性：当前窗口已读尽本地产物，但并非完整全文"
            "（例如仅有 excerpt）；勿据此断言简历已读完】"
        )
    else:
        header += "\n【完整性：已到文末】"
    return f"{header}\n---\n{win.text}"


def format_find_for_model(hits: List[Dict[str, Any]], *, query: str) -> str:
    if not hits:
        return (
            f"【find】query={query!r} 无命中章节。请改用 mode=catalog 看目录，"
            "或 mode=read 从 offset=0 顺序阅读。coverage=partial；complete=false。"
        )
    parts = [
        f"【find】query={query!r} hits={len(hits)} coverage=partial complete=false",
        "以下为相关章节窗口，禁止据此声称已读完全文；画像级结论须 mode=read 至 complete=true。",
    ]
    for i, h in enumerate(hits, 1):
        parts.append(
            f"--- hit#{i} section_id={h.get('section_id')} title={h.get('title')} "
            f"score={h.get('score'):.2f} offset={h.get('offset')} ---\n"
            f"{h.get('text') or ''}"
        )
    return "\n".join(parts)


def materialize_for_ask(
    file_ids: List[str],
    *,
    inline_budget: int = CHARS_INLINE_COMPLETE,
    query: str = "",
    student_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Ask-path entry: upgrade thin extracts, then pack.

    Does not rely on the model to page or ask the user to re-upload when
    full_text can be recovered server-side within budget.
    """
    svc = get_agent_file_service()
    for fid in file_ids or []:
        if not fid:
            continue
        if student_id is not None or session_id is not None:
            assert_document_access(
                str(fid),
                student_id=student_id,
                session_id=session_id,
                require_mount=True,
            )
        try:
            svc.upgrade_thin_ingest(str(fid))
        except Exception:
            pass
    pack, report = pack_for_prompt(
        list(file_ids or []),
        inline_budget=inline_budget,
        student_id=student_id,
        session_id=session_id,
    )
    # If still incomplete but under budget after upgrade, force-stitch full_text
    if not report.get("all_complete"):
        forced_parts: List[str] = []
        all_ok = True
        for fid in file_ids or []:
            if not fid:
                continue
            full, meta = _full_text(fid)
            if meta.get("_text_source") == "excerpt_fallback" or not full:
                all_ok = False
                continue
            if len(full) > inline_budget:
                all_ok = False
                # Query-aware supplement on top of existing pack
                hits = find_windows(fid, query=query or "教育经历 项目经验 实习", top_k=4)
                if hits:
                    forced_parts.append(format_find_for_model(hits, query=query or "关键段落"))
                continue
            win = read_window(
                fid, offset=0, max_chars=len(full), hard_cap=inline_budget,
            )
            forced_parts.append(format_window_for_model(win))
        if forced_parts and all_ok:
            return "\n\n==========\n\n".join(forced_parts), {
                **report,
                "all_complete": True,
                "materialized": True,
            }
        if forced_parts:
            return (
                pack + "\n\n==========\n\n" + "\n\n==========\n\n".join(forced_parts)
            ), {**report, "materialized_partial": True}
    report["materialized"] = True
    return pack, report


def pack_for_prompt(
    file_ids: List[str],
    *,
    inline_budget: int = CHARS_INLINE_COMPLETE,
    student_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Build prompt material for Ask/Agent turns with attachments.

    - Each file that fits budget → full text + complete=true
    - Oversized → section TOC + first window + has_more (model should find/section/read)
    Never returns a head-slice labeled as if it were the whole resume.
    """
    parts: List[str] = []
    report: Dict[str, Any] = {"files": [], "all_complete": True}
    for fid in file_ids:
        if not fid:
            continue
        if student_id is not None or session_id is not None:
            assert_document_access(
                str(fid),
                student_id=student_id,
                session_id=session_id,
                require_mount=True,
            )
        cat = document_catalog(fid)
        report["files"].append(cat)
        total = int(cat.get("total_chars") or 0)
        if total <= 0:
            report["all_complete"] = False
            parts.append(
                f"【附件：{cat.get('filename')}】状态={cat.get('status')} 无文本产物"
            )
            continue
        # Excerpt-only artifacts must not take the "complete pack" path
        _, meta = _full_text(fid)
        if meta.get("_text_source") == "excerpt_fallback":
            report["all_complete"] = False
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(
                f"【附件警告：{cat.get('filename')}】仅有 excerpt，无 full_text 产物；"
                f"勿当全文。请等待 ingest 完成或重新上传。\n"
                f"{format_window_for_model(win)}"
            )
            continue
        if total <= inline_budget:
            # Bypass page max so DeepSeek-style complete pack is actually whole-doc
            win = read_window(
                fid,
                offset=0,
                max_chars=total or inline_budget,
                hard_cap=inline_budget,
            )
            parts.append(format_window_for_model(win))
        else:
            report["all_complete"] = False
            toc = format_sections_for_catalog(list(cat.get("sections") or []))
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(
                f"【附件目录：{cat.get('filename')}】total_chars={total}\n"
                f"章节：\n{toc}\n"
                f"正文超过单次注入预算，已附首页窗口；"
                f"请用 attachment_read mode=find 或 mode=section 跳读，"
                f"需要完整结论再 mode=read 分页至 complete=true。\n"
                f"{format_window_for_model(win)}"
            )
    body = "\n\n==========\n\n".join(parts)
    return body, report
