"""结构化Compaction

使用LLM生成结构化摘要，包含：
- objective: 当前目标
- important_details: 重要细节
- work_state: 工作状态
- next_move: 下一步
- relevant_files: 相关文件
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from .errors import TaggedError, ErrorTag


@dataclass
class CompactionSummary:
    """结构化摘要"""
    objective: str = ""
    important_details: str = ""
    work_state: str = ""
    next_move: str = ""
    relevant_files: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "objective": self.objective,
            "important_details": self.important_details,
            "work_state": self.work_state,
            "next_move": self.next_move,
            "relevant_files": self.relevant_files,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
    
    def to_prompt(self) -> str:
        """转换为prompt格式"""
        parts = []
        if self.objective:
            parts.append(f"## 当前目标\n{self.objective}")
        if self.important_details:
            parts.append(f"## 重要细节\n{self.important_details}")
        if self.work_state:
            parts.append(f"## 工作状态\n{self.work_state}")
        if self.next_move:
            parts.append(f"## 下一步\n{self.next_move}")
        if self.relevant_files:
            parts.append(f"## 相关文件\n" + "\n".join(f"- {f}" for f in self.relevant_files))
        return "\n\n".join(parts)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CompactionSummary:
        """从字典创建"""
        return cls(
            objective=data.get("objective", ""),
            important_details=data.get("important_details", ""),
            work_state=data.get("work_state", ""),
            next_move=data.get("next_move", ""),
            relevant_files=data.get("relevant_files", []),
            timestamp=data.get("timestamp", datetime.now().timestamp()),
            metadata=data.get("metadata", {}),
        )


class StructuredCompaction:
    """结构化Compaction"""
    
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        self._cache: Dict[str, CompactionSummary] = {}
    
    def generate_summary(self, messages: List[Dict[str, Any]], session_id: str = None) -> CompactionSummary:
        """
        生成结构化摘要
        
        Args:
            messages: 消息列表
            session_id: 会话ID（用于缓存）
        
        Returns:
            CompactionSummary
        """
        if not messages:
            return CompactionSummary()
        
        # 如果没有LLM客户端，使用规则方法
        if not self.llm_client:
            summary = self._generate_rule_based_summary(messages)
        else:
            summary = self._generate_llm_summary(messages)
        
        # 缓存结果
        if session_id:
            self._cache[session_id] = summary
        
        return summary
    
    def _generate_llm_summary(self, messages: List[Dict[str, Any]]) -> CompactionSummary:
        """使用LLM生成摘要"""
        prompt = self._build_prompt(messages)
        
        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            )
            return self._parse_response(response)
        except Exception as e:
            return self._generate_rule_based_summary(messages)
    
    def _generate_rule_based_summary(self, messages: List[Dict[str, Any]]) -> CompactionSummary:
        """使用规则方法生成摘要"""
        # 提取最后几条消息作为上下文
        recent_messages = messages[-5:] if len(messages) > 5 else messages
        
        # 简单规则：提取最后的用户消息作为目标
        objective = ""
        for msg in reversed(recent_messages):
            if msg.get("role") == "user":
                objective = msg.get("content", "")[:200]
                break
        
        # 提取所有消息的关键词
        all_content = " ".join(m.get("content", "") for m in messages)
        
        # 简单的关键词提取
        important_keywords = []
        keywords = ["简历", "技能", "教育", "工作", "项目", "经验", "规划", "面试"]
        for keyword in keywords:
            if keyword in all_content:
                important_keywords.append(keyword)
        
        return CompactionSummary(
            objective=objective or "继续对话",
            important_details=f"涉及关键词：{', '.join(important_keywords)}" if important_keywords else "",
            work_state="对话进行中",
            next_move="等待用户输入",
            relevant_files=[],
        )
    
    def _build_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """构建prompt"""
        formatted_messages = self._format_messages(messages)
        
        return f"""请分析以下对话，生成结构化摘要。

对话内容：
{formatted_messages}

请输出JSON格式：
{{
    "objective": "当前目标（简短描述）",
    "important_details": "重要细节（关键信息点）",
    "work_state": "工作状态（当前进展）",
    "next_move": "下一步（建议的下一步行动）",
    "relevant_files": ["相关文件列表（如果有）"]
}}"""
    
    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        """格式化消息"""
        parts = []
        for msg in messages[-10:]:  # 只取最后10条
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]  # 限制长度
            parts.append(f"[{role}]: {content}")
        return "\n".join(parts)
    
    def _parse_response(self, response: str) -> CompactionSummary:
        """解析LLM响应"""
        try:
            # 尝试解析JSON
            data = json.loads(response)
            return CompactionSummary.from_dict(data)
        except json.JSONDecodeError:
            # 尝试从文本中提取JSON
            try:
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                    return CompactionSummary.from_dict(data)
            except Exception:
                pass
            
            # 返回默认摘要
            return CompactionSummary(
                objective="对话摘要",
                important_details=response[:200] if response else "",
                work_state="已完成",
                next_move="等待用户输入",
            )
    
    def get_cached_summary(self, session_id: str) -> Optional[CompactionSummary]:
        """获取缓存的摘要"""
        return self._cache.get(session_id)
    
    def clear_cache(self, session_id: str = None):
        """清除缓存"""
        if session_id:
            self._cache.pop(session_id, None)
        else:
            self._cache.clear()


# 全局实例
_compaction: Optional[StructuredCompaction] = None


def get_compaction(llm_client: Any = None) -> StructuredCompaction:
    """获取Compaction单例"""
    global _compaction
    if _compaction is None:
        _compaction = StructuredCompaction(llm_client)
    return _compaction


def generate_summary(messages: List[Dict[str, Any]], session_id: str = None) -> CompactionSummary:
    """生成摘要的便捷函数"""
    return get_compaction().generate_summary(messages, session_id)
