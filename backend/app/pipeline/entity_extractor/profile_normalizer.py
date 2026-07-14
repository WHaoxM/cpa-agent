"""
Profile 全类型实体归一化网关

架构定位: L0 解析后、写入 MySQL 前，对 profile_json 中所有实体统一归一化
归一化只做一次，下游消费已归一化数据

支持的实体类型与字段映射:
  skill       → skills.programming_languages[].name, skills.frameworks[].name, ...
  certificate → certificates[].name
  award       → awards[].name, awards[].level
  major       → education[].major
  position    → candidate_profile.job_intent.target_positions[], work_experience[].position

归一化策略:
  L1: EntityNormalizer (精确→别名→BGE-M3语义→LLM OOV闭环)
  L2: 规则快路径降级 (EntityNormalizer 不可用时)
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger('cpa-agent.profile_normalizer')

# ── 归一化覆盖的实体类型 ──
ENTITY_TYPE_MAP = {
    # (json_path_prefix, entity_type, field_to_normalize)
    # skill 相关
    "skills.programming_languages": ("skill", "name"),
    "skills.frameworks": ("skill", "name"),
    "skills.databases": ("skill", "name"),
    "skills.tools": ("skill", "name"),
    "skills.ai_ml": ("skill", "name"),
    "skills.domain_knowledge": ("skill", "name"),
    # certificate
    "certificates": ("certificate", "name"),
    # award
    "awards": ("award", "name"),
    # major
    "education": ("major", "major"),
    # position
    "candidate_profile.job_intent.target_positions": ("position", None),
    "work_experience": ("position", "position"),
    "internship_experience": ("position", "position"),
}

def _apply_external_rule(value: str, rule_domain: str, normalizer) -> str:
    """外部规则表 normalization_rule 应用入口"""
    if not value or not normalizer:
        return value
    try:
        return normalizer.apply_external_rule(value, rule_domain)
    except Exception:
        return value


def _get_nested(data: Dict, path: str, default=None):
    """获取嵌套字典值, 支持 dot notation"""
    keys = path.split(".")
    current = data
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
        if current is None:
            return default
    return current


def _set_nested(data: Dict, path: str, value: Any):
    """设置嵌套字典值, 支持 dot notation"""
    keys = path.split(".")
    current = data
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


class ProfileNormalizer:
    """
    Profile 全类型实体归一化器

    调用时机: L0 解析后、写入 MySQL 前
    归一化策略: EntityNormalizer(语义+LLM) 优先, 规则降级
    """

    def __init__(self):
        self._normalizer = None

    @property
    def normalizer(self):
        if self._normalizer is None:
            try:
                from app.services.entity_normalizer import get_entity_normalizer
                self._normalizer = get_entity_normalizer()
            except Exception:
                self._normalizer = None
        return self._normalizer

    def _normalize_entity(self, name: str, entity_type: str, context: str = "") -> str:
        """
        归一化单个实体名 — EntityNormalizer 优先, 规则降级

        EntityNormalizer 内部流程:
          精确匹配 → 别名匹配 → BGE-M3语义匹配(cosine>0.85) → LLM OOV分类
        """
        if not name or not name.strip():
            return name

        # ── EntityNormalizer 语义+LLM 归一化 ──
        if self.normalizer:
            try:
                result = self.normalizer.normalize(
                    name, entity_type, context=context or None, auto_register=True
                )
                if result.match_type in ("exact", "alias", "semantic_alias"):
                    return result.canonical
                # related 级别: 保留原名但记录关联, 不改
            except Exception as e:
                logger.warning(f"EntityNormalizer 归一化失败 [{entity_type}/{name}]: {e}")

        # ── 规则降级 ──
        if entity_type == "award":
            # award 的 name 不做规则归一化, level 单独处理
            return name
        if entity_type == "major":
            return _apply_external_rule(name, "major_alias", self.normalizer)

        return name

    def normalize_profile(self, profile: Dict, raw_text: str = "") -> Dict:
        """
        归一化整个 profile_json — 遍历所有实体字段

        Args:
            profile: L0 解析输出的 profile_json
            raw_text: 原始简历文本 (作为 EntityNormalizer context)

        Returns:
            归一化后的 profile_json (原地修改 + 返回)
        """
        if not profile or not isinstance(profile, dict):
            return profile

        context = raw_text[:500] if raw_text else ""
        stats = {"normalized": 0, "unchanged": 0, "errors": 0}

        # ── 1. 遍历 ENTITY_TYPE_MAP 中定义的数组字段 ──
        for json_path, (entity_type, field_name) in ENTITY_TYPE_MAP.items():
            items = _get_nested(profile, json_path)
            if not items or not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                # 确定要归一化的字段
                if field_name is None:
                    # 列表元素本身就是字符串 (如 target_positions: ["Java后端"])
                    if isinstance(item, str):
                        normalized = self._normalize_entity(item, entity_type, context)
                        if normalized != item:
                            idx = items.index(item)
                            items[idx] = normalized
                            stats["normalized"] += 1
                        else:
                            stats["unchanged"] += 1
                    continue

                name = item.get(field_name, "")
                if not name:
                    continue

                normalized = self._normalize_entity(name, entity_type, context)
                if normalized != name:
                    item[f"{field_name}_original"] = name  # 保留原名
                    item[field_name] = normalized
                    stats["normalized"] += 1
                else:
                    stats["unchanged"] += 1

        # ── 2. award level 专项归一化 ──
        awards = profile.get("awards", [])
        for a in awards:
            if isinstance(a, dict):
                raw_level = a.get("level", "")
                if raw_level:
                    # EntityNormalizer 归一化 award level
                    normalized_level = self._normalize_entity(raw_level, "award", context)
                    # 如果 EntityNormalizer 返回的还是原始值, 用规则快路径
                    if normalized_level == raw_level:
                        normalized_level = _apply_external_rule(raw_level, "award_level", self.normalizer)
                    if normalized_level != raw_level:
                        a["level_original"] = raw_level
                        a["level"] = normalized_level
                        stats["normalized"] += 1

        # ── 3. soft_skills 归一化 ──
        soft_skills = profile.get("skills", {}).get("soft_skills", [])
        if isinstance(soft_skills, list):
            for i, ss in enumerate(soft_skills):
                name = ss if isinstance(ss, str) else (ss.get("name", "") if isinstance(ss, dict) else "")
                if name:
                    normalized = self._normalize_entity(name, "skill", context)
                    if normalized != name:
                        if isinstance(ss, dict):
                            ss["name_original"] = name
                            ss["name"] = normalized
                        else:
                            soft_skills[i] = normalized
                        stats["normalized"] += 1

        # ── 4. 写入归一化统计 ──
        if stats["normalized"] > 0:
            profile["_normalization_stats"] = stats
            logger.info(f"Profile 归一化完成: {stats}")

        return profile


# ── 便捷函数 ──

def normalize_profile(profile: Dict, raw_text: str = "") -> Dict:
    """便捷函数: 归一化 profile_json"""
    normalizer = ProfileNormalizer()
    return normalizer.normalize_profile(profile, raw_text)
