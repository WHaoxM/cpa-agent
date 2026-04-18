"""
LLM 增强版技能抽取器
结合词典匹配 + DeepSeek LLM 补充
"""

import json
from typing import List, Dict, Optional
from .skill_extractor import SkillExtractor, extract_skills as dict_extract
from .relation_extractor import extract_relations
from app.utils.llm_client import get_llm_client
from app.utils.logger import get_logger

logger = get_logger('cpa-agent.llm_skill_extractor')


SKILL_EXTRACTION_PROMPT = """你是一个专业的技能抽取助手。请从以下文本中抽取所有IT技术技能。

文本可能来自简历或职位描述。请以JSON格式返回抽取到的技能列表。

技能类型包括但不限于：
- 编程语言：Python, Java, JavaScript, Go等
- 框架：Django, React, Vue.js, Spring Boot等
- 数据库：MySQL, PostgreSQL, MongoDB, Redis等
- 工具：Git, Docker, Kubernetes, Jenkins等
- 云服务：AWS, 阿里云, 腾讯云等
- AI/ML：TensorFlow, PyTorch, 机器学习, 深度学习等

注意：
1. 只返回技术技能，不要返回软技能（如"沟通能力"、"团队合作"）
2. 对于缩写如"JS"要展开为"JavaScript"，"K8S"展开为"Kubernetes"
3. 如果文本中没有技能，返回空列表

请以JSON格式返回，格式如下：
{{"skills": ["技能1", "技能2", ...], "reasoning": "简要说明"}}

文本：
{text}
"""


class LLMSkillExtractor:
    """LLM 增强的技能抽取器"""

    def __init__(self, use_llm_fallback: bool = True):
        self.dict_extractor = SkillExtractor()
        self.use_llm_fallback = use_llm_fallback
        self._llm_client = None

    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_llm_client()
        return self._llm_client

    def extract(
        self,
        text: str,
        use_llm: bool = False
    ) -> List[Dict]:
        """
        抽取技能

        Args:
            text: 输入文本
            use_llm: 是否使用 LLM 补充（默认 True）

        Returns:
            技能列表
        """
        dict_skills = dict_extract(text)

        if not use_llm or not self.use_llm_fallback:
            return dict_skills

        llm_skills = self._extract_with_llm(text)

        merged = self._merge_skills(dict_skills, llm_skills)

        return merged

    def _extract_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 抽取技能"""
        try:
            messages = [
                {"role": "user", "content": SKILL_EXTRACTION_PROMPT.format(text=text)}
            ]

            response = self.llm_client.chat_json(
                messages=messages,
                temperature=0.1,
                max_tokens=1024
            )

            llm_skills_raw = response.get("skills", [])

            llm_skills = []
            for skill in llm_skills_raw:
                if isinstance(skill, str):
                    llm_skills.append({
                        "name": skill,
                        "category": self._categorize_skill(skill),
                        "confidence": 0.75,
                        "source": "llm"
                    })

            logger.info(f"LLM 抽取到 {len(llm_skills)} 个技能")
            return llm_skills

        except Exception as e:
            logger.warning(f"LLM 技能抽取失败: {e}，使用词典匹配结果")
            return []

    def _merge_skills(
        self,
        dict_skills: List[Dict],
        llm_skills: List[Dict]
    ) -> List[Dict]:
        """合并词典和 LLM 抽取的技能"""
        seen = set()
        merged = []

        for skill in dict_skills:
            seen.add(skill["name"])
            skill["source"] = "dict"
            merged.append(skill)

        for skill in llm_skills:
            if skill["name"] not in seen:
                seen.add(skill["name"])
                skill["source"] = "llm"
                merged.append(skill)

        return merged

    def _categorize_skill(self, skill_name: str) -> str:
        """对技能进行分类"""
        categories = {
            "编程语言": ["Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#", "PHP", "Ruby", "Swift", "Kotlin", "Shell", "SQL"],
            "前端框架": ["React", "Vue", "Angular", "Svelte", "jQuery", "Bootstrap", "Tailwind", "Element UI", "Ant Design"],
            "后端框架": ["Django", "Flask", "FastAPI", "Spring", "Spring Boot", "Express", "NestJS", "Koa", "Laravel"],
            "数据库": ["MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "Oracle", "SQLite", "Cassandra"],
            "DevOps": ["Docker", "Kubernetes", "Jenkins", "GitLab CI", "Ansible", "Terraform", "Prometheus", "Grafana"],
            "AI/ML": ["TensorFlow", "PyTorch", "Keras", "Scikit-learn", "Pandas", "OpenCV", "Hugging Face", "LangChain"],
            "大数据": ["Hadoop", "Spark", "Flink", "Kafka", "Hive", "HBase"],
            "云计算": ["AWS", "Azure", "GCP", "阿里云", "腾讯云", "华为云"],
        }

        for category, skills in categories.items():
            if skill_name in skills:
                return category

        return "其他"


def extract_skills(text: str, use_llm: bool = True) -> List[Dict]:
    """
    便捷函数：抽取技能（词典 + LLM）

    Args:
        text: 输入文本
        use_llm: 是否使用 LLM

    Returns:
        技能列表
    """
    extractor = LLMSkillExtractor()
    return extractor.extract(text, use_llm=use_llm)


def extract_skills_with_relations(text: str, use_llm: bool = True) -> Dict:
    """
    抽取技能和关系

    Args:
        text: 输入文本
        use_llm: 是否使用 LLM

    Returns:
        {"skills": [...], "relations": [...]}
    """
    skills = extract_skills(text, use_llm=use_llm)
    skill_names = [s["name"] for s in skills]
    relations = extract_relations(skill_names, text)

    return {
        "skills": skills,
        "relations": relations
    }


if __name__ == "__main__":
    test_text = """
    Python后端开发工程师

    熟练掌握：
    - Python, Django, Flask
    - MySQL, Redis
    - Docker 容器化部署

    了解/接触过：
    - Vue.js 前端开发
    - Kubernetes 集群管理
    - 机器学习基础

    加分项：
    - 有 TensorFlow 或 PyTorch 项目经验
    - 熟悉 AWS 或阿里云
    """

    print("=" * 60)
    print("LLM 增强技能抽取测试")
    print("=" * 60)

    print("\n[1] 词典匹配:")
    dict_skills = dict_extract(test_text)
    for s in dict_skills:
        print(f"  {s['name']} ({s['category']})")

    print("\n[2] LLM 增强:")
    result = extract_skills_with_relations(test_text, use_llm=True)
    for s in result["skills"]:
        src = f"[{s.get('source', 'unknown')}]"
        print(f"  {src} {s['name']} ({s['category']})")

    print("\n[3] 关系:")
    for r in result["relations"]:
        print(f"  {r['source']} --({r['type']})--> {r['target']}")
