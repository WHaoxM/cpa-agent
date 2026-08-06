"""
知识图谱相关 API 路由
/api/graph — Neo4j 图谱查询、构建、可视化数据

安全说明:
- 所有 Cypher 查询必须使用预编译模板,不允许动态拼接
- 预编译模板定义在 SAFE_CYPHER_TEMPLATES 中
"""

import uuid
from flask import request, jsonify

from . import graph_bp
from ._helpers import safe_error
from ..services.db import get_neo4j_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.graph')

SAFE_CYPHER_TEMPLATES = {
    "skill_neighbors": {
        "cypher": """
        MATCH (s:Skill {name: $skill_name})-[r]-(n)
        RETURN s, r, n
        LIMIT 50
        """,
        "params": ["skill_name"]
    },
    "skill_by_category": {
        "cypher": """
        MATCH (s:Skill)
        WHERE s.category = $category
        RETURN s
        ORDER BY s.heat DESC
        LIMIT 50
        """,
        "params": ["category"]
    },
    "all_skills": {
        "cypher": """
        MATCH (s:Skill)
        RETURN s
        ORDER BY s.heat DESC
        LIMIT 100
        """,
        "params": []
    },
    "job_portrait": {
        "cypher": """
        MATCH (j:JobPortrait {portrait_id: $portrait_id})
        OPTIONAL MATCH (j)-[r:REQUIRES]->(s:Skill)
        OPTIONAL MATCH (j)-[r2:PREFERS]->(s2:Skill)
        OPTIONAL MATCH (j)-[r3:BONUS]->(s3:Skill)
        RETURN j,
               collect(DISTINCT {name: s.name, level: r.level}) as required_skills,
               collect(DISTINCT {name: s2.name, level: r2.level}) as preferred_skills,
               collect(DISTINCT {name: s3.name, level: r3.level}) as bonus_skills
        """,
        "params": ["portrait_id"]
    },
    "promotion_chains": {
        "cypher": """
        MATCH path = (start:JobPortrait {portrait_id: $portrait_id})-[r:PROMOTION*1..3]->(end:JobPortrait)
        RETURN path, length(path) as steps
        ORDER BY steps
        LIMIT 10
        """,
        "params": ["portrait_id"]
    },
    "transfer_options": {
        "cypher": """
        MATCH (start:JobPortrait {portrait_id: $portrait_id})-[r:TRANSFER]->(end:JobPortrait)
        RETURN start, r, end
        LIMIT 20
        """,
        "params": ["portrait_id"]
    },
    "job_requirements": {
        "cypher": """
        MATCH (j:JobPortrait {portrait_id: $portrait_id})-[r]-(s:Skill)
        RETURN j, type(r) as rel_type, s
        """,
        "params": ["portrait_id"]
    },
    "skill_categories": {
        "cypher": """
        MATCH (c:SkillCategory)<-[:BELONGS_TO]-(s:Skill)
        RETURN c.name as category, c.subcategory as subcategory, count(s) as skill_count
        ORDER BY category, subcategory
        """,
        "params": []
    },
    "career_path": {
        "cypher": """
        MATCH path=(c1:CareerNode)-[:PROMOTION*1..3]->(c2:CareerNode)
        WHERE c1.position_name = $position_name
        RETURN c1.position_name as start, c2.position_name as end, length(path) as steps
        ORDER BY steps
        LIMIT 20
        """,
        "params": ["position_name"]
    },
    "skill_prerequisite": {
        "cypher": """
        MATCH (s1:Skill)-[r:PREREQUISITE]->(s2:Skill)
        RETURN s1.name as skill, s2.name as prerequisite
        LIMIT 50
        """,
        "params": []
    },
}


def _execute_safe_query(template_name: str, params: dict) -> list:
    """执行预编译安全查询"""
    template = SAFE_CYPHER_TEMPLATES.get(template_name)
    if not template:
        raise ValueError(f"未知查询模板: {template_name}")

    required_params = set(template["params"])
    provided_params = set(params.keys())
    if required_params != provided_params:
        raise ValueError(f"参数不匹配: 需要 {required_params}, 提供 {provided_params}")

    neo4j = get_neo4j_client()
    return neo4j.execute_query(template["cypher"], params, read_only=True)


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
        return jsonify(safe_error("图谱概览查询", e)), 500


@graph_bp.route('/query', methods=['POST'])
def graph_query():
    """
    执行预定义 Cypher 查询模板

    Body:
        {
            "template": "skill_neighbors | all_skills | skill_categories | ...",
            "params": {"skill_name": "Python"},
            "limit": 100
        }
    """
    data = request.get_json() or {}
    template_name = data.get('template', '')
    params = data.get('params', {})
    limit = data.get('limit', 100)

    if not template_name:
        return jsonify({
            "success": False,
            "error": "template 参数必填"
        }), 400

    if template_name not in SAFE_CYPHER_TEMPLATES:
        return jsonify({
            "success": False,
            "error": f"未知模板: {template_name}"
        }), 400

    try:
        results = _execute_safe_query(template_name, params)

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
        return jsonify(safe_error("Cypher 查询", e, message="查询执行失败，请稍后重试")), 500


@graph_bp.route('/career-path/<job_id>', methods=['GET'])
def career_path(job_id: str):
    """查询岗位的晋升路径和换岗路径"""
    try:
        promotion_results = _execute_safe_query("promotion_chains", {"portrait_id": job_id})
        transfer_results = _execute_safe_query("transfer_options", {"portrait_id": job_id})

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
        return jsonify(safe_error("职业路径查询", e)), 500


@graph_bp.route('/skill-neighbors/<path:skill_name>', methods=['GET'])
def skill_neighbors(skill_name: str):
    """查询技能节点的邻居 (相关技能/岗位/前置技能)"""
    try:
        results = _execute_safe_query("skill_neighbors", {"skill_name": skill_name})

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
                labels = list(n.labels()) if hasattr(n, 'labels') else []
                neighbor = {
                    "name": n.get('name', n.get('title', 'Unknown')),
                    "type": labels[0] if labels else 'Unknown',
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
        return jsonify(safe_error("技能邻居查询", e)), 500


@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """触发知识图谱构建 (L1 管线)"""
    data = request.get_json() or {}
    build_type = data.get('type', 'full')
    student_id = data.get('student_id')
    jd_texts = data.get('jd_texts', [])
    profile_text = data.get('profile_text', '')

    try:
        from ..services.pipeline import PipelineRunner, PipelineLevel

        params = {
            "type": build_type,
            "student_id": student_id,
            "jd_texts": jd_texts,
            "profile_text": profile_text
        }

        runner = PipelineRunner()
        result = runner.run(PipelineLevel.L1_BUILD_GRAPH.value, params)

        return jsonify({
            "success": True,
            "data": {
                "task_id": result.get("student_id", "unknown"),
                "status": "completed",
                "type": build_type,
                "result": result,
                "message": "图谱构建完成"
            }
        })

    except Exception as e:
        return jsonify(safe_error("图谱构建触发", e, message="任务创建失败，请稍后重试")), 500


@graph_bp.route('/skill-categories', methods=['GET'])
def skill_categories():
    """获取技能分类统计"""
    try:
        results = _execute_safe_query("skill_categories", {})

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
        return jsonify(safe_error("技能分类查询", e)), 500


@graph_bp.route('/job-requirements/<job_id>', methods=['GET'])
def job_requirements(job_id: str):
    """获取岗位的技能要求 (D1-D4 四维映射)"""
    try:
        results = _execute_safe_query("job_requirements", {"portrait_id": job_id})

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
        return jsonify(safe_error("岗位要求查询", e)), 500
