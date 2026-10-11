"""
Context Compaction — 长对话自动摘要压缩。

当 history token 估算超过阈值时，用 LLM 摘要旧消息，保留最近 K 轮。
对标 OpenCode: compactIfNeeded() + incremental summary.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...utils.logger import get_logger

logger = get_logger("cpa-agent.compaction")

# 估算 token 数：中文约 1.5 char/token，英文约 4 char/token
def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(text) - cn
    return int(cn * 1.5 + en / 4)


def _estimate_history_tokens(history: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in history:
        content = str(msg.get("content") or "")
        total += _estimate_tokens(content)
        # 工具调用/结果也占 token
        for key in ("tool_calls", "tool_results"):
            if msg.get(key):
                total += _estimate_tokens(str(msg[key]))
    return total


# 压缩触发阈值（token）
COMPACTION_THRESHOLD = 12000
# 保留最近 N 轮不压缩
KEEP_RECENT = 6
# 压缩后最大 token
COMPACTED_MAX = 4000


SUMMARY_TEMPLATE = """请将以下对话历史压缩为简洁摘要，保留关键信息：

## 要求
- 保留用户的核心问题和需求
- 保留已执行的工具调用和关键结果
- 保留已达成的结论和未完成的任务
- 用第三人称客观描述，不要模拟对话
- 控制在 {max_chars} 字以内

## 对话历史
{history_text}

## 输出格式
直接输出摘要文本，不要加标题或标记。"""


def compact_history(
    history: List[Dict[str, Any]],
    *,
    threshold: int = COMPACTION_THRESHOLD,
    keep_recent: int = KEEP_RECENT,
) -> Dict[str, Any]:
    """
    检查是否需要压缩，如需要则返回压缩结果。

    Returns:
        {
            "compacted": bool,
            "summary": str,          # 压缩后的摘要（compacted=True 时有值）
            "recent": list,          # 保留的最近消息
            "original_tokens": int,
            "compacted_tokens": int,
        }
    """
    if not history:
        return {"compacted": False, "summary": "", "recent": [], "original_tokens": 0, "compacted_tokens": 0}

    total_tokens = _estimate_history_tokens(history)
    if total_tokens < threshold:
        return {
            "compacted": False,
            "summary": "",
            "recent": history,
            "original_tokens": total_tokens,
            "compacted_tokens": total_tokens,
        }

    # 分割：旧消息（要压缩）+ 最近消息（保留）
    split_at = max(1, len(history) - keep_recent)
    old_messages = history[:split_at]
    recent_messages = history[split_at:]

    # 构建摘要
    history_text = "\n".join(
        f"[{msg.get('role', 'unknown')}]: {str(msg.get('content', ''))[:300]}"
        for msg in old_messages
        if msg.get("role") in ("user", "assistant")
    )
    summary_prompt = SUMMARY_TEMPLATE.format(
        max_chars=COMPACTED_MAX,
        history_text=history_text[:8000],
    )

    try:
        from ...utils.llm_client import get_llm_client
        from ..agent_runtime.model_router import route_model

        route = route_model("ask_answer")
        llm = get_llm_client()
        summary = llm.chat(
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.2,
            max_tokens=1000,
            model_override=route["model"],
        )
        if not summary or len(summary.strip()) < 20:
            # 摘要失败，保留最近消息即可
            summary = f"（对话已压缩，保留最近 {keep_recent} 轮）"
    except Exception as e:
        logger.warning("compaction LLM failed: %s", e)
        summary = f"（对话已压缩，保留最近 {keep_recent} 轮）"

    compacted_tokens = _estimate_tokens(summary) + _estimate_history_tokens(recent_messages)

    logger.info(
        "compaction: %d msgs → summary(%d tokens) + %d recent, %d → %d tokens",
        len(old_messages),
        _estimate_tokens(summary),
        len(recent_messages),
        total_tokens,
        compacted_tokens,
    )

    return {
        "compacted": True,
        "summary": summary.strip(),
        "recent": recent_messages,
        "original_tokens": total_tokens,
        "compacted_tokens": compacted_tokens,
    }


def inject_compaction_summary(
    summary: str,
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """将压缩摘要注入 history 开头，格式对齐 OpenCode 的 <conversation-checkpoint>。"""
    if not summary:
        return history
    checkpoint = {
        "role": "system",
        "content": f"<conversation-checkpoint>\n<summary>\n{summary}\n</summary>\n</conversation-checkpoint>",
    }
    return [checkpoint] + history
