"""Small deterministic guards for user resource access in durable Agent runs.

This module does not grant access. It only detects an explicit user prohibition
and checks whether a step that promised a resource read has a server-side
receipt. The actual read remains owner-bound in the existing resource tools.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_NO_READ_PATTERNS = (
    re.compile(
        r"(?:不要|別|别|不必|无需|無需|无须|不可以|不可|禁止|不想|不)\s*"
        r"(?:再\s*)?(?:读取|讀取|读|讀|查阅|查閱|查看|检索|檢索|访问|訪問|调取|調取|使用)\s*"
        r"(?:任何|我的|本人|个人|個人|用户的|使用者的|绑定的|綁定的|当前会话中的|本轮的|相关的)?\s*"
        r"(?:个人)?(?:资料|資料|材料|信息|資訊|档案|檔案|画像|簡歷|简历|附件|文件|历史记录|歷史記錄|探索资料|探索資料|课程资料|課程資料)?"
    ),
    re.compile(
        r"(?:个人|個人|我的|本人|绑定的|綁定的|相关的)?\s*"
        r"(?:资料|資料|材料|信息|資訊|档案|檔案|画像|简历|簡歷|附件|文件|历史记录|歷史記錄)\s*"
        r"(?:不要|別|别|禁止|不必|无需|無需|不得|不可以)\s*(?:读取|讀取|查看|查阅|查閱|访问|訪問|检索|檢索|使用)"
    ),
    re.compile(
        r"\b(?:do\s+not|don't|dont|never|without|no)\s+"
        r"(?:read|access|retrieve|look\s+up|review|use)\s+"
        r"(?:(?:any|my|personal|bound|attached|profile|account)\s+){0,3}"
        r"(?:materials?|documents?|records?|profile|resume|attachments?|files?|history|personal\s+data)\b",
        re.IGNORECASE,
    ),
)

_PERSONALIZED_REQUEST = re.compile(
    r"(?:结合我的|根据我的|基于我的|按我的|适合我的|针对我的|为我(?:个人)?(?:推荐|规划|判断)|"
    r"我的(?:简历|经历|背景|画像|职业方向|学习情况|求职情况)|"
    r"based\s+on\s+my|according\s+to\s+my|for\s+my\s+background|personalized\s+for\s+me)",
    re.IGNORECASE,
)

_READ_STEP_GOAL = re.compile(
    r"(?:读取|讀取|查阅|查閱|查看|检索|檢索|调取|調取|打开|讀取)"
    r".{0,36}(?:资料|資料|材料|信息|資訊|档案|檔案|画像|简历|簡歷|附件|文件|记录|記錄|探索|课程|課程)|"
    r"(?:资料|資料|材料|信息|資訊|档案|檔案|画像|简历|簡歷|附件|文件|记录|記錄|探索|课程|課程)"
    r".{0,36}(?:读取|讀取|查阅|查閱|查看|检索|檢索|调取|調取|打开)",
    re.IGNORECASE,
)

_VERIFIED_READ_TOOLS = frozenset({
    "career_data_read",
    "attachment_read",
    "school_context_source_read",
})

_NO_READ_EVIDENCE_SECTION = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:已核验证据|已核验来源|来源核验|证据核验)\s*[：:]\s*$"
)
_NO_READ_NEXT_SECTION = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:缺口说明|未核验说明|来源说明|总结|结论|下一步)"
    r"[^\n]{0,32}[：:][^\n]*$"
)

# Skills are reviewed analysis methods; owner-bound facts are a separate data
# channel. Only explicitly approved method-only Skills may be loaded during a
# turn that forbids personal-resource reads. `school-context` is excluded
# because the runtime release currently binds it to the owner's school profile.
_NO_READ_METHOD_SKILL_IDS = frozenset({"education-screening-context"})
_NO_READ_SAFE_GOAL = (
    "只依据本轮用户明确提供的信息和一般知识回答；"
    "不得读取绑定资料、用户资料、绑定材料、历史记录或附件。"
)


def no_read_safe_skill_ids(capability: Any) -> tuple[str, ...]:
    """Return only approved method Skills, never Skills bound to owner data."""
    values = getattr(capability, "skill_ids", ()) if capability is not None else ()
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(
        skill_id for skill_id in values
        if isinstance(skill_id, str) and skill_id in _NO_READ_METHOD_SKILL_IDS
    )


def no_read_safe_goal() -> str:
    """Return the immutable server-authored goal used by no-read plans."""
    return _NO_READ_SAFE_GOAL


def validate_no_read_plan(capability: Any, steps: Any) -> bool:
    """Validate the one-step safe plan against server-owned role/Skill bindings."""
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], Mapping):
        return False
    expected_role = getattr(capability, "role", "qa") if capability is not None else "qa"
    expected_skills = list(no_read_safe_skill_ids(capability))
    step = steps[0]
    return (
        step.get("role") == expected_role
        and step.get("goal") == _NO_READ_SAFE_GOAL
        and step.get("skill_ids") == expected_skills
    )


def explicitly_forbids_personal_reads(
    message: Any,
    constraints: Iterable[Any] = (),
) -> bool:
    """Return true only for a clear, user-authored no-read instruction."""
    values = [message]
    values.extend(constraints if isinstance(constraints, (list, tuple, set, frozenset)) else ())
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split())
        if any(pattern.search(normalized) for pattern in _NO_READ_PATTERNS):
            return True
    return False


def needs_personal_context_clarification(message: Any) -> bool:
    """Identify requests that cannot be personalized without the blocked data."""
    return isinstance(message, str) and bool(_PERSONALIZED_REQUEST.search(message))


def no_read_clarification(message: Any, constraints: Iterable[Any] = ()) -> str | None:
    if (explicitly_forbids_personal_reads(message, constraints)
            and needs_personal_context_clarification(message)):
        return (
            "你要求本轮不读取个人资料，但问题需要结合你的个人情况才能回答。"
            "请直接在对话中补充希望我考虑的信息；我不会访问绑定资料。"
        )
    return None


def step_requires_resource_read(goal: Any) -> bool:
    return isinstance(goal, str) and bool(_READ_STEP_GOAL.search(goal))


def has_verified_read_tool_result(output: Any) -> bool:
    """Accept only actual UI-channel receipts emitted after a gated tool ran."""
    if not isinstance(output, Mapping):
        return False
    calls = output.get("tool_calls")
    if not isinstance(calls, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("channel") == "ui"
        and item.get("tool_name") in _VERIFIED_READ_TOOLS
        and str(item.get("status") or "").lower() in {"done", "completed"}
        for item in calls
    )


def sanitize_no_read_reply(reply: Any) -> str:
    """Remove model-authored evidence claims when no evidence was read.

    The no-read Agent path has no external read tools. A model-written
    "verified evidence" section would therefore be unproven provenance, even
    when the underlying general explanation is useful.
    """
    text = str(reply or "").strip()[:8000]
    heading = _NO_READ_EVIDENCE_SECTION.search(text)
    if heading:
        next_heading = _NO_READ_NEXT_SECTION.search(text, heading.end())
        if next_heading:
            text = (text[:heading.start()].rstrip() + "\n\n"
                    + text[next_heading.start():].lstrip())
        else:
            text = text[:heading.start()].rstrip()
    note = (
        "来源说明：本轮未联网或读取外部资料；未由用户原文直接提供的内容"
        "按一般知识处理，未外部核验。"
    )
    if note not in text:
        text = f"{text.rstrip()}\n\n{note}" if text else note
    return text[:8000]
