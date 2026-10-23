"""Session Compaction - 参考OpenCode架构

核心设计：
1. 自动检测上下文是否超限
2. 使用LLM生成对话摘要
3. 替换早期历史为摘要
4. 支持连续compaction
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class CompactionConfig:
    """Compaction配置"""
    auto: bool = True  # 是否自动compaction
    max_tokens: int = 8000  # 保留的近期token数
    buffer: int = 1000  # 缓冲区
    summary_max_tokens: int = 4096  # 摘要最大token数


@dataclass
class CompactionResult:
    """Compaction结果"""
    compacted: bool
    summary: str = ""
    recent_context: str = ""
    tokens_removed: int = 0


class SessionCompaction:
    """
    Session Compaction - 参考OpenCode SessionCompaction
    
    职责：
    1. 检测上下文是否需要compaction
    2. 选择要保留的近期上下文
    3. 使用LLM生成摘要
    4. 返回compaction结果
    """
    
    def __init__(self, llm_client: Any = None, config: Optional[CompactionConfig] = None):
        self.llm_client = llm_client
        self.config = config or CompactionConfig()
    
    def compact_if_needed(
        self,
        messages: List[Dict[str, Any]],
        context_limit: int = 12000,
        output_tokens: int = 2000,
    ) -> CompactionResult:
        """
        检测是否需要compaction，如果需要则执行
        
        Args:
            messages: 当前消息列表
            context_limit: 上下文限制
            output_tokens: 输出token预算
        
        Returns:
            CompactionResult
        """
        if not self.config.auto:
            return CompactionResult(compacted=False)
        
        # 估算当前token数
        estimated_tokens = self._estimate_tokens(messages)
        
        # 检查是否超限
        if estimated_tokens <= context_limit - max(output_tokens, self.config.buffer):
            return CompactionResult(compacted=False)
        
        # 执行compaction
        return self.compact(messages, context_limit)
    
    def compact(
        self,
        messages: List[Dict[str, Any]],
        context_limit: int = 12000,
    ) -> CompactionResult:
        """
        执行compaction
        
        Args:
            messages: 当前消息列表
            context_limit: 上下文限制
        
        Returns:
            CompactionResult
        """
        if not self.llm_client:
            logger.warning("No LLM client for compaction")
            return CompactionResult(compacted=False)
        
        # 选择要保留的近期上下文
        selected, recent = self._select_messages(messages)
        
        if not selected:
            return CompactionResult(compacted=False)
        
        # 构建摘要prompt
        summary_prompt = self._build_summary_prompt(selected)
        
        try:
            # 使用LLM生成摘要
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.1,
                max_tokens=self.config.summary_max_tokens,
            )
            
            summary = response.strip()
            
            # 计算移除的token数
            removed_tokens = self._estimate_tokens(selected)
            
            return CompactionResult(
                compacted=True,
                summary=summary,
                recent_context=recent,
                tokens_removed=removed_tokens,
            )
            
        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            return CompactionResult(compacted=False)
    
    def _select_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        选择要保留的近期上下文
        
        Returns:
            (selected_messages, recent_context)
        """
        if not messages:
            return [], ""
        
        # 从尾部向前累加token估算
        recent_tokens = 0
        recent_messages = []
        selected_messages = []
        
        for msg in reversed(messages):
            msg_tokens = self._estimate_message_tokens(msg)
            
            if recent_tokens + msg_tokens <= self.config.max_tokens:
                recent_messages.insert(0, msg)
                recent_tokens += msg_tokens
            else:
                selected_messages.insert(0, msg)
        
        recent_context = "\n".join([
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')[:200]}"
            for m in recent_messages
        ])
        
        return selected_messages, recent_context
    
    def _build_summary_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """构建摘要prompt"""
        conversation = "\n".join([
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')[:500]}"
            for m in messages
        ])
        
        return f"""请为以下对话生成一个简洁的摘要。

对话内容：
{conversation}

摘要要求：
1. 包含：目标(Objective)、重要细节(Important Details)、工作状态(Work State)、下一步(Next Move)
2. 保持简洁，不超过500字
3. 保留关键信息和决策

输出格式：
目标：...
重要细节：...
工作状态：...
下一步：..."""
    
    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """估算消息列表的token数"""
        total = 0
        for msg in messages:
            total += self._estimate_message_tokens(msg)
        return total
    
    def _estimate_message_tokens(self, msg: Dict[str, Any]) -> int:
        """估算单条消息的token数"""
        content = msg.get("content", "")
        # 简单估算：1个中文字符 ≈ 2 tokens
        return len(content) * 2


# 全局实例
_session_compaction: Optional[SessionCompaction] = None


def get_session_compaction(
    llm_client: Any = None,
    config: Optional[CompactionConfig] = None,
) -> SessionCompaction:
    """获取SessionCompaction单例"""
    global _session_compaction
    if _session_compaction is None:
        _session_compaction = SessionCompaction(llm_client, config)
    return _session_compaction
