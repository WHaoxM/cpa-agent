"""
图谱存储模块 (Graph Storage)
将技能抽取结果存入 Neo4j 图谱
"""

import uuid
from datetime import datetime
from typing import List, Dict, Optional
from app.services.db.neo4j_client import get_neo4j_client
from app.utils.logger import get_logger

logger = get_logger('cpa-agent.graph_storage')


class GraphStorage:
    """图谱存储器"""

    def __init__(self):
        self.neo4j = get_neo4j_client()

    def execute_cypher(self, cypher: str, parameters: Optional[Dict] = None, read_only: bool = False):
        """执行 Cypher 查询 (委托给 Neo4jClient.execute_query)

        旧代码使用 execute_cypher 名称, 这里作为别名保持兼容。
        """
        return self.neo4j.execute_query(cypher, parameters=parameters, read_only=read_only)

    def upsert_skill_node(self, node_data: Dict) -> bool:
        """创建或更新 Skill 节点 (MERGE)"""
        try:
            cypher = """
            MERGE (s:Skill {name: $name})
            SET s.aliases = $aliases,
                s.category = $category,
                s.sub_category = $sub_category,
                s.frequency = $frequency,
                s.proficiency_levels = $proficiency_levels,
                s.tier = $tier,
                s.heat = $heat
            RETURN s.name AS name
            """
            params = {
                "name": node_data.get("name", ""),
                "aliases": node_data.get("aliases", []),
                "category": node_data.get("category", "其他"),
                "sub_category": node_data.get("sub_category", ""),
                "frequency": node_data.get("frequency", 50),
                "proficiency_levels": node_data.get("proficiency_levels", []),
                "tier": node_data.get("tier", "foundation"),
                "heat": node_data.get("heat", 50),
            }
            result = self.neo4j.execute_query(cypher, parameters=params, read_only=False)
            return len(result) > 0
        except Exception as e:
            logger.error(f"upsert_skill_node 失败: {e}")
            return False

    def save_skills(
        self,
        entity_id: str,
        entity_type: str,
        skills: List[Dict],
        relations: List[Dict]
    ) -> Dict:
        """
        保存技能和关系到图谱 (批量优化)

        Args:
            entity_id: 实体ID (学生ID或JD_ID)
            entity_type: 实体类型 (student, job_posting)
            skills: 技能列表
            relations: 关系列表

        Returns:
            保存结果
        """
        if not skills:
            return {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "saved_skills": 0,
                "saved_relations": 0,
                "timestamp": datetime.now().isoformat()
            }

        saved_skills = 0
        saved_relations = 0

        with self.neo4j.session() as session:
            try:
                skill_names = [s["name"] for s in skills]
                self._batch_save_skill_nodes(session, skills)
                self._batch_save_entity_skill_relations(session, entity_id, entity_type, skills)
                saved_skills = len(skill_names)
            except Exception as e:
                logger.warning(f"批量保存技能失败: {e}")

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

    def _batch_save_skill_nodes(self, session, skills: List[Dict]):
        """批量保存技能节点 (UNWIND 优化 N+1)"""
        cypher = """
        UNWIND $skills AS skill
        MERGE (s:Skill {name: skill.name})
        SET s.category = skill.category,
            s.sub_category = skill.sub_category,
            s.aliases = skill.aliases,
            s.frequency = skill.frequency,
            s.confidence = skill.confidence,
            s.source = skill.source,
            s.updated_at = datetime()
        """
        session.run(cypher, {
            "skills": [{
                "name": s["name"],
                "category": s.get("category", "其他"),
                "sub_category": s.get("sub_category", ""),
                "aliases": s.get("aliases", []),
                "frequency": s.get("frequency", 0),
                "confidence": s.get("confidence", 0.5),
                "source": s.get("source", "unknown")
            } for s in skills]
        })

    def _batch_save_entity_skill_relations(
        self,
        session,
        entity_id: str,
        entity_type: str,
        skills: List[Dict]
    ):
        """批量保存实体-技能关系 (UNWIND 优化 N+1)"""
        if entity_type == "student":
            label = "Person"
            rel_type = "HAS_SKILL"
            id_key = "entity_id"
        else:
            label = "JobPortrait"
            rel_type = "REQUIRES"
            id_key = "portrait_id"

        records = []
        for skill in skills:
            if entity_type == "student":
                level = self._infer_skill_level(skill)
            else:
                level = self._infer_required_level(skill)
            records.append({
                "id_value": entity_id,
                "skill_name": skill["name"],
                "confidence": skill.get("confidence", 0.5),
                "level": level
            })

        cypher = f"""
        UNWIND $records AS rec
        MERGE (e:{label} {{{id_key}: rec.id_value}})
        MERGE (s:Skill {{name: rec.skill_name}})
        MERGE (e)-[r:{rel_type}]->(s)
        SET r.confidence = rec.confidence,
            r.level = rec.level,
            r.updated_at = datetime()
        """
        session.run(cypher, {"records": records})

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

    VALID_RELATION_TYPES = frozenset({
        "BELONGS_TO", "CHILD_OF", "REQUIRES", "PREFERS", "BONUS",
        "PROMOTION", "TRANSFER", "PREREQUISITE", "REPLACES", "EVOLVES_TO"
    })

    def _get_relation_type(self, rel_type: str) -> str:
        mapping = {
            "prerequisite": "PREREQUISITE",
            "similar": "REPLACES",
            "related": "EVOLVES_TO",
            "alternative": "REPLACES",
            "composed": "PREREQUISITE",
            "co_occurrence": "PREFERS",
            "belongs_to": "BELONGS_TO",
            "child_of": "CHILD_OF",
            "requires": "REQUIRES",
            "prefers": "PREFERS",
            "bonus": "BONUS",
            "promotion": "PROMOTION",
            "transfer": "TRANSFER",
            "replaces": "REPLACES",
            "evolves_to": "EVOLVES_TO",
        }
        mapped = mapping.get(rel_type.lower(), "")
        if mapped and mapped in self.VALID_RELATION_TYPES:
            return mapped
        raise ValueError(f"Invalid relation type: {rel_type}. Allowed: {', '.join(sorted(self.VALID_RELATION_TYPES))}")

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
        tf_score = skill.get("tf_score", 0.5)
        if tf_score >= 0.7:
            return "必须"
        elif tf_score >= 0.4:
            return "优先"
        return "加分"

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
        with self.neo4j.session() as session:
            if entity_type == "student":
                cypher = """
                MATCH (e:Person {entity_id: $entity_id})-[r:HAS_SKILL]->(s:Skill)
                RETURN s.name as name,
                       s.category as category,
                       r.confidence as confidence,
                       r.level as level
                ORDER BY r.confidence DESC
                """
            else:
                cypher = """
                MATCH (e:JobPortrait {portrait_id: $entity_id})-[r:REQUIRES]->(s:Skill)
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
        # 不硬编码 REPLACES/EVOLVES_TO 等可能不存在的类型；
        # 只匹配库中真实存在的 Skill↔Skill 边，避免 Neo4j UNRECOGNIZED 警告拖垮工具链。
        with self.neo4j.session() as session:
            cypher = """
            MATCH (s:Skill)-[r]-(other:Skill)
            WHERE s.name IN $skill_names
              AND NOT other.name IN $skill_names
            WITH other, count(r) AS weight, collect(DISTINCT type(r)) AS rel_types
            RETURN other.name AS name,
                   other.category AS category,
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
                rel_types = record["rel_types"] or []
                rel_type = rel_types[0] if rel_types else ""
                recommendations.append({
                    "name": record["name"],
                    "category": record["category"],
                    "weight": record["weight"],
                    "reason": self._get_recommend_reason(record["name"], skill_names, rel_type)
                })

            return recommendations

    def _get_recommend_reason(self, skill: str, known_skills: List[str], rel_type: str) -> str:
        rel = (rel_type or "").upper()
        if rel in ("PREREQUISITE", "REQUIRES", "DEPENDS_ON"):
            return f"学习 {skill} 前建议先掌握相关基础技能"
        if rel in ("REPLACES", "SIMILAR", "ALTERNATIVE"):
            return f"与已掌握的技能相似，可作为拓展方向"
        if rel in ("EVOLVES_TO", "LEADS_TO", "NEXT"):
            return f"技能演进方向，{skill} 是后续进阶选项"
        if rel:
            return f"通过关系 {rel_type} 关联到 {skill}"
        return f"{skill} 与您已掌握技能在图谱中相邻"

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

    def get_promotion_chains(self, position_name: str, min_chain_length: int = 3) -> List[Dict]:
        """
        获取晋升链 (R-06: ≥3条链)

        Args:
            position_name: 起始岗位名称
            min_chain_length: 最小链长度

        Returns:
            晋升链列表
        """
        with self.neo4j.session() as session:
            cypher = """
            MATCH path = (start:CareerNode)-[:PROMOTION*1..5]->(end:CareerNode)
            WHERE start.position_name = $position_name
            RETURN path,
                   length(path) as chain_length,
                   [n IN nodes(path) | n.position_name] as positions
            ORDER BY chain_length DESC
            LIMIT 10
            """

            result = session.run(cypher, {"position_name": position_name})
            chains = []

            for record in result:
                path = record["path"]
                if len(path.relationships) >= min_chain_length - 1:
                    chains.append({
                        "chain_length": record["chain_length"],
                        "positions": record["positions"],
                        "steps": [rel.get("skill_delta", "") for rel in path.relationships]
                    })

            return chains

    def get_transfer_options(self, position_name: str, min_jaccard: float = 0.3) -> List[Dict]:
        """
        获取换岗选项 (R-07: ≥5×2)

        Args:
            position_name: 当前岗位名称
            min_jaccard: 最小 Jaccard 相似度

        Returns:
            换岗选项列表
        """
        with self.neo4j.session() as session:
            cypher = """
            MATCH (current:CareerNode {position_name: $position_name})
            MATCH (current)-[r:TRANSFER]->(target:CareerNode)
            WHERE r.jaccard >= $min_jaccard
            RETURN target.position_name as target_position,
                   r.jaccard as jaccard,
                   r.affinity as affinity,
                   r.skill_overlap as skill_overlap
            ORDER BY r.jaccard DESC
            LIMIT 10
            """

            result = session.run(cypher, {
                "position_name": position_name,
                "min_jaccard": min_jaccard
            })

            return [dict(record) for record in result]

    def get_career_graph_summary(self) -> Dict:
        """
        获取职业图谱摘要

        Returns:
            图谱统计信息
        """
        with self.neo4j.session() as session:
            summary = {}

            promotion_count = session.run("""
                MATCH ()-[r:PROMOTION]->()
                RETURN count(r) as count
            """).single()
            summary["promotion_edges"] = promotion_count["count"] if promotion_count else 0

            transfer_count = session.run("""
                MATCH ()-[r:TRANSFER]->()
                RETURN count(r) as count
            """).single()
            summary["transfer_edges"] = transfer_count["count"] if transfer_count else 0

            career_nodes = session.run("""
                MATCH (n:CareerNode)
                RETURN count(n) as count
            """).single()
            summary["career_nodes"] = career_nodes["count"] if career_nodes else 0

            return summary

    def save_job_portrait(
        self,
        position_name: str,
        portrait_id: Optional[str] = None,
        required_skills: Optional[List[Dict]] = None,
        preferred_skills: Optional[List[Dict]] = None,
        bonus_skills: Optional[List[Dict]] = None,
        required_degree: str = "本科",
        d1_weight: float = 0.25,
        d2_weight: float = 0.35,
        d3_weight: float = 0.20,
        d4_weight: float = 0.20,
        jd_count: int = 1,
        major_strict: bool = False
    ) -> Dict:
        """
        保存岗位画像到图谱

        Args:
            position_name: 标准化岗位名称
            portrait_id: 画像ID (可选，自动生成)
            required_skills: 必备技能列表 [{"name": "Python", "tf_score": 0.9}, ...]
            preferred_skills: 优选技能列表
            bonus_skills: 加分技能列表
            required_degree: 学历要求
            d1_weight-d4_weight: 四维权重
            jd_count: 聚合的JD数量
            major_strict: 是否严格限专业

        Returns:
            保存结果
        """
        if not portrait_id:
            portrait_id = f"portrait_{uuid.uuid4().hex[:12]}"

        saved_skills = 0

        with self.neo4j.session() as session:
            cypher = """
            MERGE (j:JobPortrait {portrait_id: $portrait_id})
            SET j.position_name = $position_name,
                j.required_degree = $required_degree,
                j.d1_weight = $d1_weight,
                j.d2_weight = $d2_weight,
                j.d3_weight = $d3_weight,
                j.d4_weight = $d4_weight,
                j.jd_count = $jd_count,
                j.major_strict = $major_strict,
                j.updated_at = datetime()
            """
            session.run(cypher, {
                "portrait_id": portrait_id,
                "position_name": position_name,
                "required_degree": required_degree,
                "d1_weight": d1_weight,
                "d2_weight": d2_weight,
                "d3_weight": d3_weight,
                "d4_weight": d4_weight,
                "jd_count": jd_count,
                "major_strict": major_strict
            })

            if required_skills:
                saved_skills += self._save_portrait_skills(
                    session, portrait_id, required_skills, "REQUIRES"
                )
            if preferred_skills:
                saved_skills += self._save_portrait_skills(
                    session, portrait_id, preferred_skills, "PREFERS"
                )
            if bonus_skills:
                saved_skills += self._save_portrait_skills(
                    session, portrait_id, bonus_skills, "BONUS"
                )

        return {
            "portrait_id": portrait_id,
            "position_name": position_name,
            "required_skills_count": len(required_skills) if required_skills else 0,
            "preferred_skills_count": len(preferred_skills) if preferred_skills else 0,
            "bonus_skills_count": len(bonus_skills) if bonus_skills else 0,
            "saved_skills": saved_skills,
            "timestamp": datetime.now().isoformat()
        }

    def _save_portrait_skills(
        self,
        session,
        portrait_id: str,
        skills: List[Dict],
        rel_type: str
    ) -> int:
        if rel_type not in self.VALID_RELATION_TYPES:
            raise ValueError(f"Invalid relation type: {rel_type}")

        if not skills:
            return 0

        records = []
        for skill in skills:
            skill_name = skill.get("name", "")
            if not skill_name:
                continue
            tf_score = skill.get("tf_score", 0.5)
            level = skill.get("level", "必须" if rel_type == "REQUIRES" else "优先")
            records.append({
                "portrait_id": portrait_id,
                "skill_name": skill_name,
                "tf_score": tf_score,
                "level": level
            })

        if not records:
            return 0

        cypher = f"""
        UNWIND $records AS rec
        MATCH (j:JobPortrait {{portrait_id: rec.portrait_id}})
        MERGE (s:Skill {{name: rec.skill_name}})
        MERGE (j)-[r:{rel_type}]->(s)
        SET r.tf_score = rec.tf_score,
            r.level = rec.level,
            r.updated_at = datetime()
        """
        session.run(cypher, {"records": records})
        return len(records)

    def get_job_portrait(self, portrait_id: str) -> Optional[Dict]:
        """获取岗位画像"""
        with self.neo4j.session() as session:
            cypher = """
            MATCH (j:JobPortrait {portrait_id: $portrait_id})
            OPTIONAL MATCH (j)-[r:REQUIRES]->(s:Skill)
            OPTIONAL MATCH (j)-[r2:PREFERS]->(s2:Skill)
            OPTIONAL MATCH (j)-[r3:BONUS]->(s3:Skill)
            RETURN j,
                   collect(DISTINCT {name: s.name, tf_score: r.tf_score}) as required_skills,
                   collect(DISTINCT {name: s2.name, tf_score: r2.tf_score}) as preferred_skills,
                   collect(DISTINCT {name: s3.name, tf_score: r3.tf_score}) as bonus_skills
            """

            result = session.run(cypher, {"portrait_id": portrait_id})
            record = result.single()

            if not record:
                return None

            j = record["j"]
            return {
                "portrait_id": j.get("portrait_id"),
                "position_name": j.get("position_name"),
                "required_degree": j.get("required_degree"),
                "jd_count": j.get("jd_count", 0),
                "required_skills": [s for s in record["required_skills"] if s.get("name")],
                "preferred_skills": [s for s in record["preferred_skills"] if s.get("name")],
                "bonus_skills": [s for s in record["bonus_skills"] if s.get("name")],
                "d1_weight": j.get("d1_weight"),
                "d2_weight": j.get("d2_weight"),
                "d3_weight": j.get("d3_weight"),
                "d4_weight": j.get("d4_weight")
            }

    def save_skill_with_relation(
        self,
        skill: Dict,
        relation_type: str = "REQUIRES",
        target_entity_id: Optional[str] = None,
        target_entity_type: Optional[str] = None
    ) -> bool:
        if relation_type not in self.VALID_RELATION_TYPES:
            raise ValueError(f"Invalid relation type: {relation_type}")

        with self.neo4j.session() as session:
            try:
                self._save_skill_node(session, skill)

                if target_entity_id and target_entity_type:
                    if target_entity_type == "job_portrait":
                        cypher = f"""
                        MATCH (j:JobPortrait {{portrait_id: $entity_id}})
                        MATCH (s:Skill {{name: $skill_name}})
                        MERGE (j)-[r:{relation_type}]->(s)
                        SET r.confidence = $confidence,
                            r.updated_at = datetime()
                        """
                    elif target_entity_type == "student":
                        cypher = f"""
                        MATCH (e:Person {{entity_id: $entity_id}})
                        MATCH (s:Skill {{name: $skill_name}})
                        MERGE (e)-[r:{relation_type}]->(s)
                        SET r.confidence = $confidence,
                            r.updated_at = datetime()
                        """
                    else:
                        return False

                    session.run(cypher, {
                        "entity_id": target_entity_id,
                        "skill_name": skill["name"],
                        "confidence": skill.get("confidence", 0.5)
                    })

                return True
            except Exception as e:
                logger.warning(f"保存技能失败: {skill.get('name')}, {e}")
                return False


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
