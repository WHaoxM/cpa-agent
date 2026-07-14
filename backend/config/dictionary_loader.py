"""
词典加载器 — 阶段二 2A

加载 config/defaults/dictionaries/*.yaml，提供向后兼容的 Python 模块常量。
正则模式（PII/DATE）保留在代码中，仅词表走 YAML。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from .loader import _backend_root, RulesConfigError

_CACHE: Dict[str, Any] = {}


def _dict_dir() -> Path:
    return _backend_root() / "config" / "defaults" / "dictionaries"


def _load_yaml(name: str) -> Dict[str, Any]:
    if name in _CACHE:
        return _CACHE[name]
    path = _dict_dir() / name
    if not path.is_file():
        raise RulesConfigError(f"词典文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RulesConfigError(f"词典 YAML 根节点必须是对象: {path}")
    _CACHE[name] = data
    return data


def get_skill_categories() -> Dict[str, List[str]]:
    """技能分类词表 (6大类)"""
    data = _load_yaml("skill_lexicon.yaml")
    return data.get("skill_categories", {})


def get_known_skills_flat() -> List[str]:
    """扁平化的已知技能列表 (跨类别合并, 去重保序) — 供 KB fallback 关键词匹配"""
    seen: set = set()
    flat: List[str] = []
    for skills in get_skill_categories().values():
        for s in skills:
            if s not in seen:
                seen.add(s)
                flat.append(s)
    return flat


def get_position_categories() -> Dict[str, List[str]]:
    """具体岗位名分类词表 — 供 KB fallback 关键词匹配"""
    data = _load_yaml("position_lexicon.yaml")
    return data.get("position_categories", {})


def get_position_names_flat() -> List[str]:
    """扁平化的具体岗位名列表 (跨类别合并, 去重保序)"""
    seen: set = set()
    flat: List[str] = []
    for positions in get_position_categories().values():
        for p in positions:
            if p not in seen:
                seen.add(p)
                flat.append(p)
    return flat


# ── SkillExtractor 专用词典 (skill_extractor_lexicon.yaml) ───────────
# 与 skill_lexicon.yaml 区分: 14 分类 + 别名 + 熟练度等级 + 上下文关键词
# 供 app/pipeline/entity_extractor/skill_extractor.py 加载

def get_skill_extractor_dict() -> Dict[str, Any]:
    """SkillExtractor 词典完整数据 (分类+别名+熟练度+上下文关键词)"""
    return _load_yaml("skill_extractor_lexicon.yaml")


def get_skill_extractor_skills() -> Dict[str, str]:
    """技能 → 分类映射 (扁平化为 {skill_name: category})"""
    result: Dict[str, str] = {}
    for category, skills in get_skill_extractor_dict().get("skill_categories", {}).items():
        for skill in skills:
            result[skill] = category
    return result


def get_skill_extractor_aliases() -> Dict[str, str]:
    """别名 → 标准名映射"""
    return get_skill_extractor_dict().get("aliases", {})


def get_skill_extractor_proficiency_levels() -> List[str]:
    """默认熟练度等级"""
    return get_skill_extractor_dict().get("default_proficiency_levels", ["了解", "熟悉", "掌握", "精通"])


def get_skill_extractor_context_keywords() -> Dict[str, List[str]]:
    """上下文关键词 (positive/negative)"""
    return get_skill_extractor_dict().get("context_keywords", {"positive": [], "negative": []})


# ── LLM 技能抽取辅助词典 (llm_skill_aux.yaml) ───────────────────────
# 供 app/pipeline/entity_extractor/llm_skill_extractor.py 加载
# 包含: 上下文负面词 / 软技能关键词 / 高置信度技能 / 等级置信度映射 / 软技能折扣 / 技能分类映射

def get_llm_skill_aux() -> Dict[str, Any]:
    """LLM 技能抽取辅助词典完整数据"""
    return _load_yaml("llm_skill_aux.yaml")


def get_tech_context_negative() -> List[str]:
    """技术上下文负面词 (幻觉检测用)"""
    return get_llm_skill_aux().get("tech_context_negative", [])


def get_soft_skill_keywords() -> List[str]:
    """软技能关键词 (hard/soft 判定用)"""
    return get_llm_skill_aux().get("soft_skill_keywords", [])


def get_high_confidence_skills() -> List[str]:
    """高置信度技能集合 (防止 LLM 幻觉: 仅这些技能允许 level=精通/掌握)"""
    return get_llm_skill_aux().get("high_confidence_skills", [])


def get_level_confidence_mapping() -> Dict[str, float]:
    """等级 → 置信度映射"""
    return get_llm_skill_aux().get("level_confidence_mapping", {})


def get_soft_skill_discount() -> float:
    """软技能折扣系数"""
    return float(get_llm_skill_aux().get("soft_skill_discount", 0.8))


def get_default_confidence() -> float:
    """默认置信度 (level 不在 mapping 中时使用)"""
    return float(get_llm_skill_aux().get("default_confidence", 0.5))


def get_llm_category_map() -> Dict[str, List[str]]:
    """LLM 抽取的简化技能分类映射 (category → [skill_names])"""
    return get_llm_skill_aux().get("category_map", {})


def get_llm_skill_to_category() -> Dict[str, str]:
    """反向映射: skill_name → category (扁平化)"""
    result: Dict[str, str] = {}
    for category, skills in get_llm_category_map().items():
        for skill in skills:
            result[skill] = category
    return result


# ── 实体规范库种子数据 (entity_seeds.yaml) ─────────────────────────
# 供 app/services/entity_normalizer.py:seed_canonical_entities 加载

def get_entity_seeds() -> Dict[str, Any]:
    """实体规范库种子数据完整数据"""
    return _load_yaml("entity_seeds.yaml")


def get_skill_category_map() -> Dict[str, str]:
    """skill_lexicon 英文分类 → 中文显示名映射"""
    return get_entity_seeds().get("skill_category_map", {})


def get_common_majors() -> List[str]:
    """常见专业列表"""
    return get_entity_seeds().get("common_majors", [])


def get_common_positions() -> List[str]:
    """常见职位列表"""
    return get_entity_seeds().get("common_positions", [])


def get_degree_seeds() -> Dict[str, List[str]]:
    """学历层次种子: {canonical_name: [aliases]}"""
    return get_entity_seeds().get("degree_seeds", {})


def get_cert_seeds() -> List[Dict[str, Any]]:
    """证书种子列表: [{canonical_name, category, aliases}]"""
    return get_entity_seeds().get("cert_seeds", [])


def get_degree_keywords() -> List[str]:
    return _load_yaml("entity_patterns.yaml").get("degree_keywords", [])


def get_role_keywords() -> List[str]:
    return _load_yaml("entity_patterns.yaml").get("role_keywords", [])


def get_company_suffixes() -> List[str]:
    return _load_yaml("entity_patterns.yaml").get("company_suffixes", [])


def get_ethics_sensitive_keywords() -> Dict[str, List[str]]:
    return _load_yaml("pii_patterns.yaml").get("ethics_sensitive_keywords", {})


def reload() -> None:
    """清空缓存，强制重新加载"""
    _CACHE.clear()
