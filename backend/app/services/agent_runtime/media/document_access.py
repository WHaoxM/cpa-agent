"""
Document Access Layer — Claude/Qoder Files style.

Architecture contract:
- Artifacts hold the canonical full text (and chunks).
- The model never receives a silent head-slice that pretends to be the whole file.
- complete=true describes coverage of the available extracted text, not OCR fidelity.
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
    "grep_full_text",
    "grep_document_hybrid",
    "pack_for_prompt",
    "materialize_for_ask",
    "assert_document_access",
    "DocumentAccessFacade",
    "format_window_for_model",
    "format_find_for_model",
    "format_catalog_for_model",
    "format_grep_results",
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
    full_artifact = next(
        (a for a in svc.list_artifacts(file_id) if a.get("kind") == "full_text"),
        {},
    )
    full = full_artifact.get("body_text") or ""
    if full:
        meta["_text_source"] = "full_text"
        meta["_source_truncated"] = bool((full_artifact.get("meta") or {}).get("truncated"))
        meta["_layout_incomplete"] = bool(meta.get("layout_incomplete"))
        meta["_repair_pending"] = meta.get("upgrade_status") in (
            "queued", "running", "unavailable", "failed",
        )
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
    full, text_meta = _full_text(file_id)
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
        "upgrade_status": meta.get("upgrade_status"),
        "upgrade_error": meta.get("upgrade_error"),
        "total_chars": len(full),
        "chunk_count": len(chunks),
        "block_count": len(blocks),
        "sections": sections,
        "outline": outline[:16],
        "complete_inline_budget": CHARS_INLINE_COMPLETE,
        "fits_inline": len(full) <= CHARS_INLINE_COMPLETE,
        "source_complete": (
            text_meta.get("_text_source") == "full_text"
            and not text_meta.get("_source_truncated")
            and not text_meta.get("_layout_incomplete")
            and not text_meta.get("_repair_pending")
        ),
    }


def format_catalog_for_model(cat: Dict[str, Any]) -> str:
    sections = cat.get("sections") or []
    toc = format_sections_for_catalog(list(sections))
    return (
        f"文件 {cat.get('filename')} (file_id={cat.get('file_id')})\n"
        f"status={cat.get('status')} upgrade_status={cat.get('upgrade_status')} "
        f"total_chars={cat.get('total_chars')} "
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
    source_incomplete = (
        meta.get("_text_source") == "excerpt_fallback"
        or bool(meta.get("_source_truncated"))
        or bool(meta.get("_layout_incomplete"))
        or bool(meta.get("_repair_pending"))
    )
    if off >= total:
        return DocWindow(
            file_id=file_id,
            filename=name,
            total_chars=total,
            offset=off,
            text="",
            next_offset=None,
            has_more=False,
            complete=False,
            coverage="partial",
        )
    end = min(total, off + cap)
    text = full[off:end]
    has_more = end < total
    complete = not source_incomplete and off == 0 and not has_more
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
    Ask-path entry: schedule a needed repair without blocking, then pack the
    currently available artifact.  The caller can retry after ingest finishes.
    """
    svc = get_agent_file_service()
    upgrade_states: Dict[str, str] = {}
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
            result = svc.schedule_upgrade(str(fid))
            if isinstance(result, dict) and result.get("upgrade_status"):
                upgrade_states[str(fid)] = str(result["upgrade_status"])
        except Exception:
            upgrade_states[str(fid)] = "unavailable"
    pack, report = pack_for_prompt(
        list(file_ids or []),
        inline_budget=inline_budget,
        student_id=student_id,
        session_id=session_id,
    )
    report["upgrade_states"] = upgrade_states
    report["materialized"] = bool(report.get("all_complete"))
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

    - The inline budget is shared by *all* files in this turn, not reset per file.
    - Each file that fits its share → full text + complete=true.
    - Oversized → section TOC + first window + has_more (model should find/section/read).
    Never returns a head-slice labeled as if it were the whole resume.
    """
    parts: List[str] = []
    ids = [str(fid) for fid in file_ids if fid]
    # Leave room in every share for the file identity and completeness metadata.
    # This is deliberately conservative: a body just below 48k may be paged
    # instead of risking a provider context overflow or a silent head-slice.
    separator_chars = len("\n\n==========\n\n") * max(0, len(ids) - 1)
    if ids and int(inline_budget) < separator_chars + 200 * len(ids):
        raise ValueError("inline_budget too small to identify every attachment")
    share = max(200, (int(inline_budget) - separator_chars) // max(1, len(ids)))
    body_share = max(400, share - 700)

    def bounded_partial(part: str) -> str:
        if len(part) <= share:
            return part
        marker = (
            "\n【提示词预算截断：仅当前提示词窗口被裁剪；原始文档仍可用 "
            "attachment_read 按 offset 续读。complete=false】"
        )
        return part[: max(0, share - len(marker))] + marker

    report: Dict[str, Any] = {
        "files": [], "all_complete": True,
        "inline_budget_chars": int(inline_budget),
        "per_file_budget_chars": share,
        "budget_unit": "chars_estimate",
    }
    for fid in ids:
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
        cat["prompt_complete"] = False
        report["files"].append(cat)
        total = int(cat.get("total_chars") or 0)
        if total <= 0:
            report["all_complete"] = False
            parts.append(bounded_partial(
                f"【附件：{cat.get('filename')} file_id={fid}】状态={cat.get('status')} "
                f"重解析状态={cat.get('upgrade_status')}；暂无文本产物，"
                "不能基于此附件下结论；请等待异步解析完成并重试。"
            ))
            continue
        # Excerpt-only artifacts must not take the "complete pack" path
        _, meta = _full_text(fid)
        if meta.get("_text_source") == "excerpt_fallback":
            report["all_complete"] = False
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(bounded_partial(
                f"【附件警告：{cat.get('filename')} file_id={fid}】仅有 excerpt，无 full_text 产物；"
                f"勿当全文。重解析状态={cat.get('upgrade_status')}；"
                f"请等待异步解析完成并重试。\n"
                f"{format_window_for_model(win)}"
            ))
            continue
        if meta.get("_source_truncated"):
            report["all_complete"] = False
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(bounded_partial(
                f"【附件警告：{cat.get('filename')} file_id={fid}】已保存文本在入库时截断，"
                f"不能代表原文件全文；重解析状态={cat.get('upgrade_status')}。"
                f"旧文本仅供参考，等待后台解析后再确认。\n"
                f"{format_window_for_model(win)}"
            ))
            continue
        if meta.get("_layout_incomplete"):
            report["all_complete"] = False
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(bounded_partial(
                f"【附件警告：{cat.get('filename')} file_id={fid}】旧解析产物只保存了部分版面块，"
                f"章节/页码覆盖不完整；重解析状态={cat.get('upgrade_status')}。"
                f"已提取文本可供参考，但不能确认已覆盖原文件。\n"
                f"{format_window_for_model(win)}"
            ))
            continue
        if meta.get("_repair_pending"):
            report["all_complete"] = False
            win = read_window(fid, offset=0, max_chars=CHARS_READ_PAGE)
            parts.append(bounded_partial(
                f"【附件警告：{cat.get('filename')} file_id={fid}】"
                f"原提取结果需要补全，重解析状态={cat.get('upgrade_status')}；"
                "现有文本仅供参考，不能据此断言原文件已完整读取。\n"
                f"{format_window_for_model(win)}"
            ))
            continue
        if total <= body_share:
            # Bypass page max so DeepSeek-style complete pack is actually whole-doc
            win = read_window(
                fid,
                offset=0,
                max_chars=total or body_share,
                hard_cap=body_share,
            )
            rendered = format_window_for_model(win)
            if win.complete and len(rendered) <= share:
                parts.append(rendered)
                cat["prompt_complete"] = True
            else:
                # Even a short body can exceed its share through metadata; the
                # prompt must not advertise completeness after a budget cut.
                report["all_complete"] = False
                partial = read_window(
                    fid, offset=0,
                    max_chars=min(CHARS_READ_PAGE, max(400, body_share // 2)),
                )
                parts.append(bounded_partial(
                    f"【附件预算：{cat.get('filename')} file_id={fid}】当前提示词仅含窗口；"
                    "请用 attachment_read 按 offset 续读。complete=false\n"
                    f"file_id={fid} total_chars={total} offset=0 "
                    f"returned={len(partial.text)} coverage=partial\n---\n{partial.text}"
                ))
        else:
            report["all_complete"] = False
            toc = format_sections_for_catalog(list(cat.get("sections") or []))
            toc_budget = max(100, body_share // 4)
            if len(toc) > toc_budget:
                toc = toc[:toc_budget] + "\n【目录省略：用 attachment_read mode=catalog 获取完整目录】"
            win = read_window(
                fid, offset=0,
                max_chars=min(CHARS_READ_PAGE, max(400, body_share // 2)),
            )
            part = (
                f"【附件目录：{cat.get('filename')} file_id={fid}】total_chars={total}\n"
                f"章节：\n{toc}\n"
                f"正文超过本轮按附件分配的注入预算（每件约 {share} 字符），已附首页窗口；"
                f"请用 attachment_read mode=find 或 mode=section 跳读，"
                f"需要完整结论再 mode=read 分页至 complete=true。\n"
                f"{format_window_for_model(win)}"
            )
            parts.append(bounded_partial(part))
    body = "\n\n==========\n\n".join(parts)
    report["pack_chars"] = len(body)
    return body, report


# ---------------------------------------------------------------------------
# grep 搜索功能 — 混合匹配：关键词快速定位 + LLM语义归一化
# ---------------------------------------------------------------------------

def grep_full_text(
    full_text: str,
    pattern: str,
    *,
    use_regex: bool = False,
    case_sensitive: bool = False,
    context_lines: int = 2,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    全文grep搜索，返回匹配位置和上下文

    Args:
        full_text: 完整文本
        pattern: 搜索模式（关键词或正则表达式）
        use_regex: 是否使用正则表达式
        case_sensitive: 是否大小写敏感
        context_lines: 上下文行数（前后各N行）
        max_results: 最大结果数

    Returns:
        匹配结果列表 [{
            "line_number": int,
            "line": str,
            "context": str,
            "start_line": int,
            "end_line": int,
        }, ...]
    """
    import re as _re

    if not full_text or not pattern:
        return []

    lines = full_text.split('\n')
    results: List[Dict[str, Any]] = []

    # 构建正则表达式
    if use_regex:
        try:
            regex = _re.compile(pattern, 0 if case_sensitive else _re.IGNORECASE)
        except _re.error:
            # 正则语法错误，回退到简单文本匹配
            regex = _re.compile(_re.escape(pattern), 0 if case_sensitive else _re.IGNORECASE)
    else:
        # 简单文本匹配（转义特殊字符）
        flags = 0 if case_sensitive else _re.IGNORECASE
        regex = _re.compile(_re.escape(pattern), flags)

    for i, line in enumerate(lines):
        if regex.search(line):
            # 获取上下文
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            context = '\n'.join(lines[start:end])

            results.append({
                "line_number": i + 1,  # 1-indexed
                "line": line,
                "context": context,
                "start_line": start + 1,
                "end_line": end,
            })

            if len(results) >= max_results:
                break

    return results


