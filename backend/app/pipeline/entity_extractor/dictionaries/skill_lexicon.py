"""
技能词典 — 6大类结构化组织

数据源: config/defaults/dictionaries/skill_lexicon.yaml
向后兼容: SKILL_CATEGORIES 常量 + get_all_skills/get_skills_by_category/get_category_for_skill 函数
"""

from __future__ import annotations

from typing import Dict, List

from config.dictionary_loader import get_skill_categories

# 从 YAML 加载（首次访问时缓存）
SKILL_CATEGORIES: Dict[str, List[str]] = get_skill_categories()


def get_all_skills() -> List[str]:
    """获取所有技能的扁平列表（去重）"""
    seen = set()
    result: List[str] = []
    for skills in SKILL_CATEGORIES.values():
        for s in skills:
            if s not in seen:
                seen.add(s)
                result.append(s)
    return result


def get_skills_by_category(category: str) -> List[str]:
    """按类别获取技能列表"""
    return SKILL_CATEGORIES.get(category, [])


def get_category_for_skill(skill: str) -> str:
    """查找技能所属类别"""
    skill_lower = skill.lower()
    for cat, skills in SKILL_CATEGORIES.items():
        if any(s.lower() == skill_lower for s in skills):
            return cat
    return "unknown"
