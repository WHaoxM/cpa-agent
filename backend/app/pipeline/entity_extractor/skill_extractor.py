"""
技能抽取模块 (Skill Extraction)
最小化实现: 预定义词典 + 正则匹配 + 上下文验证

词典数据已外置到 config/defaults/dictionaries/skill_extractor_lexicon.yaml
通过 config.dictionary_loader 加载, 本模块只保留算法逻辑
"""

import re
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass

from config.dictionary_loader import (
    get_skill_extractor_skills,
    get_skill_extractor_aliases,
    get_skill_extractor_proficiency_levels,
    get_skill_extractor_context_keywords,
)


@dataclass
class ExtractedSkill:
    name: str
    category: str
    confidence: float
    source_text: str
    position: int


class SkillDictionary:
    """技能词典 - 数据走 YAML 加载 (config/defaults/dictionaries/skill_extractor_lexicon.yaml)"""

    # 加载时填充 (属性名保留以兼容外部引用)
    SKILLS: Dict[str, str] = get_skill_extractor_skills()
    ALIASES: Dict[str, str] = get_skill_extractor_aliases()
    DEFAULT_PROFICIENCY_LEVELS: List[str] = get_skill_extractor_proficiency_levels()

    def get_skills_by_category(self, category: str) -> list:
        """返回某分类下的所有技能名"""
        return [name for name, cat in self.SKILLS.items() if cat == category]

    def get_aliases(self, skill_name: str) -> list:
        """返回该技能的所有别名"""
        return [alias for alias, canonical in self.ALIASES.items() if canonical == skill_name]

    def get_subcategory(self, skill_name: str) -> str:
        """获取子分类 (词典暂无子分类数据, 返回默认)"""
        return self.SKILLS.get(skill_name, "其他")

    def get_proficiency_levels(self, skill_name: str) -> list:
        """获取熟练度等级 (默认四级)"""
        return list(self.DEFAULT_PROFICIENCY_LEVELS)


class SkillExtractor:
    """技能抽取器"""

    def __init__(self):
        self.dictionary = SkillDictionary()
        self._build_patterns()

    def _build_patterns(self):
        """构建匹配模式"""
        all_skills = list(self.dictionary.SKILLS.keys()) + list(self.dictionary.ALIASES.keys())
        sorted_skills = sorted(all_skills, key=len, reverse=True)

        skill_pattern = "|".join(re.escape(s) for s in sorted_skills)
        self.skill_regex = re.compile(skill_pattern, re.IGNORECASE)

        # 上下文关键词走 YAML (config/defaults/dictionaries/skill_extractor_lexicon.yaml)
        # 注: 用 in 测试而非 regex, 不需要 re.escape
        ctx = get_skill_extractor_context_keywords()
        self.context_negative = list(ctx.get("negative", []))
        self.context_positive = list(ctx.get("positive", []))

    def extract(self, text: str) -> List[ExtractedSkill]:
        """从文本中抽取技能"""
        if not text:
            return []

        skills = []
        seen = set()

        for match in self.skill_regex.finditer(text):
            skill_raw = match.group()
            skill_name = self.dictionary.ALIASES.get(skill_raw, skill_raw)

            if skill_name in seen:
                continue
            seen.add(skill_name)

            confidence = self._calculate_confidence(text, match.start(), skill_name)

            category = self._get_category(skill_name)

            skills.append(ExtractedSkill(
                name=skill_name,
                category=category,
                confidence=confidence,
                source_text=self._get_context(text, match.start(), 50),
                position=match.start()
            ))

        skills.sort(key=lambda x: x.confidence, reverse=True)
        return skills

    def _calculate_confidence(self, text: str, position: int, skill_name: str) -> float:
        """计算技能提及置信度"""
        base_confidence = 0.5

        context_window = text[max(0, position-100):position+100].lower()

        positive_count = sum(1 for p in self.context_positive if p in context_window)
        negative_count = sum(1 for p in self.context_negative if p in context_window)

        confidence = base_confidence + (positive_count * 0.15) - (negative_count * 0.1)
        confidence = max(0.3, min(0.95, confidence))

        if skill_name in self.dictionary.SKILLS:
            confidence += 0.1

        return round(confidence, 2)

    def _get_category(self, skill_name: str) -> str:
        """获取技能类别"""
        return self.dictionary.SKILLS.get(skill_name, "其他")

    def _get_context(self, text: str, position: int, window: int) -> str:
        """获取技能出现的上下文"""
        start = max(0, position - window)
        end = min(len(text), position + window)
        return text[start:end].strip()


def extract_skills(text: str) -> List[Dict]:
    """
    便捷函数: 从文本中抽取技能

    Args:
        text: 输入文本(简历、JD等)

    Returns:
        技能列表,每项包含 name, category, confidence
    """
    extractor = SkillExtractor()
    skills = extractor.extract(text)

    return [
        {
            "name": s.name,
            "category": s.category,
            "confidence": s.confidence,
        }
        for s in skills
    ]


def extract_skills_with_context(text: str) -> List[Dict]:
    """抽取技能并返回上下文"""
    extractor = SkillExtractor()
    skills = extractor.extract(text)

    return [
        {
            "name": s.name,
            "category": s.category,
            "confidence": s.confidence,
            "context": s.source_text,
        }
        for s in skills
    ]


if __name__ == "__main__":
    test_text = """
    Python 后端开发工程师

    技能要求:
    - 熟练掌握 Python、Django、Flask
    - 熟悉 MySQL、Redis 数据库
    - 了解 Docker、Kubernetes
    - 3年以上后端开发经验

    加分项:
    - 有 Vue.js 或 React 开发经验
    - 熟悉 Git、Jenkins
    - 了解大数据技术如 Hadoop、Spark
    """

    skills = extract_skills_with_context(test_text)

    print("=" * 50)
    print("技能抽取结果")
    print("=" * 50)

    for skill in skills:
        print(f"\n[{skill['category']}] {skill['name']} (置信度: {skill['confidence']})")
        print(f"  上下文: ...{skill['context']}...")