def grep_document_hybrid(
    file_id: str,
    query: str,
    *,
    category: str = "education",
    use_regex: bool = False,
    case_sensitive: bool = False,
    context_lines: int = 2,
    max_results: int = 10,
    llm_client: Any = None,
) -> List[Dict[str, Any]]:
    """
    混合grep搜索：关键词匹配 + LLM语义匹配

    Args:
        file_id: 文件ID
        query: 用户查询（如"教育背景"）
        category: 类别（education/experience/projects/skills）
        use_regex: 是否使用正则表达式
        case_sensitive: 是否大小写敏感
        context_lines: 上下文行数
        max_results: 最大结果数
        llm_client: LLM客户端（可选，用于语义匹配）

    Returns:
        混合匹配结果列表
    """
    from config.loader import get_grep_config

    # 1. 获取文件完整文本
    full, meta = _full_text(file_id)
    if not full:
        return []

    # 2. 加载配置
    grep_config = get_grep_config()
    keywords = grep_config.get('resume_keywords', {}).get(category, [])

    # 3. 关键词快速匹配
    keyword_results: List[Dict[str, Any]] = []
    if keywords:
        pattern = '|'.join(keywords)
        keyword_results = grep_full_text(
            full, pattern,
            use_regex=True,  # 关键词组合使用正则
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            max_results=max_results,
        )

    # 4. 如果没有LLM客户端，只返回关键词结果
    if not llm_client:
        return keyword_results

    # 5. LLM语义匹配
    try:
        from app.utils.semantic_matcher import SemanticMatcher

        matcher = SemanticMatcher(llm_client)
        candidate_texts = [r.get('line', '') for r in keyword_results]

        semantic_results = matcher.find_semantically_similar(
            query=query,
            candidate_texts=candidate_texts,
            full_text=full,
            category=category,
        )

        # 6. 合并结果（去重）
        return _merge_grep_results(keyword_results, semantic_results)

    except Exception:
        # 语义匹配失败，只返回关键词结果
        return keyword_results


