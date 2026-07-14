"""
Workspace Branding 加载器 — 阶段二 2D

加载 workspace/{id}/branding/ 下的品牌定制文件：
- logo.svg / logo.png  → 报告 Logo
- style.css              → 报告样式覆盖
- accent_color           → 强调色（十六进制）

branding 文件为可选；workspace 可选择只覆盖部分。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .loader import resolve_workspace_dir


def _branding_dir() -> Optional[Path]:
    ws = resolve_workspace_dir()
    if ws is None:
        return None
    branding = ws / "branding"
    return branding if branding.is_dir() else None


def load_branding() -> Dict[str, Any]:
    """
    加载品牌配置。
    返回: {logo_path, style_path, accent_color, ...}
    若无 branding 目录，返回空字典（使用 repo 默认）。
    """
    branding_dir = _branding_dir()
    result: Dict[str, Any] = {}

    if branding_dir is None:
        return result

    # Logo 文件
    for ext in ("svg", "png", "jpg"):
        logo_path = branding_dir / f"logo.{ext}"
        if logo_path.is_file():
            result["logo_path"] = str(logo_path)
            break

    # Style 文件
    style_path = branding_dir / "style.css"
    if style_path.is_file():
        result["style_path"] = str(style_path)

    # brand.yaml 配置
    brand_path = branding_dir / "brand.yaml"
    if brand_path.is_file():
        with brand_path.open("r", encoding="utf-8") as f:
            brand_data = yaml.safe_load(f) or {}
        result.setdefault("accent_color", brand_data.get("accent_color"))
        result.setdefault("font_family", brand_data.get("font_family"))
        result.setdefault("company_name", brand_data.get("company_name"))

    return result


def has_branding() -> bool:
    """检查当前 workspace 是否有 branding 目录"""
    return _branding_dir() is not None
