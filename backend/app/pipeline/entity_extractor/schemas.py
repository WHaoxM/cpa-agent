"""
JSON Schema 定义 — extraction schemas

数据源: config/schemas/*.json (冻结契约)
向后兼容: EDUCATION_SCHEMA / PROJECT_SCHEMA / EXTRACTION_OUTPUT_SCHEMA 常量
冻结规则: 标记 frozen=true 的 schema 不可修改，只能新增字段。
"""

from __future__ import annotations

from typing import Any, Dict

from config.schema_loader import (
    get_education_schema,
    get_extraction_output_schema,
    get_project_schema,
)

# 从 JSON 文件加载（首次访问时缓存）
EDUCATION_SCHEMA: Dict[str, Any] = get_education_schema()
PROJECT_SCHEMA: Dict[str, Any] = get_project_schema()
EXTRACTION_OUTPUT_SCHEMA: Dict[str, Any] = get_extraction_output_schema()
