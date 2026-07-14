"""
PII 与伦理敏感词表

数据源: config/defaults/dictionaries/pii_patterns.yaml (ETHICS_SENSITIVE_KEYWORDS)
正则模式 PII_PATTERNS 保留在代码中 (ADR-0001: 安全正则不做 YAML 化)
"""

from __future__ import annotations

import re
from typing import Dict, List, Pattern

from config.dictionary_loader import get_ethics_sensitive_keywords

# 从 YAML 加载伦理敏感词表
ETHICS_SENSITIVE_KEYWORDS: Dict[str, List[str]] = get_ethics_sensitive_keywords()

# PII 正则模式保留在代码中 (ADR-0001: 安全正则不做 YAML 化)
PII_PATTERNS: Dict[str, Pattern[str]] = {
    'phone': re.compile(r'1[3-9]\d{9}'),
    'email': re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
    'id_card': re.compile(r'\d{17}[\dXx]'),
    'bank_card': re.compile(r'\d{16,19}'),
    'passport': re.compile(r'[A-Z]\d{8}'),
    'qq': re.compile(r'[1-9]\d{4,10}'),
    'wechat': re.compile(r'[a-zA-Z][-_a-zA-Z0-9]{5,19}'),
}
