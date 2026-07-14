"""
业务规则配置加载器 — 阶段一

加载顺序: config/defaults/rules/*.yaml → workspace/overrides/rules.yaml（白名单校验）
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

_RULES: Optional["RulesConfig"] = None


class RulesConfigError(Exception):
    """规则配置加载或校验失败"""


@dataclass(frozen=True)
class RulesConfig:
    """合并后的业务规则配置（只读视图）"""

    matching: Dict[str, Any]
    thresholds: Dict[str, Any]
    scoring_tables: Dict[str, Any]

    @property
    def dimension_weights(self) -> Dict[str, float]:
        return self.matching["dimension_weights"]

    @property
    def semantic_weight(self) -> float:
        return float(self.matching["skill_coverage_formula"]["semantic_weight"])

    @property
    def match_gates(self) -> Dict[str, float]:
        return self.matching["match_gates"]

    @property
    def alias_threshold(self) -> float:
        return float(self.thresholds["entity_normalizer"]["alias"])

    @property
    def related_threshold(self) -> float:
        return float(self.thresholds["entity_normalizer"]["related"])

    @property
    def semantic_cosine_threshold(self) -> float:
        return float(self.thresholds["semantic"]["cosine"])

    @property
    def competitiveness_levels(self) -> Dict[str, int]:
        return self.thresholds["portrait"]["competitiveness_levels"]

    @property
    def match_level_thresholds(self) -> Dict[str, int]:
        """match_level 阈值: {excellent, good, fair}"""
        return self.thresholds["match_level"]

    @property
    def d3_params(self) -> Dict[str, Any]:
        """D3 软技能评分参数 (matcher._calc_d3_detailed 使用)"""
        return self.thresholds["d3"]


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _repo_root() -> Path:
    backend = _backend_root()
    parent = backend.parent
    if (parent / "workspace").is_dir() or (parent / "CONTEXT.md").is_file():
        return parent
    return backend


def _defaults_rules_dir() -> Path:
    return _backend_root() / "config" / "defaults" / "rules"


def resolve_workspace_dir() -> Optional[Path]:
    explicit = os.environ.get("WORKSPACE_PATH", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise RulesConfigError(f"WORKSPACE_PATH 目录不存在: {path}")
        return path

    workspace_id = os.environ.get("WORKSPACE_ID", "default")
    path = _repo_root() / "workspace" / workspace_id
    return path if path.is_dir() else None


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise RulesConfigError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RulesConfigError(f"YAML 根节点必须是对象: {path}")
    return data


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _leaf_paths(data: Dict[str, Any], prefix: str = "") -> Set[str]:
    paths: Set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            paths.update(_leaf_paths(value, path))
        else:
            paths.add(path)
    return paths


def _is_path_allowed(path: str, allowed: List[str]) -> bool:
    return any(path == item or path.startswith(item + ".") for item in allowed)


def _validate_workspace_override(
    override: Dict[str, Any],
    allowed: List[str],
) -> None:
    if not override:
        return
    invalid = [
        p for p in sorted(_leaf_paths(override))
        if not _is_path_allowed(p, allowed)
    ]
    if invalid:
        raise RulesConfigError(
            "workspace overrides/rules.yaml 含未授权键: "
            + ", ".join(invalid)
            + f"；允许路径见 override_whitelist.yaml: {allowed}"
        )


def _load_defaults() -> Dict[str, Any]:
    rules_dir = _defaults_rules_dir()
    merged: Dict[str, Any] = {}
    for name in ("matching.yaml", "thresholds.yaml", "scoring_tables.yaml"):
        path = rules_dir / name
        section = _load_yaml(path)
        merged = _deep_merge(merged, section)
    return merged


def _build_rules_config() -> RulesConfig:
    defaults = _load_defaults()
    whitelist_path = _defaults_rules_dir() / "override_whitelist.yaml"
    whitelist = _load_yaml(whitelist_path)
    allowed = whitelist.get("allowed") or []
    if not isinstance(allowed, list):
        raise RulesConfigError("override_whitelist.yaml: allowed 必须是列表")

    workspace_dir = resolve_workspace_dir()
    override_path = (
        workspace_dir / "overrides" / "rules.yaml"
        if workspace_dir
        else None
    )
    override: Dict[str, Any] = {}
    if override_path and override_path.is_file():
        with override_path.open("r", encoding="utf-8") as f:
            raw_override = yaml.safe_load(f)
        # workspace override 允许为空（仅注释）→ 视作无覆盖
        if raw_override is None:
            override = {}
        elif isinstance(raw_override, dict):
            override = raw_override
        else:
            raise RulesConfigError(
                f"workspace overrides/rules.yaml 根节点必须是对象: {override_path}"
            )
        _validate_workspace_override(override, allowed)

    merged = _deep_merge(defaults, override)

    for section in ("matching", "thresholds", "scoring_tables"):
        if section not in merged:
            raise RulesConfigError(f"合并后缺少配置段: {section}")

    return RulesConfig(
        matching=merged["matching"],
        thresholds=merged["thresholds"],
        scoring_tables=merged["scoring_tables"],
    )


def init_rules_config(*, force: bool = False) -> RulesConfig:
    """启动时加载规则配置（失败则抛 RulesConfigError）"""
    global _RULES
    if _RULES is not None and not force:
        return _RULES
    _RULES = _build_rules_config()
    return _RULES


def get_rules_config() -> RulesConfig:
    if _RULES is None:
        return init_rules_config()
    return _RULES


def competitiveness_level(score: float, rules: Optional[RulesConfig] = None) -> str:
    levels = (rules or get_rules_config()).competitiveness_levels
    if score >= levels["extreme"]:
        return "极强"
    if score >= levels["strong"]:
        return "强"
    if score >= levels["medium"]:
        return "中等"
    if score >= levels["weak"]:
        return "偏弱"
    return "弱"
