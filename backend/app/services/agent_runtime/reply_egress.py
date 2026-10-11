"""
User-reply egress — Claude/Qoder ui_view.

Model may reason over model_view (tools, blackboard keys). The HTTP reply
channel must speak product language only. Isolation is a channel boundary:
prompts for Ask stay product-facing; egress is defense-in-depth.
"""

from __future__ import annotations

import re
from typing import Optional

from .tool_egress import redact_text

_REPLACEMENTS = [
    (re.compile(r"\bportrait_generate\b", re.I), "建立个人画像"),
    (re.compile(r"\breport_generate\b", re.I), "生成职业报告"),
    (re.compile(r"\bmatch_run\b", re.I), "做人岗匹配"),
    (re.compile(r"\bskill_match\b", re.I), "技能匹配"),
    (re.compile(r"\bpipeline_run\b", re.I), "执行分析任务"),
    (re.compile(r"\battachment_read\b", re.I), "阅读附件"),
    (re.compile(r"\bgraph_query\b", re.I), "查询知识图谱"),
    (re.compile(r"\bsemantic_search\b", re.I), "语义检索"),
    (re.compile(r"\bdb_query\b", re.I), "查询业务数据"),
    (re.compile(r"\bllm_reason\b", re.I), "推理分析"),
    (re.compile(r"\bnormalize_entity\b", re.I), "实体归一"),
    (re.compile(r"\blearning_path\b", re.I), "学习路径建议"),
    (re.compile(r"\bskill_recommend\b", re.I), "技能推荐"),
    (re.compile(r"portrait\.current", re.I), "个人职业画像"),
    (re.compile(r"match\.current", re.I), "人岗匹配结果"),
    (re.compile(r"report\.current", re.I), "职业报告"),
    (re.compile(r"plan\.current", re.I), "学习规划"),
    (re.compile(r"student\.profile_ref", re.I), "当前学生档案"),
    (re.compile(r"constraints\.user", re.I), "你已确认的约束"),
    (re.compile(r"orchestration\.meta", re.I), "当前进度"),
    (re.compile(r"attachments\.mounted", re.I), "已上传附件"),
    (re.compile(r"【黑板能力卡】"), "【会话进度】"),
    (re.compile(r"黑板业务态|黑板\s*ACL", re.I), "系统业务状态"),
    (re.compile(r"(?<![一-龥])黑板(?![一-龥])"), "系统状态"),
    (re.compile(r"\bTaskPacket\b", re.I), "任务"),
    (re.compile(r"\bSupervisor\b", re.I), "助手编排"),
    (re.compile(r"\bSubAgents?\b", re.I), "专项助手"),
    (re.compile(r"\bstudent_[a-zA-Z0-9_]+"), "当前用户"),
]


def sanitize_user_reply(text: str, *, max_len: Optional[int] = 8000) -> str:
    """Convert model reply to ui_view: product language + PII redact."""
    out = str(text or "")
    if not out.strip():
        return ""
    # Drop model_view write-back fences / raw JSON dumps from user channel
    out = re.sub(r"```blackboard[\s\S]*?```", "", out, flags=re.I)
    out = re.sub(r"```json[\s\S]*?```", "", out, flags=re.I)
    out = re.sub(r"\{\s*\"[^\"]+\"\s*:\s*\{[\s\S]{0,800}?\}(?:\s*\})+", "", out)
    for pat, repl in _REPLACEMENTS:
        out = pat.sub(repl, out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return redact_text(out.strip(), max_len=max_len)
