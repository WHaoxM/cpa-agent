"""SystemContext Source抽象

提供类型安全的上下文源组合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

T = TypeVar('T')


@dataclass
class SystemContextSource(Generic[T]):
    """系统上下文源"""
    name: str
    baseline: Callable[[], T]           # 基线值
    update: Callable[[T], T]            # 更新函数
    removed: Callable[[], str]          # 移除时的渲染
    serialize: Callable[[T], str]       # 序列化
    description: str = ""
    
    def get_baseline(self) -> T:
        """获取基线值"""
        return self.baseline()
    
    def update_value(self, prev: T) -> T:
        """更新值"""
        return self.update(prev)
    
    def render_removed(self) -> str:
        """渲染移除时的内容"""
        return self.removed()
    
    def serialize_value(self, value: T) -> str:
        """序列化值"""
        return self.serialize(value)


class SystemContext:
    """系统上下文组合器"""
    
    def __init__(self):
        self._sources: Dict[str, SystemContextSource] = {}
        self._values: Dict[str, Any] = {}
        self._snapshots: List[Dict[str, Any]] = []
    
    def register(self, source: SystemContextSource):
        """注册上下文源"""
        self._sources[source.name] = source
        self._values[source.name] = source.get_baseline()
    
    def unregister(self, name: str):
        """注销上下文源"""
        if name in self._sources:
            del self._sources[name]
            del self._values[name]
    
    def update(self, source_name: str):
        """更新上下文源"""
        if source_name in self._sources:
            prev = self._values.get(source_name)
            self._values[source_name] = self._sources[source_name].update_value(prev)
    
    def update_all(self):
        """更新所有上下文源"""
        for name in self._sources:
            self.update(name)
    
    def remove(self, source_name: str) -> str:
        """移除上下文源"""
        if source_name in self._sources:
            value = self._values.pop(source_name, None)
            return self._sources[source_name].render_removed()
        return ""
    
    def set_value(self, source_name: str, value: Any):
        """设置值"""
        if source_name in self._sources:
            self._values[source_name] = value
    
    def get_value(self, source_name: str) -> Any:
        """获取值"""
        return self._values.get(source_name)
    
    def compose(self) -> str:
        """组合所有上下文"""
        parts = []
        for name, value in self._values.items():
            if name in self._sources:
                serialized = self._sources[name].serialize_value(value)
                if serialized:
                    parts.append(serialized)
        return "\n".join(parts)
    
    def snapshot(self) -> Dict[str, Any]:
        """创建快照"""
        snapshot = {
            "values": self._values.copy(),
            "sources": list(self._sources.keys()),
        }
        self._snapshots.append(snapshot)
        return snapshot
    
    def restore(self, snapshot: Dict[str, Any]):
        """恢复快照"""
        self._values = snapshot.get("values", {}).copy()
    
    def list_sources(self) -> List[Dict[str, Any]]:
        """列出所有源"""
        return [
            {
                "name": source.name,
                "description": source.description,
                "has_value": name in self._values,
            }
            for name, source in self._sources.items()
        ]


# 预定义上下文源

def create_files_context(get_files: Callable[[], List[str]]) -> SystemContextSource:
    """创建文件上下文源"""
    return SystemContextSource(
        name="files",
        description="当前相关文件",
        baseline=lambda: [],
        update=lambda prev: get_files(),
        removed=lambda: "文件列表已清除",
        serialize=lambda files: "相关文件：\n" + "\n".join(f"- {f}" for f in files) if files else "",
    )


def create_memory_context(get_memory: Callable[[], str]) -> SystemContextSource:
    """创建记忆上下文源"""
    return SystemContextSource(
        name="memory",
        description="会话记忆",
        baseline=lambda: "",
        update=lambda prev: get_memory(),
        removed=lambda: "记忆已清除",
        serialize=lambda mem: f"## 记忆\n{mem}" if mem else "",
    )


def create_skills_context(get_skills: Callable[[], List[str]]) -> SystemContextSource:
    """创建技能上下文源"""
    return SystemContextSource(
        name="skills",
        description="可用技能",
        baseline=lambda: [],
        update=lambda prev: get_skills(),
        removed=lambda: "技能列表已清除",
        serialize=lambda skills: "可用技能：\n" + "\n".join(f"- {s}" for s in skills) if skills else "",
    )


def create_tools_context(get_tools: Callable[[], List[str]]) -> SystemContextSource:
    """创建工具上下文源"""
    return SystemContextSource(
        name="tools",
        description="可用工具",
        baseline=lambda: [],
        update=lambda prev: get_tools(),
        removed=lambda: "工具列表已清除",
        serialize=lambda tools: "可用工具：\n" + "\n".join(f"- {t}" for t in tools) if tools else "",
    )


# 全局实例
_system_context: Optional[SystemContext] = None


def get_system_context() -> SystemContext:
    """获取SystemContext单例"""
    global _system_context
    if _system_context is None:
        _system_context = SystemContext()
    return _system_context
