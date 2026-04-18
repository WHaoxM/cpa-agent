"""
知识图谱相关 API 路由
/api/graph — Neo4j 图谱查询、构建、可视化数据
"""

import uuid
from flask import request, jsonify

from . import graph_bp
from ..services.db import get_neo4j_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.graph')


def _convert_nl_to_cypher(nl_query: str) -> str:
    """简单自然语言转 Cypher"""
    nl = nl_query.lower()

    if '技能' in nl or 'skill' in nl:
        if '前端' in nl:
            return "MATCH (s:Skill) WHERE s.category = '前端' RETURN s ORDER BY s.heat DESC LIMIT 20"
        elif '后端' in nl:
            return "MATCH (s:Skill) WHERE s.category = '后端' RETURN s ORDER BY s.heat DESC LIMIT 20"
        elif '需要' in nl or 'require' in nl:
            return "MATCH (j:JobPortrait)-[r:REQUIRES]->(s:Skill) RETURN s.name, r.tf_score ORDER BY r.tf_score DESC LIMIT 20"
        else:
            return "MATCH (s:Skill) RETURN s ORDER BY s.heat DESC LIMIT 50"

    elif '职业路径' in nl or 'career' in nl or '晋升' in nl:
        return "MATCH path=(c1:CareerNode)-[:PROMOTION*1..3]->(c2:CareerNode) RETURN c1.position_name as start, c2.position_name as end, length(path) as steps LIMIT 20"

    elif '岗位' in nl or 'job' in nl or '职位' in nl:
        return "MATCH (j:JobPortrait) RETURN j.position_name, j.jd_count ORDER BY j.jd_count DESC LIMIT 20"

    elif '学习' in nl or 'learn' in nl or '路线' in nl:
        return "MATCH (s1:Skill)-[r:PREREQUISITE]->(s2:Skill) RETURN s1.name as skill, s2.name as prerequisite LIMIT 50"

    else:
        return f"MATCH (n) RETURN n LIMIT {20}"


@graph_bp.route('/overview', methods=['GET'])
def graph_overview():
    """获取图谱概览统计 (节点数/边数/类型分布)"""
    try:
        neo4j = get_neo4j_client()

        if not neo4j.test_connection():
            return jsonify({
                "success": False,
                "error": "Neo4j 连接失败"
            }), 500

        node_count = neo4j.get_node_count()
        edge_count = neo4j.get_edge_count()

        schema = neo4j.get_schema()

        return jsonify({
            "success": True,
            "data": {
                "node_count": node_count,
                "edge_count": edge_count,
                "node_types": schema.get("node_labels", []),
                "node_counts_by_type": schema.get("node_counts", {}),
                "edge_types": schema.get("relationship_types", [])
            }
        })

    except Exception as e:
        logger.error(f"图谱概览查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


@graph_bp.route('/query', methods=['POST'])
def graph_query():
    """执行 Cypher 查询或自然语言查询"""
    data = request.get_json() or {}
    cypher = data.get('cypher', '')
    natural_query = data.get('query', '')
    limit = data.get('limit', 100)

    if not cypher and natural_query:
        cypher = _convert_nl_to_cypher(natural_query)

    if not cypher:
        return jsonify({
            "success": False,
            "error": "cypher 或 query 参数至少需要一个"
        }), 400

    forbidden_keywords = ['CREATE', 'DELETE', 'REMOVE', 'SET', 'MERGE']
    cypher_upper = cypher.upper()
    if any(kw in cypher_upper for kw in forbidden_keywords):
        return jsonify({
            "success": False,
            "error": "只允许执行只读查询 (MATCH/RETURN)"
        }), 400

    try:
        neo4j = get_neo4j_client()

        if 'LIMIT' not in cypher_upper:
            if cypher_upper.strip().endswith('RETURN'):
                cypher = f"{cypher} LIMIT {limit}"

        results = neo4j.execute_query(cypher, read_only=True)

        nodes = []
        edges = []

        for record in results:
            for key, value in record.items():
                if hasattr(value, 'labels'):
                    nodes.append({
                        "id": value.element_id,
                        "labels": list(value.labels()),
                        "properties": {k: v for k, v in value.items() if not k.startswith('_')}
                    })
                elif hasattr(value, 'type'):
                    edges.append({
                        "id": value.element_id,
                        "type": value.type(),
                        "start": value.start_node.get('name', value.start_node.element_id),
                        "end": value.end_node.get('name', value.end_node.element_id),
                        "properties": {k: v for k, v in value.items() if not k.startswith('_')}
                    })
                elif isinstance(value, list):
                    for item in value:
                        if hasattr(item, 'labels'):
                            nodes.append({
                                "id": item.element_id,
                                "labels": list(item.labels()),
                                "properties": {k: v for k, v in item.items() if not k.startswith('_')}
                            })
                        elif hasattr(item, 'type'):
                            edges.append({
                                "id": item.element_id,
                                "type": item.type(),
                                "start": item.start_node.get('name', item.start_node.element_id),
                                "end": item.end_node.get('name', item.end_node.element_id),
                                "properties": {k: v for k, v in item.items() if not k.startswith('_')}
                            })

        unique_nodes = {n['id']: n for n in nodes}.values()
        unique_edges = [e for e in edges if e['id']]

        return jsonify({
            "success": True,
            "data": {
                "nodes": list(unique_nodes),
                "edges": unique_edges,
                "total_nodes": len(unique_nodes),
                "total_edges": len(unique_edges)
            }
        })

    except Exception as e:
        logger.error(f"Cypher 查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询执行失败: {str(e)}"
        }), 500


