"""
PII 正则模式 + 伦理敏感词

覆盖契约 privacy_guard.pii_detection.entities 全部 7 类：
PHONE, EMAIL, ID_CARD, ADDRESS, PERSON_NAME, STUDENT_ID, COMPANY_INTERNAL_ID

伦理偏见过滤覆盖 ethics_bias_filter.mask_fields：
gender, age, ethnicity, hometown
"""

from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# PII 正则模式 — 契约 7 类
# ---------------------------------------------------------------------------

PII_PATTERNS: Dict[str, str] = {
    "PHONE": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "ID_CARD": (
        r"(?<!\d)"
        r"\d{6}"
        r"(?:19|20)\d{2}"
        r"(?:0[1-9]|1[0-2])"
        r"(?:0[1-9]|[12]\d|3[01])"
        r"\d{3}[\dXx]"
        r"(?!\d)"
    ),
    "ADDRESS": (
        r"(?:"
        r"[\u4e00-\u9fa5]{2,6}(?:省|自治区)"
        r"(?![\u4e00-\u9fa5]*(?:重点|建设|发展|级别|级|属|厅|委|办))"
        r"[\u4e00-\u9fa5]{2,10}(?:市|区|县|镇|乡|街道|路|号|弄|室|栋|幢|楼)"
        r"[\u4e00-\u9fa5\d\-]*"
        r")"
    ),
    "PERSON_NAME": (
        r"(?:姓\s*名\s*[:：]\s*)"
        r"([\u4e00-\u9fa5]{2,4})"
    ),
    "STUDENT_ID": r"(?<![A-Za-z\d])[A-Z]{1,3}\d{6,12}(?![A-Za-z\d])",
    "COMPANY_INTERNAL_ID": r"(?:EMP|STF|ID)[\-_]?\d{4,10}",
}

# ---------------------------------------------------------------------------
# 伦理敏感词 — ethics_bias_filter.mask_fields
# ---------------------------------------------------------------------------

ETHICS_SENSITIVE_KEYWORDS: Dict[str, List[str]] = {
    "gender": [
        "男", "女", "性别", "先生", "女士", "male", "female", "gender",
    ],
    "age": [
        "岁", "年龄", "出生日期", "生日", "出生年月",
        "age", "birthday", "date of birth",
    ],
    "ethnicity": [
        "民族", "族", "汉族", "回族", "藏族", "维吾尔",
        "ethnicity", "race",
    ],
    "hometown": [
        "籍贯", "户籍", "户口", "家乡", "出生地",
        "hometown", "birthplace", "native place",
    ],
}


def get_all_ethics_keywords() -> List[str]:
    """获取所有伦理敏感词的扁平列表"""
    result: List[str] = []
    for keywords in ETHICS_SENSITIVE_KEYWORDS.values():
        result.extend(keywords)
    return result
