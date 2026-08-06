"""
Thinking egress — Claude / Qoder product channel.

Architecture contract (two channels, never mixed):

1. **Operator playbook** (internal): instructions TO the model / runtime
   ("先确认…再决定…", "轻量模式下可…", "正式回复应…"). Logs / prompts only.
2. **UI thinking** (external): first-person deliberation ABOUT this user turn
   ("我看到用户挂了简历…", "材料里能确认的是…", "本轮我先澄清意向…").

Sanitize is the last insurance on channel (2). It must drop playbook voice,
not just rename snake_case keys. Patching individual strings without this
boundary will keep leaking system instructions into the thought panel.
"""

from __future__ import annotations

import re
from typing import List

# Raw keys / tools that must never appear in user-visible thinking
_KEY_RE = re.compile(
    r"\b("
    r"portrait\.current|match\.current|report\.current|plan\.current|"
    r"student\.profile_ref|constraints\.user|orchestration\.meta|attachments\.mounted|"
    r"portrait_generate|pipeline_run|skill_match|report_generate|match_run|db_query|"
    r"attachment_read|skill_extract|graph_query|semantic_search|llm_reason|"
    r"normalize_entity|learning_path|skill_recommend"
    r")\b",
    re.IGNORECASE,
)

# Operator / prompt voice — instructions TO the model, not deliberation
_PLAYBOOK_RE = re.compile(
    r"("
    r"先确认.{0,16}再决定|再决定是|再决定给|"
    r"应紧扣附件|应引导上传|应介绍可解释|应先定位|"
    r"不得假装|硬性禁止|禁止出现在|"
    r"正式回复应|收束到一个可执行|"
    r"可选答法\s*[ABC]|组织成「我能做什么|"
    r"轻量模式下可|完整管线仍需|本轮不得假装|"
    r"要求：至少\s*\d|不要写最终成稿|不要 emoji|"
    r"<<<THINKING>>>|<<<ANSWER>>>"
    r")",
    re.IGNORECASE,
)

_REPLACEMENTS = [
    (re.compile(r"portrait\.current", re.I), "个人职业画像"),
    (re.compile(r"match\.current", re.I), "人岗匹配结果"),
    (re.compile(r"report\.current", re.I), "职业报告"),
    (re.compile(r"plan\.current", re.I), "学习规划"),
    (re.compile(r"student\.profile_ref", re.I), "当前学生档案引用"),
    (re.compile(r"constraints\.user", re.I), "用户约束"),
    (re.compile(r"attachments\.mounted", re.I), "已挂载附件"),
    (re.compile(r"orchestration\.meta", re.I), "编排状态"),
    (re.compile(r"portrait_generate", re.I), "画像生成"),
    (re.compile(r"match_run", re.I), "人岗匹配"),
    (re.compile(r"pipeline_run", re.I), "分析任务"),
    (re.compile(r"skill_match", re.I), "技能匹配"),
    (re.compile(r"report_generate", re.I), "报告生成"),
    (re.compile(r"attachment_read", re.I), "附件阅读"),
    (re.compile(r"db_query", re.I), "数据查询"),
    (re.compile(r"graph_query", re.I), "知识图谱查询"),
    (re.compile(r"semantic_search", re.I), "语义检索"),
    (re.compile(r"CPA\s*架构|底层契约|黑板能力卡|黑板业务态", re.I), "系统能力边界"),
    (re.compile(r"student_[a-zA-Z0-9_]+"), "当前用户"),
    (re.compile(r"【黑板能力卡】"), "【会话进度】"),
]


def looks_like_playbook(text: str) -> bool:
    """True if line is operator instruction voice, not user-facing deliberation."""
    s = (text or "").strip()
    if not s:
        return False
    if _PLAYBOOK_RE.search(s):
        return True
    # Imperative checklist without first-person "我"
    if "我" not in s and re.search(r"^(先|再|应|须|必须|禁止)", s):
        return True
    return False


def sanitize_thinking_line(text: str) -> str:
    out = str(text or "")
    for pat, repl in _REPLACEMENTS:
        out = pat.sub(repl, out)
    out = re.sub(r"\b[a-z_]+\.[a-z_]+\b", "相关状态", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def sanitize_thinking_steps(steps: List[str]) -> List[str]:
    cleaned: List[str] = []
    for s in steps or []:
        if looks_like_playbook(s):
            continue
        line = sanitize_thinking_line(s)
        if not line or looks_like_playbook(line):
            continue
        if _KEY_RE.search(line) and len(line) < 40:
            continue
        cleaned.append(line)
    return cleaned[:12]