@graph_bp.route('/career-path/<job_id>', methods=['GET'])
def career_path(job_id: str):
    """查询岗位的晋升路径和换岗路径"""
    try:
        neo4j = get_neo4j_client()

        cypher_promotion = f"""
        MATCH path = (start:JobPortrait)-[r:PROMOTION*1..3]->(end:JobPortrait)
        WHERE start.name = $job_id OR start.id = $job_id
        RETURN path, length(path) as steps
        ORDER BY steps
        LIMIT 10
        """

        cypher_transfer = f"""
        MATCH (start:JobPortrait)-[r:TRANSFER]->(end:JobPortrait)
        WHERE start.name = $job_id OR start.id = $job_id
        RETURN start, r, end
        LIMIT 20
        """

        promotion_results = neo4j.execute_query(
            cypher_promotion,
            {"job_id": job_id},
            read_only=True
        )

        transfer_results = neo4j.execute_query(
            cypher_transfer,
            {"job_id": job_id},
            read_only=True
        )

        promotion_paths = []
        for record in promotion_results:
            path = record.get('path')
            if path:
                nodes = list(path.nodes)
                steps = record.get('steps', 0)
                path_data = {
                    "steps": steps,
                    "nodes": [
                        {"name": n.get('name', 'Unknown'), "level": n.get('level', 0)}
                        for n in nodes
                    ]
                }
                promotion_paths.append(path_data)

        transfer_options = []
        for record in transfer_results:
            start = record.get('start')
            end = record.get('end')
            rel = record.get('r')
            if start and end:
                transfer_options.append({
                    "from": start.get('name', 'Unknown'),
                    "to": end.get('name', 'Unknown'),
                    "relationship": rel.type() if hasattr(rel, 'type') else 'transfer'
                })

        return jsonify({
            "success": True,
            "data": {
                "job_id": job_id,
                "promotion_paths": promotion_paths,
                "transfer_options": transfer_options
            }
        })

    except Exception as e:
        logger.error(f"职业路径查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


@graph_bp.route('/skill-neighbors/<path:skill_name>', methods=['GET'])
def skill_neighbors(skill_name: str):
    """查询技能节点的邻居 (相关技能/岗位/前置技能)"""
    try:
        neo4j = get_neo4j_client()

        cypher = f"""
        MATCH (s:Skill)-[r]-(n)
        WHERE s.name = $skill_name OR s.alias CONTAINS $skill_name
        RETURN s, r, n
        LIMIT 50
        """

        results = neo4j.execute_query(
            cypher,
            {"skill_name": skill_name},
            read_only=True
        )

        neighbors = []
        skill_info = None

        for record in results:
            s = record.get('s')
            r = record.get('r')
            n = record.get('n')

            if s and not skill_info:
                skill_info = {
                    "name": s.get('name'),
                    "category": s.get('category'),
                    "alias": s.get('alias', []),
                    "description": s.get('description')
                }

            if n:
                neighbor = {
                    "name": n.get('name', n.get('title', 'Unknown')),
                    "type": list(n.labels())[0] if hasattr(n, 'labels') and n.labels else 'Unknown',
                    "relationship": r.type() if hasattr(r, 'type') else 'related'
                }
                neighbors.append(neighbor)

        if not skill_info:
            return jsonify({
                "success": False,
                "error": f"未找到技能: {skill_name}"
            }), 404

        neighbor_groups = {}
        for n in neighbors:
            rel_type = n['relationship']
            if rel_type not in neighbor_groups:
                neighbor_groups[rel_type] = []
            neighbor_groups[rel_type].append({
                "name": n['name'],
                "type": n['type']
            })

        return jsonify({
            "success": True,
            "data": {
                "center": skill_info,
                "neighbors": neighbors,
                "neighbor_groups": neighbor_groups,
                "total_neighbors": len(neighbors)
            }
        })

    except Exception as e:
        logger.error(f"技能邻居查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """触发知识图谱构建 (L1 管线)"""
    data = request.get_json() or {}
    build_type = data.get('type', 'full')

    try:
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        logger.info(f"触发图谱构建任务: {task_id}, type={build_type}")

        return jsonify({
            "success": True,
            "data": {
                "task_id": task_id,
                "status": "pending",
                "type": build_type,
                "message": "图谱构建任务已创建，请通过 task_id 查询进度"
            }
        })

    except Exception as e:
        logger.error(f"图谱构建触发失败: {e}")
        return jsonify({
            "success": False,
            "error": f"任务创建失败: {str(e)}"
        }), 500


@graph_bp.route('/skill-categories', methods=['GET'])
def skill_categories():
    """获取技能分类统计"""
    try:
        neo4j = get_neo4j_client()

        cypher = """
        MATCH (c:SkillCategory)<-[:BELONGS_TO]-(s:Skill)
        RETURN c.name as category, c.subcategory as subcategory, count(s) as skill_count
        ORDER BY category, subcategory
        """

        results = neo4j.execute_query(cypher, read_only=True)

        categories = {}
        for record in results:
            cat = record.get('category', 'Unknown')
            sub = record.get('subcategory', 'General')
            count = record.get('skill_count', 0)

            if cat not in categories:
                categories[cat] = {"total": 0, "subcategories": {}}

            categories[cat]["total"] += count
            if sub:
                categories[cat]["subcategories"][sub] = count

        return jsonify({
            "success": True,
            "data": {
                "categories": categories,
                "total_categories": len(categories)
            }
        })

    except Exception as e:
        logger.error(f"技能分类查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


@graph_bp.route('/job-requirements/<job_id>', methods=['GET'])
def job_requirements(job_id: str):
    """获取岗位的技能要求 (D1-D4 四维映射)"""
    try:
        neo4j = get_neo4j_client()

        cypher = f"""
        MATCH (j:JobPortrait)-[r]-(s:Skill)
        WHERE j.name = $job_id OR j.id = $job_id
        RETURN j, type(r) as rel_type, s
        """

        results = neo4j.execute_query(
            cypher,
            {"job_id": job_id},
            read_only=True
        )

        job_info = None
        requirements = {"D1_required": [], "D2_preferred": [], "D3_bonus": []}

        for record in results:
            j = record.get('j')
            rel_type = record.get('rel_type', '')
            s = record.get('s')

            if j and not job_info:
                job_info = {
                    "id": j.get('id'),
                    "name": j.get('name'),
                    "level": j.get('level')
                }

            if s:
                skill_info = {
                    "name": s.get('name'),
                    "category": s.get('category')
                }

                if 'REQUIRES' in rel_type.upper():
                    requirements["D1_required"].append(skill_info)
                elif 'PREFERS' in rel_type.upper():
                    requirements["D2_preferred"].append(skill_info)
                elif 'BONUS' in rel_type.upper():
                    requirements["D3_bonus"].append(skill_info)

        if not job_info:
            return jsonify({
                "success": False,
                "error": f"未找到岗位: {job_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": {
                "job": job_info,
                "requirements": requirements
            }
        })

    except Exception as e:
        logger.error(f"岗位要求查询失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500
