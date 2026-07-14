"""
Prompt 注册表加载器 — 阶段一

加载顺序: config/prompts/<prompt_id>/base.md (只读) + tone.md
→ workspace/overrides/prompts/<prompt_id>/tone.md (若存在则覆盖 tone)

registry.yaml 为唯一索引; base.md 不可被 workspace 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .loader import _backend_root, resolve_workspace_dir, RulesConfigError

_PROMPT_CACHE: Optional["PromptRegistry"] = None


class PromptRegistryError(Exception):
    """Prompt 注册表加载或校验失败"""


class PromptRegistry:
    """Prompt 注册表 — 读取 registry.yaml 并按 prompt_id 加载 base+tone"""

    def __init__(self):
        self._prompts_dir = _backend_root() / "config" / "prompts"
        self._registry_path = self._prompts_dir / "registry.yaml"
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        if not self._registry_path.is_file():
            raise PromptRegistryError(f"registry.yaml 不存在: {self._registry_path}")
        with self._registry_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "prompts" not in data:
            raise PromptRegistryError("registry.yaml 根节点必须含 prompts 列表")
        prompts = data["prompts"]
        if not isinstance(prompts, list):
            raise PromptRegistryError("registry.yaml: prompts 必须是列表")
        for entry in prompts:
            pid = entry.get("id")
            if not pid:
                raise PromptRegistryError(f"registry.yaml 条目缺少 id: {entry}")
            self._entries[pid] = entry

    def _read_file(self, path: Path) -> str:
        if not path.is_file():
            raise PromptRegistryError(f"Prompt 文件不存在: {path}")
        return path.read_text(encoding="utf-8")

    def _resolve_tone_path(self, prompt_id: str, repo_tone_rel: str) -> Path:
        """workspace 覆盖优先; 否则用 repo 默认 tone.md"""
        ws = resolve_workspace_dir()
        if ws is not None:
            ws_tone = ws / "overrides" / "prompts" / repo_tone_rel
            if ws_tone.is_file():
                return ws_tone
        return self._prompts_dir / repo_tone_rel

    def load(self, prompt_id: str, **variables: Any) -> str:
        """加载并组装 prompt: base + tone, 再填充变量"""
        if prompt_id not in self._entries:
            raise PromptRegistryError(f"未知 prompt_id: {prompt_id}")
        entry = self._entries[prompt_id]
        base_rel = entry["base"]
        tone_rel = entry["tone"]

        base = self._read_file(self._prompts_dir / base_rel)
        tone = self._read_file(self._resolve_tone_path(prompt_id, tone_rel))

        prompt = base.rstrip() + "\n\n" + tone.rstrip() + "\n"

        if variables:
            try:
                prompt = prompt.format(**variables)
            except KeyError as e:
                raise PromptRegistryError(
                    f"prompt_id={prompt_id} 缺少变量: {e}"
                )
        return prompt

    def list_prompt_ids(self) -> List[str]:
        return list(self._entries.keys())


def init_prompt_registry(*, force: bool = False) -> PromptRegistry:
    """启动时加载 Prompt 注册表"""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None and not force:
        return _PROMPT_CACHE
    _PROMPT_CACHE = PromptRegistry()
    return _PROMPT_CACHE


def get_prompt_registry() -> PromptRegistry:
    if _PROMPT_CACHE is None:
        return init_prompt_registry()
    return _PROMPT_CACHE


def load_prompt(prompt_id: str, **variables: Any) -> str:
    """便捷函数: 加载并组装 prompt"""
    return get_prompt_registry().load(prompt_id, **variables)
