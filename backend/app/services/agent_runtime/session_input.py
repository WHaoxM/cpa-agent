"""Session Input 管理 - 参考OpenCode架构

核心设计：
1. 两种投递模式：steer（立即指导）和 queue（排队等待）
2. admit → promote 生命周期
3. 支持批量投递和单个投递
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from collections import deque
import threading


class DeliveryMode(Enum):
    """投递模式"""
    STEER = "steer"  # 立即指导（中断当前执行）
    QUEUE = "queue"  # 排队等待（当前执行完成后）


@dataclass
class InputItem:
    """输入项"""
    id: str
    session_id: str
    content: str
    delivery: DeliveryMode
    admitted_at: float = field(default_factory=time.time)
    promoted_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionInput:
    """
    Session Input 管理器 - 参考OpenCode SessionInput
    
    职责：
    1. 管理用户的输入（steer/queue两种模式）
    2. 提供admit → promote生命周期
    3. 支持批量投递和单个投递
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._inputs: Dict[str, List[InputItem]] = {}  # session_id -> inputs
        self._promoted_seq: Dict[str, int] = {}  # session_id -> last promoted seq
    
    def admit(
        self,
        session_id: str,
        content: str,
        delivery: DeliveryMode = DeliveryMode.QUEUE,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InputItem:
        """
        接受用户输入
        
        Args:
            session_id: 会话ID
            content: 输入内容
            delivery: 投递模式（steer/queue）
            metadata: 元数据
        
        Returns:
            InputItem
        """
        with self._lock:
            if session_id not in self._inputs:
                self._inputs[session_id] = []
            
            item = InputItem(
                id=f"input_{int(time.time() * 1000)}_{len(self._inputs[session_id])}",
                session_id=session_id,
                content=content,
                delivery=delivery,
                metadata=metadata or {},
            )
            
            self._inputs[session_id].append(item)
            return item
    
    def has_pending(self, session_id: str, delivery: DeliveryMode) -> bool:
        """
        检查是否有待处理的输入
        
        Args:
            session_id: 会话ID
            delivery: 投递模式
        
        Returns:
            是否有待处理的输入
        """
        with self._lock:
            inputs = self._inputs.get(session_id, [])
            return any(
                item.delivery == delivery and item.promoted_at is None
                for item in inputs
            )
    
    def promote_steers(self, session_id: str) -> List[InputItem]:
        """
        批量投递所有steer输入（OpenCode语义）
        
        Args:
            session_id: 会话ID
        
        Returns:
            投递的输入列表
        """
        with self._lock:
            inputs = self._inputs.get(session_id, [])
            promoted = []
            
            for item in inputs:
                if item.delivery == DeliveryMode.STEER and item.promoted_at is None:
                    item.promoted_at = time.time()
                    promoted.append(item)
            
            return promoted
    
    def promote_next_queued(self, session_id: str) -> Optional[InputItem]:
        """
        投递下一个queue输入（OpenCode语义）
        
        Args:
            session_id: 会话ID
        
        Returns:
            投递的输入，如果没有则返回None
        """
        with self._lock:
            inputs = self._inputs.get(session_id, [])
            
            # 找到第一个未投递的queue输入
            for item in inputs:
                if item.delivery == DeliveryMode.QUEUE and item.promoted_at is None:
                    item.promoted_at = time.time()
                    return item
            
            return None
    
    def get_pending_count(self, session_id: str) -> Dict[str, int]:
        """获取待处理输入计数"""
        with self._lock:
            inputs = self._inputs.get(session_id, [])
            return {
                "steer": sum(1 for i in inputs if i.delivery == DeliveryMode.STEER and i.promoted_at is None),
                "queue": sum(1 for i in inputs if i.delivery == DeliveryMode.QUEUE and i.promoted_at is None),
                "total": sum(1 for i in inputs if i.promoted_at is None),
            }
    
    def clear(self, session_id: str):
        """清空会话的所有输入"""
        with self._lock:
            self._inputs.pop(session_id, None)


# 全局实例
_session_input: Optional[SessionInput] = None


def get_session_input() -> SessionInput:
    """获取SessionInput单例"""
    global _session_input
    if _session_input is None:
        _session_input = SessionInput()
    return _session_input


def admit_input(
    session_id: str,
    content: str,
    delivery: DeliveryMode = DeliveryMode.QUEUE,
    metadata: Optional[Dict[str, Any]] = None,
) -> InputItem:
    """接受用户输入的便捷函数"""
    return get_session_input().admit(session_id, content, delivery, metadata)


def has_pending_input(session_id: str, delivery: DeliveryMode = DeliveryMode.STEER) -> bool:
    """检查是否有待处理输入的便捷函数"""
    return get_session_input().has_pending(session_id, delivery)
