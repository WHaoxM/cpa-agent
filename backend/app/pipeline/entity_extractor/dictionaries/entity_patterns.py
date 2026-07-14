"""
实体规则词表 — 学位/角色/公司后缀关键词

数据源: config/defaults/dictionaries/entity_patterns.yaml
正则模式 DATE_PATTERNS 保留在代码中 (ADR-0001: 算法型逻辑不做 YAML 化)
"""

from __future__ import annotations

import re
from typing import List, Pattern

from config.dictionary_loader import (
    get_company_suffixes,
    get_degree_keywords,
    get_role_keywords,
)

# 从 YAML 加载词表
DEGREE_KEYWORDS: List[str] = get_degree_keywords()
ROLE_KEYWORDS: List[str] = get_role_keywords()
COMPANY_SUFFIXES: List[str] = get_company_suffixes()

# 正则模式保留在代码中 (ADR-0001: 算法型逻辑不做 YAML 化)
DATE_PATTERNS: List[Pattern[str]] = [
    re.compile(r'(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?'),
    re.compile(r'(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月]?'),
    re.compile(r'(\d{4})\s*[-~至到]\s*(\d{4}|至今|现在|present|now)', re.IGNORECASE),
    re.compile(r'(至今|现在|present|now)', re.IGNORECASE),
]
