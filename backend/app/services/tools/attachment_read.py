"""Gated tool: Document Access for mounted attachments (Claude Files style).

Modes:
  catalog — section TOC with offsets
  find    — query-aware jump (coverage=partial, never complete)
  section — read one section by id/title (never complete)
  read    — sequential offset pages until complete=true (exhaustive fallback)
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..agent_runtime.media.types import CHARS_READ_PAGE, CHARS_READ_PAGE_MAX


class AttachmentReadInput(BaseModel):
    file_id: str = Field(..., description="会话已挂载的 file_id")
    mode: str = Field(
        "read",
        description=(
            "catalog=章节目录；find=按 query 跳读相关段；"
            "section=按 section_id/title 读一章；read=按 offset 顺序分页"
        ),
    )
    offset: int = Field(0, description="read 模式：从第几个字符开始（上一页的 next_offset）")
    max_chars: int = Field(
        CHARS_READ_PAGE,
        description=f"本窗口最大字符数（上限 {CHARS_READ_PAGE_MAX}）",
    )
    query: str = Field("", description="find 模式：用户问题或关键词")
    section_id: str = Field("", description="section 模式：目录中的 id，如 s2")
    title: str = Field("", description="section 模式：章节标题模糊匹配（可选）")
    top_k: int = Field(3, description="find 模式：返回最多几段相关窗口")


def create_attachment_read_tool(
    student_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> StructuredTool:
    def _run(
        file_id: str,
        mode: str = "read",
        offset: int = 0,
        max_chars: int = CHARS_READ_PAGE,
        query: str = "",
        section_id: str = "",
        title: str = "",
        top_k: int = 3,
    ) -> str:
        if not file_id:
            return "[attachment_read] 缺少 file_id"
        from ..agent_runtime.media.document_access import (
            document_catalog,
            find_windows,
            format_catalog_for_model,
            format_find_for_model,
            format_window_for_model,
            read_section,
            read_window,
        )
        from ..agent_runtime.media.file_store import get_agent_file_service
        from ..agent_runtime.media.session_resources import list_resources
        from ..agent_runtime.tool_egress import redact_text

        if session_id:
            if not student_id:
                return "[attachment_read] 缺少会话 student_id"
            mounted = {
                r.get("file_id")
                for r in list_resources(session_id, student_id=student_id)
            }
            if file_id not in mounted:
                return "[attachment_read] 文件未挂载到当前会话，拒绝读取"

        svc = get_agent_file_service()
        meta = svc.get(file_id)
        if not meta:
            return "[attachment_read] 文件不存在或未就绪"
        if meta.get("status") != "ready":
            return f"[attachment_read] 文件状态={meta.get('status')}"
        if not student_id:
            return "[attachment_read] 缺少会话 student_id"
        if meta.get("student_id") and meta["student_id"] != student_id:
            return "[attachment_read] 无权读取该文件"

        m = (mode or "read").strip().lower()
        if m == "catalog":
            return format_catalog_for_model(document_catalog(file_id))

        if m == "find":
            q = (query or "").strip()
            if not q:
                return "[attachment_read] find 模式需要 query=问题或关键词"
            hits = find_windows(
                file_id,
                query=q,
                top_k=int(top_k or 3),
                max_chars=int(max_chars or 2400),
            )
            for h in hits:
                h["text"] = redact_text(h.get("text") or "", max_len=CHARS_READ_PAGE_MAX)
            return format_find_for_model(hits, query=q)

        if m == "section":
            from dataclasses import replace

            win = read_section(
                file_id,
                section_id=section_id or "",
                title=title or "",
                max_chars=int(max_chars or CHARS_READ_PAGE),
            )
            if not (win.text or "").strip() and not win.section_id:
                return (
                    "[attachment_read] 未找到章节；请先 mode=catalog 查看 section_id，"
                    "或改用 mode=find / mode=read。"
                )
            safe_text = redact_text(win.text or "", max_len=CHARS_READ_PAGE_MAX)
            return format_window_for_model(replace(win, text=safe_text))

        from dataclasses import replace

        win = read_window(
            file_id,
            offset=int(offset or 0),
            max_chars=int(max_chars or CHARS_READ_PAGE),
        )
        safe_text = redact_text(win.text or "", max_len=CHARS_READ_PAGE_MAX)
        return format_window_for_model(replace(win, text=safe_text))

    return StructuredTool.from_function(
        func=_run,
        name="attachment_read",
        description=(
            "读取已挂载附件（Document Access）。"
            "mode=catalog 看章节目录；mode=find(query) 按问题跳读相关段；"
            "mode=section(section_id|title) 读一章；mode=read(offset) 顺序分页。"
            "find/section 均为 coverage=partial 且 complete=false；"
            "未 complete=true 前禁止把窗口当成全文下结论。仅使用挂载卡上的 file_id。"
        ),
        args_schema=AttachmentReadInput,
    )
