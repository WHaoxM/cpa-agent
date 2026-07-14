"""
集成配置加载器 — 阶段二 2B

加载 config/defaults/integrations/*.yaml，与 .env 配合：
- .env: 密钥 (LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, MILVUS_URI, MYSQL_HOST, ...)
- YAML:  业务默认值 (provider presets, model names, dimensions, agent params)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .loader import _backend_root, RulesConfigError

_CACHE: Dict[str, Any] = {}


def _integrations_dir() -> Path:
    return _backend_root() / "config" / "defaults" / "integrations"


def _load_yaml(name: str) -> Dict[str, Any]:
    if name in _CACHE:
        return _CACHE[name]
    path = _integrations_dir() / name
    if not path.is_file():
        raise RulesConfigError(f"集成配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RulesConfigError(f"集成 YAML 根节点必须是对象: {path}")
    _CACHE[name] = data
    return data


# ── LLM Provider ────────────────────────────────────────────────

def get_llm_provider_presets() -> Dict[str, Dict[str, Any]]:
    """LLM Provider presets (base_url + model)"""
    return _load_yaml("llm.yaml").get("providers", {})


def get_provider_presets() -> Dict[str, Dict[str, Any]]:
    """get_llm_provider_presets 的别名（与 app/config.py 命名一致）"""
    return get_llm_provider_presets()


def get_default_provider() -> str:
    return _load_yaml("llm.yaml").get("defaults", {}).get("provider", "openai")


def get_default_timeout() -> int:
    return _load_yaml("llm.yaml").get("defaults", {}).get("timeout", 120)


def get_provider_config(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    获取 LLM Provider 配置:
    - base_url: .env > YAML preset
    - model:     .env > YAML preset
    - api_key:   始终从 .env
    """
    provider = provider or os.environ.get("LLM_PROVIDER", get_default_provider())
    presets = get_llm_provider_presets()
    preset = presets.get(provider, presets.get("custom", {}))

    return {
        "provider": provider,
        "api_key": os.environ.get("LLM_API_KEY"),
        "base_url": os.environ.get("LLM_BASE_URL") or preset.get("base_url"),
        "model": os.environ.get("LLM_MODEL_NAME") or preset.get("model"),
        "timeout": int(os.environ.get("LLM_TIMEOUT", str(get_default_timeout()))),
    }


# ── Embedding ────────────────────────────────────────────────────

def get_embedding_config() -> Dict[str, Any]:
    """Embedding 服务配置"""
    return _load_yaml("embedding.yaml").get("model", {})


def get_embedding_model_name() -> str:
    return get_embedding_config().get("name", "BAAI/bge-m3")


def get_embedding_dimension() -> int:
    return get_embedding_config().get("dimension", 1024)


# ── Agent ────────────────────────────────────────────────────────

def get_react_agent_config() -> Dict[str, Any]:
    return _load_yaml("agent.yaml").get("react_agent", {})


def get_langgraph_agent_config() -> Dict[str, Any]:
    return _load_yaml("agent.yaml").get("langgraph_agent", {})


def get_ethics_filter_config() -> Dict[str, Any]:
    return _load_yaml("agent.yaml").get("ethics_filter", {})


# ── 缓存清理 ─────────────────────────────────────────────────────

def reload() -> None:
    _CACHE.clear()
