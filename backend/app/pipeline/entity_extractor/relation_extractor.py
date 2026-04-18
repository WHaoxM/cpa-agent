"""
关系抽取模块 (Relation Extraction)
最小化实现: 基于语法模式和共现统计算法
"""

import re
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class ExtractedRelation:
    source: str
    target: str
    relation_type: str
    confidence: float
    evidence: str


class RelationPatterns:
    """关系模式库"""

    PREREQUISITE_PATTERNS = [
        (r"(\w+)\s*(?:是|属于)\s*(\w+)\s*的基础", "prerequisite"),
        (r"(\w+)\s*基于\s*(\w+)", "prerequisite"),
        (r"(\w+)\s*需要\s*(\w+)", "prerequisite"),
        (r"先学\s*(\w+)\s*再学\s*(\w+)", "prerequisite"),
        (r"(\w+)\s*是\s*(\w+)\s*的前提", "prerequisite"),
        (r"学会\s*(\w+)\s*才能学\s*(\w+)", "prerequisite"),
        (r"(\w+)\s*依赖\s*(\w+)", "prerequisite"),
        (r"(\w+)\s*是\s*(\w+)\s*的基础", "prerequisite"),
    ]

    RELATED_PATTERNS = [
        (r"(\w+)\s*和\s*(\w+)\s*类似", "related"),
        (r"(\w+)\s*与\s*(\w+)\s*都是", "related"),
        (r"类似\s*(\w+)\s*的\s*(\w+)", "related"),
        (r"(\w+)\s*或者\s*(\w+)", "alternative"),
        (r"(\w+)\s*优先于\s*(\w+)", "alternative"),
    ]

    COMPOSED_PATTERNS = [
        (r"(\w+)\s*\+\s*(\w+)", "composed"),
        (r"(\w+)\s*\/\s*(\w+)", "composed"),
        (r"(\w+)\s*~\s*(\w+)", "composed"),
    ]


