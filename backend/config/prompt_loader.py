"""
Prompt 注册表加载器 — 阶段一

加载顺序: config/prompts/<prompt_id>/base.md (只读) + tone.md
→ workspace/overrides/prompts/<prompt_id>/tone.md (若存在则覆盖 tone)

registry.yaml 为唯一索引; base.md 不可被 workspace 覆盖。
"""

from __future__ import annotations

import os
import hashlib
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        self._runtime_routes: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
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
            if pid in self._entries:
                raise PromptRegistryError(f"registry.yaml 重复 prompt_id: {pid}")
            self._validate_entry_files(pid, entry)
            self._entries[pid] = entry
        routes = data.get("runtime_routes") or []
        if routes:
            if not isinstance(routes, list):
                raise PromptRegistryError("registry.yaml: runtime_routes 必须是列表")
            for route in routes:
                key = self._runtime_route_key(route)
                if key in self._runtime_routes:
                    raise PromptRegistryError(f"runtime_routes 重复 route: {key}")
                prompt_ids = route.get("prompt_ids")
                if not isinstance(prompt_ids, list) or not prompt_ids:
                    raise PromptRegistryError(f"runtime_route 缺少 prompt_ids: {route}")
                unknown = [pid for pid in prompt_ids if pid not in self._entries]
                if unknown:
                    raise PromptRegistryError(f"runtime_route 引用未知 prompt_id: {unknown}")
                self._runtime_routes[key] = dict(route)

    def _validate_entry_files(self, prompt_id: str, entry: Dict[str, Any]) -> None:
        for layer in ("base", "tone"):
            rel = entry.get(layer)
            if not rel:
                raise PromptRegistryError(f"prompt_id={prompt_id} 缺少 {layer}")
            path = self._prompts_dir / str(rel)
            if not path.is_file():
                raise PromptRegistryError(f"Prompt 文件不存在: {path}")
        declared = set(entry.get("variables") or [])
        for layer in ("base", "tone"):
            text = (self._prompts_dir / str(entry[layer])).read_text(encoding="utf-8")
            for name in _extract_format_fields(text, prompt_id=prompt_id):
                root = name.split(".", 1)[0].split("[", 1)[0]
                if root and root not in declared:
                    raise PromptRegistryError(
                        f"prompt_id={prompt_id} 存在未声明变量: {root}"
                    )

    def _runtime_route_key(self, route: Dict[str, Any]) -> Tuple[str, str, str, str]:
        try:
            engine = str(route["engine"]).strip().lower()
            mode = str(route["mode"]).strip().lower()
            kind = str(route["content_kind"]).strip().lower()
        except KeyError as e:
            raise PromptRegistryError(f"runtime_route 缺少字段: {e}") from e
        subagent = str(route.get("subagent") or "").strip().lower()
        if not engine or not mode or not kind:
            raise PromptRegistryError(f"runtime_route 字段不能为空: {route}")
        return engine, mode, kind, subagent

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

        declared = list(entry.get("variables") or [])
        missing = [v for v in declared if v not in variables]
        if missing:
            raise PromptRegistryError(f"prompt_id={prompt_id} 缺少变量: {missing[0]}")
        try:
            prompt = prompt.format(**variables)
        except KeyError as e:
            raise PromptRegistryError(f"prompt_id={prompt_id} 缺少变量: {e}") from e
        except ValueError as e:
            raise PromptRegistryError(f"prompt_id={prompt_id} 模板格式非法: {e}") from e
        return prompt

    def list_prompt_ids(self) -> List[str]:
        return list(self._entries.keys())

    def get_entry(self, prompt_id: str) -> Dict[str, Any]:
        if prompt_id not in self._entries:
            raise PromptRegistryError(f"未知 prompt_id: {prompt_id}")
        return dict(self._entries[prompt_id])

    def prompt_fingerprint(self, prompt_id: str) -> str:
        """Hash the effective, unrendered prompt layers and declared version."""
        entry = self.get_entry(prompt_id)
        digest = hashlib.sha256()
        digest.update(str(entry.get("version", 1)).encode("utf-8"))
        for layer in ("base", "tone"):
            relative = str(entry[layer])
            path = (
                self._prompts_dir / relative
                if layer == "base"
                else self._resolve_tone_path(prompt_id, relative)
            )
            digest.update(b"\0" + layer.encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def get_runtime_route(
        self,
        *,
        engine: str,
        mode: str,
        content_kind: str,
        subagent: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        key = (
            str(engine).strip().lower(),
            str(mode).strip().lower(),
            str(content_kind).strip().lower(),
            str(subagent or "").strip().lower(),
        )
        route = self._runtime_routes.get(key)
        return dict(route) if route else None

    def list_runtime_routes(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._runtime_routes.values()]


def _extract_format_fields(text: str, *, prompt_id: str) -> List[str]:
    fields: List[str] = []
    try:
        for _, field_name, _, _ in string.Formatter().parse(text):
            if field_name:
                fields.append(str(field_name))
    except ValueError as e:
        raise PromptRegistryError(f"prompt_id={prompt_id} 模板格式非法: {e}") from e
    return fields


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
