"""
LLM 增强版技能抽取器
架构原则：LLM 为主，规则防幻觉为辅
软技能和硬技能都会抽取并分类存储，供 Agent 验证

辅助数据走 YAML (config/defaults/dictionaries/llm_skill_aux.yaml)
通过 config.dictionary_loader 加载
"""

import re
from typing import List, Dict, Set

from config.prompt_loader import load_prompt
from config.dictionary_loader import (
    get_tech_context_negative,
    get_soft_skill_keywords,
    get_high_confidence_skills,
    get_level_confidence_mapping,
    get_soft_skill_discount,
    get_default_confidence,
    get_llm_skill_to_category,
)


# 辅助数据从 YAML 加载 (保留模块级常量名以兼容外部引用)
TECH_CONTEXT_NEGATIVE: List[str] = get_tech_context_negative()
SOFT_SKILL_KEYWORDS: Set[str] = set(get_soft_skill_keywords())


class LLMSkillExtractor:
    """LLM 驱动的技能抽取器"""

    def __init__(self, use_normalizer: bool = True):
        self._llm_client = None
        self._normalizer = None
        self._use_normalizer = use_normalizer

    @property
    def llm_client(self):
        if self._llm_client is None:
            from app.utils.llm_client import get_llm_client
            self._llm_client = get_llm_client()
        return self._llm_client

    @property
    def normalizer(self):
        if self._normalizer is None and self._use_normalizer:
            try:
                from app.services.entity_normalizer import get_entity_normalizer
                self._normalizer = get_entity_normalizer()
            except Exception:
                self._normalizer = None
        return self._normalizer

    def extract(self, text: str, use_llm: bool = True) -> List[Dict]:
        if not text or not text.strip():
            return []

        if use_llm:
            skills = self._extract_with_llm(text)
            if skills:
                return skills

        return self._rule_based_fallback(text)

    def _extract_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取技能"""
        try:
            prompt = load_prompt("entity.skill_extractor.user", text=text[:3000])

            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500
            )

            content = str(response).strip()

            import re
            json_match = re.search(r'\{.*\}|\[.*\]', content, re.DOTALL)
            if not json_match:
                return []

            import json
            data = json.loads(json_match.group())

            skills_raw = data.get("skills", []) if isinstance(data, dict) else data
            if not isinstance(skills_raw, list):
                return []

            skills = []
            for item in skills_raw:
                if isinstance(item, dict):
                    name = item.get("name", "").strip()
                else:
                    name = str(item).strip()

                if not name or len(name) < 2:
                    continue

                skill_type = self._detect_skill_type(name)

                if skill_type == "hard" and self._is_hallucination(name, item if isinstance(item, dict) else {}):
                    continue

                if isinstance(item, dict):
                    skills.append({
                        "name": name,
                        "level": item.get("level", "熟悉"),
                        "category": item.get("category", self._categorize(name)),
                        "skill_type": skill_type,
                        "confidence": self._calc_confidence(item.get("level", "熟悉"), skill_type),
                        "source": "llm"
                    })
                else:
                    skills.append({
                        "name": name,
                        "level": "熟悉",
                        "category": self._categorize(name),
                        "skill_type": skill_type,
                        "confidence": 0.6 if skill_type == "hard" else 0.5,
                        "source": "llm"
                    })

            # ── 归一化: LLM 抽取结果 → canonical 规范名 ──
            if self.normalizer:
                skills = self.normalizer.normalize_batch(skills, "skill", text[:500])
                for s in skills:
                    if s.get("is_oov"):
                        s["source"] = "llm+oov"
                    else:
                        s["source"] = "llm+normalized"

            return self._deduplicate_skills(skills)

        except Exception as e:
            import logging
            logging.getLogger('cpa-agent.llm_skill_extractor').warning(f"LLM抽取失败: {e}")
            return []

    def _detect_skill_type(self, name: str) -> str:
        """检测技能类型：hard(硬技能) 或 soft(软技能)"""
        name_lower = name.lower()
        for keyword in SOFT_SKILL_KEYWORDS:
            if keyword.lower() in name_lower:
                return "soft"
        return "hard"

    def _is_hallucination(self, name: str, item: Dict) -> bool:
        """基于上下文判断是否为幻觉"""
        level = item.get("level", "")

        if level in ["精通", "掌握"] and name not in self._get_high_confidence_skills():
            context = str(item)
            for neg in TECH_CONTEXT_NEGATIVE:
                if neg.lower() in context.lower():
                    return True

        if len(name) > 30:
            return True

        if re.match(r'^[\d\W]+$', name):
            return True

        return False

    def _get_high_confidence_skills(self) -> Set[str]:
        """高置信度技能集合 (走 YAML, 防止 LLM 幻觉: 仅这些技能允许 level=精通/掌握)"""
        return set(get_high_confidence_skills())

    def _calc_confidence(self, level: str, skill_type: str = "hard") -> float:
        """根据掌握程度和技能类型计算置信度 (映射表 + 折扣系数走 YAML)"""
        mapping = get_level_confidence_mapping()
        base = mapping.get(level, get_default_confidence())
        if skill_type == "soft":
            return base * get_soft_skill_discount()
        return base

    def _categorize(self, skill_name: str) -> str:
        """对技能进行分类 (反向映射走 YAML: skill_name → category)"""
        skill_to_cat = get_llm_skill_to_category()
        return skill_to_cat.get(skill_name, "其他")

    def _rule_based_fallback(self, text: str) -> List[Dict]:
        """基于规则的降级方案（仅在LLM不可用时使用）"""
        from .skill_extractor import extract_skills as dict_extract
        skills = dict_extract(text)
        for s in skills:
            s["source"] = "dict"
        return skills

    def _deduplicate_skills(self, skills: List[Dict]) -> List[Dict]:
        """去重并合并技能"""
        seen: Dict[str, Dict] = {}
        for skill in skills:
            name = skill["name"]
            if name not in seen:
                seen[name] = skill
            else:
                if skill.get("confidence", 0) > seen[name].get("confidence", 0):
                    seen[name] = skill

        result = list(seen.values())
        result.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return result


def extract_skills(text: str, use_llm: bool = True) -> List[Dict]:
    """便捷函数：抽取技能"""
    extractor = LLMSkillExtractor()
    return extractor.extract(text, use_llm=use_llm)


def extract_skills_with_relations(text: str, use_llm: bool = True) -> Dict:
    """抽取技能和关系（关系基于共现统计）"""
    skills = extract_skills(text, use_llm=use_llm)
    skill_names = [s["name"] for s in skills]
    relations = _extract_cooccurrence_relations(skill_names, text)

    return {
        "skills": skills,
        "relations": relations
    }


def _extract_cooccurrence_relations(skill_names: List[str], text: str) -> List[Dict]:
    """基于共现统计的简单关系抽取"""
    if len(skill_names) < 2:
        return []

    lines = text.split('\n')
    relations = []
    seen = set()

    for line in lines:
        line_lower = line.lower()
        skills_in_line = [s for s in skill_names if s.lower() in line_lower]

        if len(skills_in_line) >= 2:
            for i in range(len(skills_in_line)):
                for j in range(i + 1, len(skills_in_line)):
                    pair = tuple(sorted([skills_in_line[i], skills_in_line[j]]))
                    if pair not in seen:
                        seen.add(pair)
                        relations.append({
                            "source": skills_in_line[i],
                            "target": skills_in_line[j],
                            "type": "co_occurrence",
                            "confidence": 0.6
                        })

    return relations


if __name__ == "__main__":
    test_text = """
    Python后端开发工程师 | 5年经验

    熟练掌握：
    - Python, Django, Flask, FastAPI
    - MySQL, PostgreSQL, Redis 数据库
    - Docker, Kubernetes 容器化
    - Git, Jenkins CI/CD

    了解：
    - Vue.js 前端开发
    - TensorFlow, PyTorch 机器学习
    - AWS 阿里云云服务

    加分项：
    - 有微服务架构经验
    - 熟悉分布式系统设计
    - 具有良好的沟通能力和团队合作精神
    - 具备项目管理经验，负责过多个跨团队项目
    """

    print("=" * 60)
    print("LLM 增强技能抽取测试")
    print("=" * 60)

    print("\n[1] LLM 抽取:")
    result = extract_skills_with_relations(test_text, use_llm=True)
    for s in result["skills"]:
        skill_type = s.get('skill_type', 'unknown')
        print(f"  [{skill_type}] {s['name']} ({s.get('category', '其他')}) - {s.get('level', '熟悉')} - 置信度: {s.get('confidence', 0):.2f}")

    hard_skills = [s for s in result["skills"] if s.get("skill_type") == "hard"]
    soft_skills = [s for s in result["skills"] if s.get("skill_type") == "soft"]
    print(f"\n硬技能: {len(hard_skills)} 个")
    print(f"软技能: {len(soft_skills)} 个")

    print("\n[2] 关系:")
    for r in result["relations"]:
        print(f"  {r['source']} --({r['type']})--> {r['target']}")
