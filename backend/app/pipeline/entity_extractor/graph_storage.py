"""
图谱存储模块 (Graph Storage)
将技能抽取结果存入 Neo4j 图谱
"""

import uuid
from datetime import datetime
from typing import List, Dict, Optional
from app.services.db.neo4j_client import Neo4jClient
from app.utils.logger import get_logger

logger = get_logger('cpa-agent.graph_storage')


class GraphStorage:
    """图谱存储器"""

    def __init__(self):
        self.neo4j = Neo4jClient()

    def save_skills(
        self,
        entity_id: str,
        entity_type: str,
        skills: List[Dict],
        relations: List[Dict]
    ) -> Dict:
        """
        保存技能和关系到图谱

        Args:
            entity_id: 实体ID (学生ID或JD_ID)
            entity_type: 实体类型 (student, job_posting)
            skills: 技能列表
            relations: 关系列表

        Returns:
            保存结果
        """
        saved_skills = 0
        saved_relations = 0

        with self.neo4j.session() as session:
            for skill in skills:
                try:
                    self._save_skill_node(session, skill)
                    self._save_entity_skill_relation(
                        session, entity_id, entity_type, skill
                    )
                    saved_skills += 1
                except Exception as e:
                    logger.warning(f"保存技能失败: {skill.get('name')}, {e}")

            for rel in relations:
                try:
                    self._save_relation(session, rel)
                    saved_relations += 1
                except Exception as e:
                    logger.warning(f"保存关系失败: {rel}, {e}")

        return {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "saved_skills": saved_skills,
            "saved_relations": saved_relations,
            "timestamp": datetime.now().isoformat()
        }

    def _save_skill_node(self, session, skill: Dict):
        """保存技能节点"""
        cypher = """
        MERGE (s:Skill {name: $name})
        SET s.category = $category,
            s.confidence = $confidence,
            s.source = $source,
            s.updated_at = datetime()
        """

        session.run(cypher, {
            "name": skill["name"],
            "category": skill.get("category", "其他"),
            "confidence": skill.get("confidence", 0.5),
            "source": skill.get("source", "unknown")
        })

    def _save_entity_skill_relation(
        self,
        session,
        entity_id: str,
        entity_type: str,
        skill: Dict
    ):
        """保存实体-技能关系"""
        if entity_type == "student":
            cypher = """
            MERGE (e:Student {student_id: $entity_id})
            MERGE (s:Skill {name: $skill_name})
            MERGE (e)-[r:HAS_SKILL]->(s)
            SET r.confidence = $confidence,
                r.level = $level,
                r.updated_at = datetime()
            """
            level = self._infer_skill_level(skill)
        else:
            cypher = """
            MERGE (e:JobPosting {job_id: $entity_id})
            MERGE (s:Skill {name: $skill_name})
            MERGE (e)-[r:REQUIRES]->(s)
            SET r.confidence = $confidence,
                r.level = $level,
                r.updated_at = datetime()
            """
            level = self._infer_required_level(skill)

        session.run(cypher, {
            "entity_id": entity_id,
            "skill_name": skill["name"],
            "confidence": skill.get("confidence", 0.5),
            "level": level
        })

    def _save_relation(self, session, relation: Dict):
        """保存技能之间的关系"""
        source = relation["source"]
        target = relation["target"]
        rel_type = relation["type"]
        confidence = relation.get("confidence", 0.5)

        cypher = f"""
        MERGE (s1:Skill {{name: $source}})
        MERGE (s2:Skill {{name: $target}})
        MERGE (s1)-[r:{self._get_relation_type(rel_type)}]->(s2)
        SET r.confidence = $confidence,
            r.updated_at = datetime()
        """

        session.run(cypher, {
            "source": source,
            "target": target,
            "confidence": confidence
        })

    def _get_relation_type(self, rel_type: str) -> str:
        """转换关系类型到 Cypher 关系类型"""
        mapping = {
            "prerequisite": "PREREQUISITE",
            "similar": "SIMILAR",
            "related": "RELATED",
            "alternative": "ALTERNATIVE",
            "composed": "COMPOSED",
            "co_occurrence": "CO_OCCURS"
        }
        return mapping.get(rel_type, "RELATED")

    def _infer_skill_level(self, skill: Dict) -> str:
        """推断技能掌握级别"""
        confidence = skill.get("confidence", 0.5)
        if confidence >= 0.8:
            return "精通"
        elif confidence >= 0.6:
            return "掌握"
        elif confidence >= 0.4:
            return "熟悉"
        return "了解"

    def _infer_required_level(self, skill: Dict) -> str:
        """推断技能要求级别"""
        return "必须"

    def get_skill_neighbors(self, skill_name: str, limit: int = 10) -> List[Dict]:
        """获取技能的邻居"""
        with self.neo4j.session() as session:
            cypher = """
            MATCH (s:Skill {name: $skill_name})-[r]-(neighbor)
            RETURN neighbor.name as name,
                   neighbor.category as category,
                   type(r) as relation,
                   r.confidence as confidence
            LIMIT $limit
            """

            result = session.run(cypher, {
                "skill_name": skill_name,
                "limit": limit
            })

            return [dict(record) for record in result]

    def get_entity_skills(self, entity_id: str, entity_type: str) -> List[Dict]:
        """获取实体的所有技能"""
        with self.neo4j.session() as session:
            if entity_type == "student":
                cypher = """
                MATCH (e:Student {student_id: $entity_id})-[r:HAS_SKILL]->(s:Skill)
                RETURN s.name as name,
                       s.category as category,
                       r.confidence as confidence,
                       r.level as level
                ORDER BY r.confidence DESC
                """
            else:
                cypher = """
                MATCH (e:JobPosting {job_id: $entity_id})-[r:REQUIRES]->(s:Skill)
                RETURN s.name as name,
                       s.category as category,
                       r.confidence as confidence,
                       r.level as level
                ORDER BY r.confidence DESC
                """

            result = session.run(cypher, {"entity_id": entity_id})
            return [dict(record) for record in result]

    def recommend_skills(
        self,
        skill_names: List[str],
        limit: int = 5
    ) -> List[Dict]:
        """
        基于已有技能推荐相关技能

        Args:
            skill_names: 已有的技能列表
            limit: 返回数量

        Returns:
            推荐的技能列表
        """
        with self.neo4j.session() as session:
            cypher = """
            MATCH (s:Skill)-[r:SIMILAR|PREREQUISITE|RELATED]-(other:Skill)
            WHERE s.name IN $skill_names
            AND NOT other.name IN $skill_names
            WITH other, count(r) as weight, collect(type(r)) as rel_types
            RETURN other.name as name,
                   other.category as category,
                   weight,
                   rel_types
            ORDER BY weight DESC
            LIMIT $limit
            """

            result = session.run(cypher, {
                "skill_names": skill_names,
                "limit": limit
            })

            recommendations = []
            for record in result:
                rel_type = record["rel_types"][0] if record["rel_types"] else "RELATED"
                recommendations.append({
                    "name": record["name"],
                    "category": record["category"],
                    "weight": record["weight"],
                    "reason": self._get_recommend_reason(record["name"], skill_names, rel_type)
                })

            return recommendations

    def _get_recommend_reason(self, skill: str, known_skills: List[str], rel_type: str) -> str:
        """获取推荐原因"""
        if rel_type == "PREREQUISITE":
            return f"学习 {skill} 前建议先掌握相关基础技能"
        elif rel_type == "SIMILAR":
            return f"与已掌握的技能相似，可作为拓展方向"
        else:
            return f"热门技能，{skill} 与您已掌握技能相关"

    def find_learning_path(
        self,
        from_skill: str,
        to_skill: str,
        max_hops: int = 5
    ) -> Dict:
        """
        查找两技能之间的学习路径

        Args:
            from_skill: 起始技能
            to_skill: 目标技能
            max_hops: 最大跳数

        Returns:
            学习路径
        """
        with self.neo4j.session() as session:
            cypher = f"""
            MATCH path = (start:Skill)-[r:PREREQUISITE*1..{max_hops}]->(end:Skill)
            WHERE start.name = $from_skill AND end.name = $to_skill
            RETURN path,
                   length(path) as hops
            ORDER BY hops ASC
            LIMIT 1
            """

            result = session.run(cypher, {
                "from_skill": from_skill,
                "to_skill": to_skill
            })

            records = list(result)

            if not records:
                return {
                    "found": False,
                    "from_skill": from_skill,
                    "to_skill": to_skill,
                    "path": [],
                    "message": f"未找到从 {from_skill} 到 {to_skill} 的学习路径"
                }

            record = records[0]
            path = record["path"]

            nodes = []
            edges = []

            for i, node in enumerate(path.nodes):
                nodes.append({
                    "name": node.get("name"),
                    "category": node.get("category")
                })

            for rel in path.relationships:
                edges.append({
                    "from": rel.start_node.get("name"),
                    "to": rel.end_node.get("name"),
                    "type": type(rel).__name__
                })

            return {
                "found": True,
                "from_skill": from_skill,
                "to_skill": to_skill,
                "hops": record["hops"],
                "nodes": nodes,
                "edges": edges,
                "steps": [n["name"] for n in nodes]
            }

    def get_skill_graph(self, skill_names: List[str]) -> Dict:
        """获取技能子图"""
        with self.neo4j.session() as session:
            cypher = """
            MATCH (s1:Skill)-[r]-(s2:Skill)
            WHERE s1.name IN $skill_names AND s2.name IN $skill_names
            RETURN s1.name as source,
                   s2.name as target,
                   type(r) as relation,
                   r.confidence as confidence
            """

            result = session.run(cypher, {"skill_names": skill_names})

            nodes = set()
            edges = []

            for record in result:
                nodes.add(record["source"])
                nodes.add(record["target"])
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "relation": record["relation"],
                    "confidence": record["confidence"]
                })

            return {
                "nodes": [{"name": n} for n in nodes],
                "edges": edges
            }