def _merge_grep_results(
    keyword_results: List[Dict[str, Any]],
    semantic_results: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """合并关键词和语义匹配结果"""
    # 将语义匹配结果转换为统一格式
    merged = list(keyword_results)

    for segment in semantic_results.get('matched_segments', []):
        text = segment.get('text', '')
        if not text:
            continue

        # 检查是否已存在（简单文本包含检查）
        exists = False
        for r in merged:
            if text in r.get('line', '') or r.get('line', '') in text:
                exists = True
                break

        if not exists:
            merged.append({
                "line_number": 0,  # 语义匹配的行号不精确
                "line": text[:500],  # 限制长度
                "context": text[:500],
                "start_line": 0,
                "end_line": 0,
                "source": "semantic_match",
                "relevance_score": segment.get('relevance_score', 0.0),
                "semantic_reason": segment.get('semantic_reason', ''),
            })

    return merged


def format_grep_results(
    hits: List[Dict[str, Any]],
    *,
    query: str,
    max_chars_per_hit: int = 200,
) -> str:
    """
    格式化grep搜索结果供模型使用

    Args:
        hits: grep搜索结果
        query: 原始查询
        max_chars_per_hit: 每个结果最大字符数

    Returns:
        格式化后的字符串
    """
    if not hits:
        return (
            f"【grep】query={query!r} 无匹配结果。"
            "请尝试其他关键词或使用 mode=catalog 查看目录。"
        )

    parts = [
        f"【grep】query={query!r} hits={len(hits)} coverage=partial complete=false",
        "以下为匹配结果，禁止据此声称已读完全文；画像级结论须 mode=read 至 complete=true。",
    ]

    for i, h in enumerate(hits, 1):
        source = h.get('source', 'keyword_match')
        score_info = ""
        if source == 'semantic_match':
            score = h.get('relevance_score', 0)
            reason = h.get('semantic_reason', '')
            score_info = f" relevance={score:.2f} reason={reason}"

        line_num = h.get('line_number', 0)
        line_info = f"L{line_num}" if line_num > 0 else "?"

        text = h.get('line', '')[:max_chars_per_hit]
        if len(h.get('line', '')) > max_chars_per_hit:
            text += "..."

        parts.append(
            f"--- hit#{i} [{source}] {line_info}{score_info} ---\n{text}"
        )

    return "\n".join(parts)
