"""
Schema 加载器 — 阶段二 2C

加载 config/schemas/*.json，提供冻结契约访问。
消费者通过 SchemaLoader 读取，禁止直接 import JSON 文件路径。

冻结规则: 标记 frozen=true 的 schema 不可修改，只能新增字段。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .loader import _backend_root, RulesConfigError

_CACHE: Dict[str, Dict[str, Any]] = {}


def _schemas_dir() -> Path:
    return _backend_root() / "config" / "schemas"


def _load_schema(name: str) -> Dict[str, Any]:
    if name in _CACHE:
        return _CACHE[name]
    path = _schemas_dir() / name
    if not path.is_file():
        raise RulesConfigError(f"Schema 文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RulesConfigError(f"Schema JSON 根节点必须是对象: {path}")
    _CACHE[name] = data
    return data


def get_schema(schema_id: str) -> Dict[str, Any]:
    """按 schema ID 加载（如 'education', 'project', 'extraction_output'）"""
    # schema_id 对应文件名 education.json → schema_id="education"
    path = _schemas_dir()
    # 查找匹配的文件
    candidates = [
        f"{schema_id}.json",
        f"extraction_{schema_id}.json",
    ]
    for candidate in candidates:
        p = path / candidate
        if p.is_file():
            return _load_schema(candidate)
    # fallback: 直接使用 schema_id 作为文件名
    if "." not in schema_id:
        schema_file = f"{schema_id}.json"
    else:
        schema_file = schema_id
    return _load_schema(schema_file)


def get_education_schema() -> Dict[str, Any]:
    return get_schema("education")


def get_project_schema() -> Dict[str, Any]:
    return get_schema("project")


def get_extraction_output_schema() -> Dict[str, Any]:
    return get_schema("extraction_output")


def list_schemas() -> list[str]:
    """列出所有 schema 文件（不含 _meta）"""
    schemas_dir = _schemas_dir()
    if not schemas_dir.is_dir():
        return []
    return [p.name for p in schemas_dir.glob("*.json")]


def is_frozen(schema_id: str) -> bool:
    """检查 schema 是否已冻结"""
    schema = get_schema(schema_id)
    return schema.get("_meta", {}).get("frozen", False)


def reload() -> None:
    _CACHE.clear()
