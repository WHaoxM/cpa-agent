"""
Qoder-aligned Chat fast path.

Principle (from Qoder Chat vs Agent/Experts + Awareness fork):
- Main conversation must not pay full multi-agent cost every turn.
- Awareness / heavy routing / tool graphs run only when the turn needs them.
- Chitchat and clear keyword intents skip the LLM router.

Claude/Qoder context boundary:
- Conversation STM is per session_id only.
- Student LTM / resume profile must not be smuggled as fake user text.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Frontend used to rewrite user turns as "[用户概况] … | 询问：…"（破坏会话隔离）
_PROFILE_PREFIX_RE = re.compile(
    r"^\[用户概况\][\s\S]*?\|\s*询问：\s*",
    re.UNICODE,
)

# Pure greetings / acknowledgements — may reply without any LLM
_GREETING_RE = re.compile(
    r"^[\s]*(你好|您好|嗨|哈喽|hello|hi|hey|早上好|下午好|晚上好|在吗|在不在)"
    r"[\s!！。.~～啊呀哦嗯]*$",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^[\s]*(谢谢|感谢|thanks|thank you|多谢)[\s!！。.~～啊]*$",
    re.IGNORECASE,
)

# Meta / capability probes — must not inherit prior career LTM
_CAPABILITY_PROBE_RE = re.compile(
    r"^[\s]*("
    r"你能帮我做什么|你能做什么|你会什么|你有什么能力|介绍一下你自己|你是谁|"
    r"可以做什么|能做什么|有哪些能力|帮我做什么|"
    r"what can you (do|help(?: with)?)|who are you|what do you do"
    r")[\s?？!！.。~～]*$",
    re.IGNORECASE,
)

_TASK_KEYWORDS = (
    "报告", "report", "匹配", "match", "岗位", "投递", "画像", "portrait",
    "能力", "维度", "规划", "学习路径", "计划", "roadmap", "简历", "技能",
    "管线", "生成", "分析", "推荐", "课程", "图谱", "查询", "搜索",
)

_TOOLISH_QA = (
    "图谱", "neo4j", "查询", "搜索", "相似", "数据库", "有哪些岗位",
    "技能要求", "知识库",
    # Attachment / resume body intents — must enable attachment_read
    "完整内容", "全文", "原文", "简历内容", "附件内容", "读简历", "开始读",
)

_DOCUMENT_CONTENT_RE = re.compile(
    r"(完整内容|全文|原文|完整文本|简历内容|附件内容|展示简历|读一下简历|开始读简历|把简历.*发|贴出.*简历)",
    re.UNICODE,
)


def visible_user_text(message: str) -> str:
    """Strip synthetic profile prefixes so STM/fast-path see the real utterance."""
    text = (message or "").strip()
    if not text:
        return ""
    cleaned = _PROFILE_PREFIX_RE.sub("", text).strip()
    return cleaned or text


def is_pure_greeting(message: str) -> bool:
    text = visible_user_text(message)
    if not text or len(text) > 24:
        return False
    return bool(_GREETING_RE.match(text) or _THANKS_RE.match(text))


def is_capability_probe(message: str) -> bool:
    """User asking what the assistant can do — treat as meta, not career continuation."""
    text = visible_user_text(message)
    if not text or len(text) > 48:
        return False
    return bool(_CAPABILITY_PROBE_RE.match(text))


def is_chat_fast_path(message: str) -> bool:
    """Short non-task utterances → Chat mode (no router, no tools)."""
    text = visible_user_text(message)
    if not text:
        return False
    if is_pure_greeting(text) or is_capability_probe(text):
        return True
    if len(text) > 48:
        return False
    return not any(k in text for k in _TASK_KEYWORDS)


def qa_needs_tools(message: str) -> bool:
    text = message or ""
    return any(k in text for k in _TOOLISH_QA)


def wants_document_content(message: str) -> bool:
    """User asks to see mounted resume/attachment body — do not stall on '开始读简历'."""
    text = visible_user_text(message)
    if not text:
        return False
    return bool(_DOCUMENT_CONTENT_RE.search(text))


def greeting_reply(message: str) -> str:
    text = visible_user_text(message)
    if _THANKS_RE.match(text):
        return "不客气。需要的话我可以帮你做能力画像、人岗匹配、职业报告或学习规划——直接说你的目标就行。"
    if is_capability_probe(text):
        return (
            "我是职导星图助手，当前是 Ask 模式（轻量问答）。\n\n"
            "我可以帮你：\n"
            "- 解释职业规划概念与路径取舍\n"
            "- 分析技能与岗位匹配思路\n"
            "- 指出常见能力缺口与补齐顺序\n"
            "- 引导下一步（画像 / 匹配 / 报告需切换到 Agent 或 Experts 执行）\n\n"
            "你更想先聊方向、技能，还是具体岗位？"
        )
    return (
        "你好！我是职导星图助手，可以帮你做简历/能力画像、人岗匹配、职业报告和学习路径规划。"
        "想从哪一步开始？"
    )


def keyword_route(message: str) -> Optional[str]:
    """Return subagent only when message explicitly signals a task. Never infer from blackboard."""
    text = message or ""
    if any(k in text for k in ["报告", "report", "职业报告"]):
        return "report"
    if any(k in text for k in ["匹配", "match", "投递"]):
        return "match"
    if any(k in text for k in ["画像", "portrait", "四维"]):
        return "profile"
    if any(k in text for k in ["学习路径", "roadmap"]) or (
        "规划" in text and any(k in text for k in ["学习", "职业", "路径", "计划"])
    ):
        return "planner"
    if any(k in text for k in ["岗位"]) and any(k in text for k in ["匹配", "适合", "推荐"]):
        return "match"
    return None


def should_use_llm_router(message: str, keyword_sub: Optional[str]) -> bool:
    """
    LLM router only for ambiguous, task-like turns.
    Skip for: chat fast path, pure keyword hits, short qa.
    """
    if is_chat_fast_path(message):
        return False
    if keyword_sub in ("profile", "match", "report", "planner"):
        return False
    text = (message or "").strip()
    # short vague questions still go qa without router tax
    if len(text) < 60 and not any(k in text for k in _TASK_KEYWORDS):
        return False
    # ambiguous mid-length with some task smell
    return len(text) >= 24
