"""
System Context Epochs — prompt 版本管理。

解决场景：管理员更新了 tone.md（prompt 语气层），但正在运行的会话仍用旧版 system prompt。

方案：给 prompt 注册表加版本号，session 记录初始化时的 epoch，每轮校验是否需要刷新。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ContextEpoch:
    """System context 版本快照。"""

    prompt_versions: Dict[str, int]  # prompt_id → version
    created_at: float = field(default_factory=time.time)

    @classmethod
    def current(cls) -> "ContextEpoch":
        """从 registry 读取当前版本。"""
        from config.prompt_loader import get_prompt_registry

        registry = get_prompt_registry()
        versions: Dict[str, int] = {}
        for prompt_id in registry.list_prompt_ids():
            entry = registry.get_entry(prompt_id)
            versions[prompt_id] = entry.get("version", 1)
        return ContextEpoch(prompt_versions=versions, created_at=time.time())

    def needs_refresh(self, other: "ContextEpoch") -> bool:
        """与另一个 epoch 对比，是否有 prompt 版本变化。"""
        for pid, ver in self.prompt_versions.items():
            if other.prompt_versions.get(pid, 0) < ver:
                return True
        return False

    def changed_prompts(self, other: "ContextEpoch") -> List[str]:
        """返回在 other 之后更新过的 prompt_id 列表。"""
        changed: List[str] = []
        for pid, ver in self.prompt_versions.items():
            if other.prompt_versions.get(pid, 0) < ver:
                changed.append(pid)
        return changed

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可存储的字典。"""
        return {
            "prompt_versions": self.prompt_versions,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextEpoch":
        """从字典反序列化。"""
        return cls(
            prompt_versions=data.get("prompt_versions", {}),
            created_at=data.get("created_at", 0.0),
        )


def build_refresh_notice(changed: List[str], max_items: int = 3) -> str:
    """构建用户可见的 prompt 变更通知。"""
    if not changed:
        return ""
    display = changed[:max_items]
    suffix = f" 等 {len(changed)} 项" if len(changed) > max_items else ""
    return f"【系统提示已更新】涉及：{', '.join(display)}{suffix}。请重新阅读最新指令。"
