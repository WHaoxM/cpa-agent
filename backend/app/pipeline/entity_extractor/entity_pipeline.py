"""
实体抽取 Pipeline (Entity Extraction Pipeline)
整合 L0 文本解析 + L1 技能抽取 + 图谱存储
"""

import uuid
from typing import Dict, List, Optional
from .skill_extractor import extract_skills as dict_extract
from .llm_skill_extractor import extract_skills_with_relations, LLMSkillExtractor
from .graph_storage import save_skills_to_graph, GraphStorage
from app.utils.logger import get_logger

logger = get_logger('cpa-agent.entity_pipeline')


class EntityPipeline:
    """实体抽取 Pipeline"""

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        self.llm_extractor = LLMSkillExtractor() if use_llm else None
        self.graph_storage = GraphStorage()

    def run_l0_parse(self, text: str) -> Dict:
        """
        L0: 文本解析
        - 清洗文本
        - 分段
        - 提取元信息
        """
        if not text:
            return {"success": False, "error": "文本为空"}

        text = self._clean_text(text)

        return {
            "success": True,
            "data": {
                "text": text,
                "length": len(text),
                "segments": self._segment_text(text)
            }
        }

    def run_l1_extract(self, text: str, entity_id: Optional[str] = None) -> Dict:
        """
        L1: 技能抽取 + 关系抽取 + 图谱存储

        Args:
            text: 输入文本
            entity_id: 实体ID (可选)

        Returns:
            抽取结果
        """
        if not text:
            return {"success": False, "error": "文本为空"}

        if not entity_id:
            entity_id = f"entity_{uuid.uuid4().hex[:12]}"

        try:
            result = extract_skills_with_relations(text, use_llm=self.use_llm)

            skills = result["skills"]
            relations = result["relations"]

            saved = self.graph_storage.save_skills(
                entity_id=entity_id,
                entity_type="student",
                skills=skills,
                relations=relations
            )

            return {
                "success": True,
                "data": {
                    "entity_id": entity_id,
                    "skills": skills,
                    "relations": relations,
                    "skill_count": len(skills),
                    "relation_count": len(relations),
                    "graph_saved": saved
                }
            }

        except Exception as e:
            logger.error(f"L1 抽取失败: {e}")
            return {"success": False, "error": str(e)}

    def run_full(self, text: str, entity_id: Optional[str] = None) -> Dict:
        """
        运行完整 Pipeline: L0 + L1

        Args:
            text: 输入文本
            entity_id: 实体ID

        Returns:
            完整结果
        """
        l0_result = self.run_l0_parse(text)
        if not l0_result["success"]:
            return l0_result

        l1_result = self.run_l1_extract(text, entity_id)
        if not l1_result["success"]:
            return l1_result

        return {
            "success": True,
            "data": {
                "entity_id": l1_result["data"]["entity_id"],
                "l0": l0_result["data"],
                "l1": l1_result["data"],
                "summary": {
                    "skills_count": l1_result["data"]["skill_count"],
                    "relations_count": l1_result["data"]["relation_count"],
                    "categories": self._count_categories(l1_result["data"]["skills"])
                }
            }
        }

    def _clean_text(self, text: str) -> str:
        """清洗文本"""
        import re
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        return text.strip()

    def _segment_text(self, text: str, max_length: int = 500) -> List[str]:
        """分段文本"""
        if len(text) <= max_length:
            return [text]

        sentences = re.split(r'[。！？\n]', text)
        segments = []
        current = ""

        for sentence in sentences:
            if len(current) + len(sentence) <= max_length:
                current += sentence + "。"
            else:
                if current:
                    segments.append(current.strip())
                current = sentence + "。"

        if current:
            segments.append(current.strip())

        return segments

    def _count_categories(self, skills: List[Dict]) -> Dict[str, int]:
        """统计技能类别"""
        categories = {}
        for skill in skills:
            cat = skill.get("category", "其他")
            categories[cat] = categories.get(cat, 0) + 1
        return categories


def run_entity_pipeline(
    text: str,
    entity_id: Optional[str] = None,
    use_llm: bool = True
) -> Dict:
    """
    便捷函数: 运行实体抽取 Pipeline

    Args:
        text: 输入文本
        entity_id: 实体ID
        use_llm: 是否使用LLM

    Returns:
        抽取结果
    """
    pipeline = EntityPipeline(use_llm=use_llm)
    return pipeline.run_full(text, entity_id)


if __name__ == "__main__":
    test_text = """
    Python 后端开发工程师

    熟练掌握：
    - Python, Django, Flask, FastAPI
    - MySQL, PostgreSQL, Redis
    - Docker, Kubernetes
    - Git, Jenkins

    了解：
    - Vue.js, React
    - TensorFlow, PyTorch
    - AWS, 阿里云

    3年以上后端开发经验，
    有大型分布式系统开发经验优先。
    """

    print("=" * 60)
    print("实体抽取 Pipeline 测试")
    print("=" * 60)

    result = run_entity_pipeline(test_text, use_llm=True)

    if result["success"]:
        data = result["data"]
        print(f"\n实体ID: {data['entity_id']}")
        print(f"技能数量: {data['summary']['skills_count']}")
        print(f"关系数量: {data['summary']['relations_count']}")
        print(f"\n类别统计:")
        for cat, count in data["summary"]["categories"].items():
            print(f"  {cat}: {count}")
        print(f"\n技能列表:")
        for skill in data["l1"]["skills"]:
            print(f"  - {skill['name']} ({skill['category']})")
    else:
        print(f"\n失败: {result.get('error')}")
