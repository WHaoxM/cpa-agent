"""事件溯源架构

提供完整的事件溯源系统，支持：
- 事件发布和订阅
- 事件持久化
- 事件重放
- 状态恢复
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


@dataclass
class DurableEvent:
    """持久化事件"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: time.time())
    session_id: str = ""
    version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "version": self.version,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DurableEvent:
        """从字典创建"""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=data.get("type", ""),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            version=data.get("version", 1),
            metadata=data.get("metadata", {}),
        )


class EventType:
    """事件类型常量"""
    SESSION_CREATED = "session.created"
    SESSION_MESSAGE_ADDED = "session.message.added"
    SESSION_TOOL_EXECUTED = "session.tool.executed"
    SESSION_COMPACTION_COMPLETED = "session.compaction.completed"
    SESSION_STATE_CHANGED = "session.state.changed"
    SESSION_CLOSED = "session.closed"
    
    USER_MESSAGE = "user.message"
    ASSISTANT_MESSAGE = "assistant.message"
    SYSTEM_MESSAGE = "system.message"
    
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    
    FILE_UPLOADED = "file.uploaded"
    FILE_PROCESSED = "file.processed"
    
    ROLE_ACTIVATED = "role.activated"
    ROLE_DEACTIVATED = "role.deactivated"


class EventBus:
    """事件总线"""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._event_store: List[DurableEvent] = []
        self._max_store_size = 10000
    
    def publish(self, event: DurableEvent):
        """发布事件"""
        self._event_store.append(event)
        
        # 限制存储大小
        if len(self._event_store) > self._max_store_size:
            self._event_store = self._event_store[-self._max_store_size // 2:]
        
        self._notify_subscribers(event)
    
    def subscribe(self, event_type: str, handler: Callable):
        """订阅事件"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: str, handler: Callable):
        """取消订阅"""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type]
                if h != handler
            ]
    
    def _notify_subscribers(self, event: DurableEvent):
        """通知订阅者"""
        handlers = self._subscribers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"Handler error for {event.type}: {e}")
        
        # 通知通配符订阅者
        wildcard_handlers = self._subscribers.get("*", [])
        for handler in wildcard_handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"Wildcard handler error: {e}")
    
    def get_events(
        self,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[DurableEvent]:
        """获取事件"""
        events = self._event_store
        
        if session_id:
            events = [e for e in events if e.session_id == session_id]
        
        if event_type:
            events = [e for e in events if e.type == event_type]
        
        return events[-limit:]
    
    def replay(self, session_id: str) -> Dict[str, Any]:
        """重放事件"""
        events = self.get_events(session_id=session_id)
        state = {}
        for event in events:
            state = self._apply_event(state, event)
        return state
    
    def _apply_event(self, state: Dict, event: DurableEvent) -> Dict:
        """应用事件到状态"""
        if event.type == EventType.SESSION_CREATED:
            return {
                "id": event.session_id,
                "created": event.timestamp,
                "version": event.version,
            }
        elif event.type == EventType.SESSION_STATE_CHANGED:
            state.update(event.payload)
            state["version"] = event.version
        elif event.type == EventType.SESSION_CLOSED:
            state["closed"] = event.timestamp
        return state
    
    def clear(self, session_id: Optional[str] = None):
        """清除事件"""
        if session_id:
            self._event_store = [
                e for e in self._event_store
                if e.session_id != session_id
            ]
        else:
            self._event_store.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        type_counts = {}
        for event in self._event_store:
            type_counts[event.type] = type_counts.get(event.type, 0) + 1
        
        return {
            "total_events": len(self._event_store),
            "event_types": type_counts,
            "subscribers": {
                k: len(v) for k, v in self._subscribers.items()
            },
        }


class EventProjector:
    """事件投影器"""
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._projections: Dict[str, Callable] = {}
        self._projection_store: Dict[str, Any] = {}
    
    def register_projection(self, name: str, handler: Callable):
        """注册投影"""
        self._projections[name] = handler
        self._projection_store[name] = {}
    
    def project(self, event: DurableEvent):
        """投影事件"""
        for name, handler in self._projections.items():
            try:
                self._projection_store[name] = handler(
                    self._projection_store[name],
                    event,
                )
            except Exception as e:
                print(f"Projection error for {name}: {e}")
    
    def get_projection(self, name: str) -> Any:
        """获取投影结果"""
        return self._projection_store.get(name)
    
    def get_all_projections(self) -> Dict[str, Any]:
        """获取所有投影结果"""
        return self._projection_store.copy()
    
    def reset_projection(self, name: str):
        """重置投影"""
        self._projection_store[name] = {}


# 预定义投影

def session_projection(state: Dict, event: DurableEvent) -> Dict:
    """会话投影"""
    if event.type == EventType.SESSION_CREATED:
        return {
            "session_id": event.session_id,
            "created": event.timestamp,
            "message_count": 0,
            "tool_count": 0,
        }
    elif event.type == EventType.SESSION_MESSAGE_ADDED:
        state["message_count"] = state.get("message_count", 0) + 1
    elif event.type == EventType.SESSION_TOOL_EXECUTED:
        state["tool_count"] = state.get("tool_count", 0) + 1
    return state


def user_activity_projection(state: Dict, event: DurableEvent) -> Dict:
    """用户活动投影"""
    if event.type == EventType.USER_MESSAGE:
        user_id = event.metadata.get("user_id", "unknown")
        if user_id not in state:
            state[user_id] = {"messages": 0, "last_active": 0}
        state[user_id]["messages"] += 1
        state[user_id]["last_active"] = event.timestamp
    return state


# 全局实例
_event_bus: Optional[EventBus] = None
_event_projector: Optional[EventProjector] = None


def get_event_bus() -> EventBus:
    """获取事件总线单例"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def get_event_projector() -> EventProjector:
    """获取事件投影器单例"""
    global _event_projector
    if _event_projector is None:
        _event_projector = EventProjector(get_event_bus())
        # 注册默认投影
        _event_projector.register_projection("session", session_projection)
        _event_projector.register_projection("user_activity", user_activity_projection)
    return _event_projector
