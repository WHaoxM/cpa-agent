"""可重放状态管理

提供状态可恢复的能力，支持：
- 状态版本控制
- 状态撤销/重做
- 状态快照
- 批量状态更新
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

T = TypeVar('T')


@dataclass
class StateDraft(Generic[T]):
    """状态草稿"""
    value: T
    _transforms: List[Callable] = field(default_factory=list, repr=False)
    
    def transform(self, fn: Callable[[T], T]):
        """转换状态"""
        self._transforms.append(fn)
        return self
    
    def apply(self) -> T:
        """应用所有转换"""
        result = self.value
        for transform in self._transforms:
            result = transform(result)
        return result


@dataclass
class StateSnapshot:
    """状态快照"""
    version: int
    state: Any
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReplayableState(Generic[T]):
    """可重放状态"""
    
    def __init__(self, initial: T, name: str = "state"):
        self._initial = initial
        self._current = initial
        self._history: List[T] = [initial]
        self._future: List[T] = []  # 用于重做
        self._version = 0
        self._name = name
        self._snapshots: List[StateSnapshot] = []
        self._change_listeners: List[Callable] = []
    
    def get(self) -> T:
        """获取当前状态"""
        return self._current
    
    def set(self, value: T, metadata: Dict[str, Any] = None):
        """设置状态"""
        self._current = value
        self._history.append(value)
        self._future.clear()  # 新操作清空重做栈
        self._version += 1
        
        # 通知监听器
        for listener in self._change_listeners:
            try:
                listener(self._name, value, metadata)
            except Exception as e:
                print(f"Listener error: {e}")
    
    def update(self, fn: Callable[[T], T], metadata: Dict[str, Any] = None):
        """更新状态"""
        new_value = fn(self._current)
        self.set(new_value, metadata)
    
    @asynccontextmanager
    async def batch(self):
        """批量状态更新"""
        draft = StateDraft(self._current)
        yield draft
        new_value = draft.apply()
        self.set(new_value, {"batch": True})
    
    def undo(self) -> Optional[T]:
        """撤销"""
        if len(self._history) > 1:
            current = self._history.pop()
            self._future.append(current)
            self._current = self._history[-1]
            self._version -= 1
            return self._current
        return None
    
    def redo(self) -> Optional[T]:
        """重做"""
        if self._future:
            next_state = self._future.pop()
            self._history.append(next_state)
            self._current = next_state
            self._version += 1
            return self._current
        return None
    
    def can_undo(self) -> bool:
        """是否可以撤销"""
        return len(self._history) > 1
    
    def can_redo(self) -> bool:
        """是否可以重做"""
        return len(self._future) > 0
    
    def get_version(self) -> int:
        """获取版本号"""
        return self._version
    
    def get_history_length(self) -> int:
        """获取历史长度"""
        return len(self._history)
    
    def snapshot(self, metadata: Dict[str, Any] = None) -> StateSnapshot:
        """创建快照"""
        snapshot = StateSnapshot(
            version=self._version,
            state=copy.deepcopy(self._current),
            timestamp=__import__('time').time(),
            metadata=metadata or {},
        )
        self._snapshots.append(snapshot)
        return snapshot
    
    def restore_snapshot(self, snapshot: StateSnapshot):
        """恢复快照"""
        self._current = copy.deepcopy(snapshot.state)
        self._history.append(self._current)
        self._future.clear()
        self._version = snapshot.version
    
    def get_snapshots(self) -> List[StateSnapshot]:
        """获取所有快照"""
        return self._snapshots.copy()
    
    def add_change_listener(self, listener: Callable):
        """添加变更监听器"""
        self._change_listeners.append(listener)
    
    def remove_change_listener(self, listener: Callable):
        """移除变更监听器"""
        self._change_listeners = [
            l for l in self._change_listeners if l != listener
        ]
    
    def reset(self):
        """重置到初始状态"""
        self._current = self._initial
        self._history = [self._initial]
        self._future.clear()
        self._version = 0
    
    def get_state_at_version(self, version: int) -> Optional[T]:
        """获取指定版本的状态"""
        if 0 <= version < len(self._history):
            return self._history[version]
        return None


class StateManager:
    """状态管理器"""
    
    def __init__(self):
        self._states: Dict[str, ReplayableState] = {}
    
    def create_state(self, name: str, initial: Any) -> ReplayableState:
        """创建状态"""
        state = ReplayableState(initial, name)
        self._states[name] = state
        return state
    
    def get_state(self, name: str) -> Optional[ReplayableState]:
        """获取状态"""
        return self._states.get(name)
    
    def remove_state(self, name: str):
        """移除状态"""
        self._states.pop(name, None)
    
    def list_states(self) -> List[Dict[str, Any]]:
        """列出所有状态"""
        return [
            {
                "name": state._name,
                "version": state.get_version(),
                "history_length": state.get_history_length(),
                "can_undo": state.can_undo(),
                "can_redo": state.can_redo(),
            }
            for state in self._states.values()
        ]
    
    def snapshot_all(self) -> Dict[str, StateSnapshot]:
        """创建所有状态的快照"""
        return {
            name: state.snapshot()
            for name, state in self._states.items()
        }
    
    def restore_all(self, snapshots: Dict[str, StateSnapshot]):
        """恢复所有状态"""
        for name, snapshot in snapshots.items():
            if name in self._states:
                self._states[name].restore_snapshot(snapshot)


# 预定义状态类型

def create_session_state(session_id: str) -> ReplayableState:
    """创建会话状态"""
    initial = {
        "session_id": session_id,
        "messages": [],
        "tools_used": [],
        "files_accessed": [],
        "context": {},
    }
    return ReplayableState(initial, name=f"session_{session_id}")


def create_user_state(user_id: str) -> ReplayableState:
    """创建用户状态"""
    initial = {
        "user_id": user_id,
        "preferences": {},
        "history": [],
        "bookmarks": [],
    }
    return ReplayableState(initial, name=f"user_{user_id}")


# 全局实例
_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """获取状态管理器单例"""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
