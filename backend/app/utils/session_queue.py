"""会话输入队列

支持用户在Agent执行中注入消息，提供两种交付模式：
- steer: 立即执行（中断当前Agent）
- queue: 排队等待（当前Agent完成后执行）
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import uuid


class DeliveryMode(Enum):
    """交付模式"""
    STEER = "steer"    # 立即执行（中断当前Agent）
    QUEUE = "queue"    # 排队等待（当前Agent完成后执行）


@dataclass
class QueueItem:
    """队列项"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message: str = ""
    mode: DeliveryMode = DeliveryMode.QUEUE
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, processing, completed, failed


class SessionInputQueue:
    """会话输入队列"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._steer_queue: deque[QueueItem] = deque()
        self._pending_queue: deque[QueueItem] = deque()
        self._current_task: Optional[asyncio.Task] = None
        self._processing = False
        self._callbacks: Dict[str, Callable] = {}
    
    def admit(self, message: str, mode: DeliveryMode = DeliveryMode.QUEUE, **kwargs) -> QueueItem:
        """
        输入消息
        
        Args:
            message: 消息内容
            mode: 交付模式
            **kwargs: 额外元数据
        
        Returns:
            QueueItem
        """
        item = QueueItem(
            message=message,
            mode=mode,
            metadata=kwargs,
        )
        
        if mode == DeliveryMode.STEER:
            self._steer_queue.append(item)
            self._interrupt_current()
        else:
            self._pending_queue.append(item)
        
        return item
    
    def next(self) -> Optional[QueueItem]:
        """获取下一条消息"""
        if self._steer_queue:
            return self._steer_queue.popleft()
        if self._pending_queue:
            return self._pending_queue.popleft()
        return None
    
    def peek(self) -> Optional[QueueItem]:
        """查看下一条消息（不取出）"""
        if self._steer_queue:
            return self._steer_queue[0]
        if self._pending_queue:
            return self._pending_queue[0]
        return None
    
    def _interrupt_current(self):
        """中断当前任务"""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
    
    def set_current_task(self, task: asyncio.Task):
        """设置当前任务"""
        self._current_task = task
    
    def cancel_current(self):
        """取消当前任务"""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self._current_task = None
    
    def size(self) -> Dict[str, int]:
        """获取队列大小"""
        return {
            "steer": len(self._steer_queue),
            "pending": len(self._pending_queue),
            "total": len(self._steer_queue) + len(self._pending_queue),
        }
    
    def clear(self):
        """清空队列"""
        self._steer_queue.clear()
        self._pending_queue.clear()
    
    def get_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        return {
            "session_id": self.session_id,
            "queue_size": self.size(),
            "current_task": self._current_task.id() if self._current_task else None,
            "processing": self._processing,
        }
    
    def mark_completed(self, item_id: str):
        """标记项目完成"""
        # 这里可以添加完成逻辑
        pass
    
    def mark_failed(self, item_id: str, error: str):
        """标记项目失败"""
        # 这里可以添加失败逻辑
        pass


class QueueManager:
    """队列管理器"""
    
    def __init__(self):
        self._queues: Dict[str, SessionInputQueue] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
    
    def get_queue(self, session_id: str) -> SessionInputQueue:
        """获取会话队列"""
        if session_id not in self._queues:
            self._queues[session_id] = SessionInputQueue(session_id)
        return self._queues[session_id]
    
    def remove_queue(self, session_id: str):
        """移除会话队列"""
        if session_id in self._queues:
            del self._queues[session_id]
    
    def list_queues(self) -> List[Dict[str, Any]]:
        """列出所有队列"""
        return [
            queue.get_status()
            for queue in self._queues.values()
        ]
    
    def subscribe(self, event: str, callback: Callable):
        """订阅事件"""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)
    
    def _notify(self, event: str, data: Any):
        """通知订阅者"""
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                print(f"Callback error: {e}")


# 全局实例
_queue_manager: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    """获取队列管理器单例"""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = QueueManager()
    return _queue_manager


def get_session_queue(session_id: str) -> SessionInputQueue:
    """获取会话队列"""
    return get_queue_manager().get_queue(session_id)
