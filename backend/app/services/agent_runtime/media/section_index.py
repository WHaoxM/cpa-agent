"""
Section index for Document Access — navigation TOC, not profile extraction.

Priority:
1) ingest block headings (short lines / title-like blocks)
2) line-level resume/document section titles (教育/项目/实习/技能 …)
3) even anchors as last-resort TOC

Offsets are char indices into full_text so find/section/read can jump.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# Navigation headings only — not field scrapers for school/GPA/etc.
_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"(?:教育|学历)(?:背景|经历)?|"
    r"(?:工作|实习|项目|科研|竞赛|获奖|荣誉|证书|技能|专业|综合)(?:经历|经验|背景|能力|技能)?|"
    r"自我评价|个人简介|基本信息|联系方式|校园经历|社团|志愿|"
    r"Education|Experience|Projects?|Internships?|Skills?|Awards?|Summary|"
    r"Work Experience|Research|Publications?"
    r")\s*[:：]?\s*$",
    re.IGNORECASE,
)

_MAX_SECTIONS = 24
_ANCHOR_EVERY = 4000


def build_section_index(
    full_text: str,
    *,
    blocks: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return [{id, title, offset, end_offset}] covering full_text."""
    text = full_text or ""
    if not text.strip():
        return []

    from_blocks = _from_blocks(text, blocks or [])
    if len(from_blocks) >= 2:
        return _finalize(text, from_blocks)

    from_lines = _from_line_titles(text)
    if len(from_lines) >= 2:
        return _finalize(text, from_lines)

    # Single detected heading still better than pure anchors if present
    if from_lines:
        return _finalize(text, from_lines)

    return _finalize(text, _even_anchors(text))


def format_sections_for_catalog(sections: List[Dict[str, Any]]) -> str:
    if not sections:
        return "- （无章节索引；请用 mode=read 从 offset=0 分页）"
    lines = []
    for s in sections[:_MAX_SECTIONS]:
        lines.append(
            f"- [{s['id']}] {s['title']}  offset={s['offset']}–{s['end_offset']}"
        )
    return "\n".join(lines)


def find_section(
    sections: List[Dict[str, Any]],
    *,
    section_id: str = "",
    title: str = "",
) -> Optional[Dict[str, Any]]:
    sid = (section_id or "").strip().lower()
    tit = (title or "").strip().lower()
    if sid:
        for s in sections:
            if str(s.get("id", "")).lower() == sid:
                return s
    if tit:
        for s in sections:
            st = str(s.get("title") or "").lower()
            if tit == st or tit in st or st in tit:
                return s
    return None


def score_query_against_sections(
    query: str,
    sections: List[Dict[str, Any]],
    full_text: str,
    *,
    top_k: int = 3,
    window: int = 2400,
) -> List[Dict[str, Any]]:
    """
    Lightweight lexical score over section titles + nearby body.
    Returns hit dicts with offset/text — never marks document complete.
    """
    q = (query or "").strip()
    if not q or not full_text:
        return []
    tokens = _query_tokens(q)
    if not tokens:
        tokens = [q.lower()]

    scored: List[Dict[str, Any]] = []
    for s in sections:
        title = str(s.get("title") or "")
        off = int(s.get("offset") or 0)
        end = int(s.get("end_offset") or off)
        body = full_text[off:min(end, off + window)]
        title_l = title.lower()
        body_l = body.lower()
        score = 0.0
        for t in tokens:
            if t in title_l:
                score += 3.0
            if t in body_l:
                score += 1.0 + min(body_l.count(t), 5) * 0.15
        # Chinese bigram overlap when tokens are short
        if score <= 0 and len(q) >= 2:
            for i in range(len(q) - 1):
                bg = q[i : i + 2]
                if bg in title:
                    score += 2.0
                elif bg in body:
                    score += 0.5
        if score > 0:
            scored.append({
                "section": s,
                "score": score,
                "offset": off,
                "text": body,
            })

    scored.sort(key=lambda x: (-x["score"], x["offset"]))
    return scored[: max(1, min(int(top_k or 3), 5))]


def _from_blocks(text: str, blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for b in blocks:
        raw = (b.get("body_text") or "").strip()
        if not raw:
            continue
        first = raw.split("\n", 1)[0].strip()
        title = first[:48]
        if len(first) > 60 or len(first) < 2:
            continue
        # Prefer title-like / short heading blocks
        bt = str((b.get("meta") or {}).get("block_type") or "").lower()
        if bt not in ("title", "heading", "header", "") and len(first) > 40:
            continue
        if bt in ("title", "heading", "header") or _SECTION_TITLE_RE.match(first) or len(first) <= 24:
            idx = text.find(first)
            if idx < 0:
                idx = text.find(raw[: min(40, len(raw))])
            if idx < 0:
                continue
            hits.append({"title": title, "offset": idx})
    return _dedupe_offsets(hits)


def _from_line_titles(text: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        bare = line.strip()
        if bare and _SECTION_TITLE_RE.match(bare):
            rel = line.find(bare)
            hits.append({"title": bare[:48], "offset": pos + (rel if rel >= 0 else 0)})
        pos += len(line)
    return _dedupe_offsets(hits)


def _even_anchors(text: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = [{"title": "文档开头", "offset": 0}]
    step = _ANCHOR_EVERY
    for off in range(step, len(text), step):
        hits.append({"title": f"锚点@{off}", "offset": off})
    return hits


def _dedupe_offsets(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for h in sorted(hits, key=lambda x: int(x["offset"])):
        off = int(h["offset"])
        if off in seen:
            continue
        # Collapse near-duplicates within 40 chars
        if out and abs(off - int(out[-1]["offset"])) < 40:
            continue
        seen.add(off)
        out.append(h)
    return out


def _finalize(text: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not hits:
        hits = [{"title": "文档开头", "offset": 0}]
    hits = sorted(hits, key=lambda x: int(x["offset"]))
    if hits[0]["offset"] > 0:
        hits = [{"title": "文档开头", "offset": 0}] + hits
    sections: List[Dict[str, Any]] = []
    for i, h in enumerate(hits[:_MAX_SECTIONS]):
        start = int(h["offset"])
        end = int(hits[i + 1]["offset"]) if i + 1 < len(hits) else len(text)
        if end <= start:
            end = min(len(text), start + 1)
        sections.append({
            "id": f"s{i}",
            "title": str(h.get("title") or f"段{i}"),
            "offset": start,
            "end_offset": end,
        })
    return sections


def _query_tokens(q: str) -> List[str]:
    # Split on whitespace / punctuation; keep CJK runs as soft tokens
    parts = re.split(r"[\s,，。；;、/\-_|]+", q)
    out: List[str] = []
    for p in parts:
        p = p.strip().lower()
        if len(p) >= 2:
            out.append(p)
    return out[:24]
