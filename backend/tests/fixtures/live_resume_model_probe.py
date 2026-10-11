"""Opt-in live model probe of page-labelled MinerU text; never prints resume text.

Run from the repo root with the mkg interpreter and a content_list.json path.
Loads the repo .env only to obtain the configured LLM provider credentials.
This probes Prompt Registry + LLMClient, not the full HTTP Ask runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("content_list", type=Path, help="MinerU *_content_list.json")
    args = parser.parse_args()

    load_dotenv(BACKEND.parent / ".env")
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.prompt_selector import PromptSelectionKey, get_prompt_selector
    from app.utils.llm_client import get_llm_client

    rows = json.loads(args.content_list.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("content_list must be a JSON array")
    pages: dict[int, list[str]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("type") == "text":
            page = row.get("page_idx")
            if isinstance(page, int) and page >= 0:
                pages.setdefault(page, []).append(str(row.get("text") or ""))
    if not pages:
        raise ValueError("content_list contains no page-labelled text")
    body = "\n".join(
        f"【第{page + 1}页】\n" + "\n".join(parts)
        for page, parts in sorted(pages.items())
    )
    prompt = get_prompt_selector().select(
        PromptSelectionKey(
            engine="supervisor",
            interaction_mode=InteractionMode.ASK,
            content_kind="attachment",
        )
    ).system
    question = (
        "只依据这份逐页简历，严格返回 JSON 对象："
        "education_page 为教育经历所在页数字或 null；"
        "education_level 为本科/硕士/未知；"
        "salary_stated 为是否写了目标薪资数字的布尔值；"
        "career_tracks 为最多两个不含人名、学校名、公司名的岗位方向；"
        "evidence_pages 为支持方向的页码列表。"
        "不得输出姓名、学校、公司、联系方式或原文段落。"
    )
    result = get_llm_client().chat_with_metadata(
        [
            {"role": "system", "content": prompt + "\n仅基于文档证据，未知不推断。严格输出 JSON。"},
            {"role": "user", "content": body + "\n【问题】" + question},
        ],
        temperature=0.1,
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    answer = json.loads(result.text)
    if not isinstance(answer, dict):
        raise ValueError("model response was not a JSON object")
    tracks = answer.get("career_tracks")
    tracks = tracks if isinstance(tracks, list) else []
    track_text = " ".join(str(item) for item in tracks)
    education_page = answer.get("education_page")
    education_page = education_page if isinstance(education_page, int) else None
    education_level = answer.get("education_level")
    education_level = education_level if isinstance(education_level, str) and education_level in {"本科", "硕士", "未知"} else "unknown_format"
    salary_stated = answer.get("salary_stated")
    salary_stated = salary_stated if isinstance(salary_stated, bool) else None
    evidence_pages = answer.get("evidence_pages")
    evidence_pages = [p for p in evidence_pages if isinstance(p, int)] if isinstance(evidence_pages, list) else []
    print(json.dumps({
        "provider": result.provider,
        "model": result.model,
        "page_count": len(pages),
        "page_chars": {str(page + 1): sum(map(len, parts)) for page, parts in pages.items()},
        "context_fits": result.context_status.fits,
        "usage": result.usage,
        "education_page": education_page,
        "education_level": education_level,
        "salary_stated": salary_stated,
        "career_tracks_count": len(tracks),
        "mentions_ai_application": "AI应用" in track_text or "人工智能应用" in track_text,
        "mentions_agent": "Agent" in track_text or "智能体" in track_text,
        "evidence_pages": evidence_pages,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