class SkillRelationExtractor:
    """技能关系抽取器"""

    SKILL_SIMILARITY = {
        ("JavaScript", "TypeScript"): 0.85,
        ("Python", "Java"): 0.4,
        ("Python", "Go"): 0.5,
        ("Vue.js", "React"): 0.75,
        ("Django", "Flask"): 0.7,
        ("MySQL", "PostgreSQL"): 0.8,
        ("MongoDB", "MySQL"): 0.5,
        ("Redis", "Memcached"): 0.7,
        ("Docker", "Kubernetes"): 0.6,
        ("TensorFlow", "PyTorch"): 0.75,
        ("Java", "C++"): 0.6,
        ("Java", "C#"): 0.7,
    }

    PREREQUISITE_GRAPH = {
        "Python": ["Django", "Flask", "Scrapy"],
        "Java": ["Spring", "Spring Boot", "Hibernate"],
        "JavaScript": ["Vue.js", "React", "Angular", "Node.js"],
        "HTML": ["CSS", "JavaScript"],
        "CSS": ["Sass", "Less"],
        "SQL": ["MySQL", "PostgreSQL", "Oracle"],
        "Linux": ["Shell", "Bash"],
        "Git": ["GitHub", "GitLab"],
    }

    def __init__(self):
        self.patterns = RelationPatterns()

    def extract_relations(
        self,
        skills: List[str],
        text: str = "",
        cooccurrence_matrix: Dict = None
    ) -> List[ExtractedRelation]:
        """
        从技能列表和文本中抽取关系

        Args:
            skills: 技能列表
            skills_set: 技能集合
            text: 原始文本(可选)
            cooccurrence_matrix: 共现矩阵(可选)

        Returns:
            关系列表
        """
        relations = []
        skills_set = set(skills)

        for prereq_skill, dependent_skills in self.PREREQUISITE_GRAPH.items():
            if prereq_skill in skills_set:
                for dep_skill in dependent_skills:
                    if dep_skill in skills_set:
                        relations.append(ExtractedRelation(
                            source=dep_skill,
                            target=prereq_skill,
                            relation_type="prerequisite",
                            confidence=0.8,
                            evidence=f"{dep_skill} requires {prereq_skill}"
                        ))

        relations.extend(self._extract_from_text(skills_set, text))

        if cooccurrence_matrix:
            relations.extend(self._extract_from_cooccurrence(skills_set, cooccurrence_matrix))

        relations = self._deduplicate_relations(relations)

        relations.sort(key=lambda x: x.confidence, reverse=True)

        return relations

    def _extract_from_text(
        self,
        skills_set: Set[str],
        text: str
    ) -> List[ExtractedRelation]:
        """从文本中抽取关系"""
        relations = []

        if not text:
            return relations

        text_lower = text.lower()

        for skill1, skill2 in self.SKILL_SIMILARITY.keys():
            if skill1 in skills_set and skill2 in skills_set:
                confidence = self.SKILL_SIMILARITY[(skill1, skill2)]
                relations.append(ExtractedRelation(
                    source=skill1,
                    target=skill2,
                    relation_type="similar",
                    confidence=confidence,
                    evidence="known_similarity"
                ))

        for pattern, rel_type in self.patterns.PREREQUISITE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                skill1, skill2 = match.group(1), match.group(2)
                if skill1 in skills_set and skill2 in skills_set:
                    relations.append(ExtractedRelation(
                        source=skill1,
                        target=skill2,
                        relation_type=rel_type,
                        confidence=0.75,
                        evidence=match.group(0)
                    ))

        return relations

    def _extract_from_cooccurrence(
        self,
        skills_set: Set[str],
        cooccurrence_matrix: Dict
    ) -> List[ExtractedRelation]:
        """从共现矩阵抽取关系"""
        relations = []

        for (skill1, skill2), count in cooccurrence_matrix.items():
            if skill1 in skills_set and skill2 in skills_set and count >= 2:
                confidence = min(0.5 + count * 0.1, 0.9)
                relations.append(ExtractedRelation(
                    source=skill1,
                    target=skill2,
                    relation_type="co_occurrence",
                    confidence=confidence,
                    evidence=f"co_occurred_{count}_times"
                ))

        return relations

    def _deduplicate_relations(
        self,
        relations: List[ExtractedRelation]
    ) -> List[ExtractedRelation]:
        """去重"""
        seen = set()
        unique = []

        for rel in relations:
            key = (rel.source, rel.target, rel.relation_type)
            if key not in seen:
                seen.add(key)
                unique.append(rel)

        return unique

    def build_cooccurrence_matrix(
        self,
        texts: List[str],
        skills: List[str]
    ) -> Dict[Tuple[str, str], int]:
        """
        从多个文本构建共现矩阵

        Args:
            texts: 文本列表(简历、JD等)
            skills: 技能列表

        Returns:
            共现矩阵 {(skill1, skill2): count}
        """
        matrix = defaultdict(int)
        skills_set = set(skills)

        for text in texts:
            text_skills = set()
            for skill in skills:
                if skill.lower() in text.lower():
                    text_skills.add(skill)

            for s1 in text_skills:
                for s2 in text_skills:
                    if s1 != s2:
                        key = tuple(sorted([s1, s2]))
                        matrix[key] += 1

        return dict(matrix)


def extract_relations(skills: List[str], text: str = "") -> List[Dict]:
    """
    便捷函数: 抽取技能关系

    Args:
        skills: 技能列表
        text: 原始文本

    Returns:
        关系列表
    """
    extractor = SkillRelationExtractor()
    relations = extractor.extract_relations(skills, text)

    return [
        {
            "source": r.source,
            "target": r.target,
            "type": r.relation_type,
            "confidence": r.confidence,
        }
        for r in relations
    ]


if __name__ == "__main__":
    skills = [
        "Python", "Django", "Flask", "MySQL", "Redis",
        "Vue.js", "JavaScript", "HTML", "CSS", "Docker"
    ]

    test_text = """
    Python 后端开发:
    - 熟练掌握 Python、Django、Flask
    - 熟悉 MySQL、Redis 数据库
    - 了解 Docker 容器化

    前端技能:
    - 掌握 Vue.js、JavaScript、HTML、CSS
    - 有 React 开发经验优先
    """

    relations = extract_relations(skills, test_text)

    print("=" * 50)
    print("关系抽取结果")
    print("=" * 50)

    for rel in relations:
        print(f"\n[{rel['type']}] {rel['source']} → {rel['target']}")
        print(f"  置信度: {rel['confidence']}")