def save_skills_to_graph(
    entity_id: str,
    entity_type: str,
    skills: List[Dict],
    relations: List[Dict]
) -> Dict:
    """
    便捷函数: 保存技能到图谱
    """
    storage = GraphStorage()
    return storage.save_skills(entity_id, entity_type, skills, relations)


def get_skill_neighbors(skill_name: str, limit: int = 10) -> List[Dict]:
    """便捷函数: 获取技能邻居"""
    storage = GraphStorage()
    return storage.get_skill_neighbors(skill_name, limit)


if __name__ == "__main__":
    test_skills = [
        {"name": "Python", "category": "编程语言", "confidence": 0.8, "source": "dict"},
        {"name": "Django", "category": "后端框架", "confidence": 0.75, "source": "dict"},
        {"name": "MySQL", "category": "数据库", "confidence": 0.7, "source": "dict"},
        {"name": "Redis", "category": "数据库", "confidence": 0.65, "source": "dict"},
    ]

    test_relations = [
        {"source": "Django", "target": "Python", "type": "prerequisite", "confidence": 0.8},
        {"source": "Django", "target": "MySQL", "type": "similar", "confidence": 0.6},
    ]

    print("=" * 50)
    print("图谱存储测试")
    print("=" * 50)

    storage = GraphStorage()
    result = storage.save_skills(
        entity_id="test_student_001",
        entity_type="student",
        skills=test_skills,
        relations=test_relations
    )

    print(f"\n保存结果:")
    print(f"  实体ID: {result['entity_id']}")
    print(f"  保存技能: {result['saved_skills']}")
    print(f"  保存关系: {result['saved_relations']}")
    print(f"  时间: {result['timestamp']}")

    print("\n查询测试:")
    neighbors = storage.get_skill_neighbors("Django")
    print(f"  Django 的邻居: {[n['name'] for n in neighbors]}")
