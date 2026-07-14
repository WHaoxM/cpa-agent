"""
Workspace Seeds 加载器 — 阶段二 2D

加载 workspace/{id}/seeds/ 下的运营数据，与 repo 默认数据合并：
- skills.yaml  → 追加到技能词典（去重合并）
- jobs.yaml    → 追加到岗位包

seeds 文件为可选；workspace 可选择只提供部分覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .loader import resolve_workspace_dir, RulesConfigError

_EMPTY: Dict[str, Any] = {}


def _seeds_dir() -> Optional[Path]:
    ws = resolve_workspace_dir()
    if ws is None:
        return None
    seeds = ws / "seeds"
    return seeds if seeds.is_dir() else None


def load_seeds_skills() -> List[Dict[str, Any]]:
    """
    加载追加技能词条。
    返回: [{name, category, aliases?, tier?}, ...]
    若 workspace/seeds/skills.yaml 不存在，返回空列表。
    """
    seeds_dir = _seeds_dir()
    if seeds_dir is None:
        return []
    skills_path = seeds_dir / "skills.yaml"
    if not skills_path.is_file():
        return []
    with skills_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return []
    items = data.get("additional_skills", [])
    if not isinstance(items, list):
        return []
    return items


def load_seeds_jobs() -> List[Dict[str, Any]]:
    """
    加载追加岗位包。
    返回: [{job_id, position_name, required_skills?, ...}, ...]
    若 workspace/seeds/jobs.yaml 不存在，返回空列表。
    """
    seeds_dir = _seeds_dir()
    if seeds_dir is None:
        return []
    jobs_path = seeds_dir / "jobs.yaml"
    if not jobs_path.is_file():
        return []
    with jobs_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return []
    items = data.get("additional_jobs", [])
    if not isinstance(items, list):
        return []
    return items


def has_seeds() -> bool:
    """检查当前 workspace 是否有 seeds 目录"""
    return _seeds_dir() is not None
