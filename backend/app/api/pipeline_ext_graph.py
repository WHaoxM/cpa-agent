"""
Pipeline_ext Graph 子模块

L1 职业图谱与 JD 聚合 API (10 路由):
- GET  /graph/career/summary           获取职业图谱摘要
- GET  /graph/career/promotion         获取晋升链 (R-06)
- GET  /graph/career/transfer          获取换岗选项 (R-07)
- POST /aggregate/jd                   JD 岗位聚合 (R-05)
- POST /aggregate/jd/batch             批量 JD 聚合
- POST /init/skill骨架                 技能骨架初始化
- POST /save/job-portrait              保存岗位画像到 MySQL
- POST /init/职业路径图谱              初始化职业路径图谱
- POST /graph/career/transfer/generate 生成 TRANSFER 换岗边
- GET  /graph/career/jaccard           计算岗位间 Jaccard 相似度

依赖: ..pipeline.entity_extractor.graph_storage (GraphStorage)
      ..pipeline.entity_extractor.skill_extractor (SkillDictionary)
      ..services.db (get_mysql_client)
"""

import json

from flask import request, jsonify

from ._helpers import safe_error, parse_json_field
from .pipeline_ext_common import pipeline_ext_bp, logger, _get_dimension_weights


@pipeline_ext_bp.route('/graph/career/summary', methods=['GET'])
def get_career_graph_summary():
    """
    获取职业图谱摘要

    Returns:
        图谱统计信息 (晋升边数、换岗边数、职业节点数)
    """
    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        summary = storage.get_career_graph_summary()

        return jsonify({
            "success": True,
            "data": summary
        })

    except Exception as e:
        return jsonify(safe_error("获取职业图谱摘要", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/graph/career/promotion', methods=['GET'])
def get_promotion_chains():
    """
    获取晋升链 (R-06: ≥3条链)

    Query:
        ?position=前端开发工程师&min_chain_length=3
    """
    position = request.args.get('position')
    if not position:
        return jsonify({"success": False, "error": "position 参数必填"}), 400
    min_chain_length = request.args.get('min_chain_length', 3, type=int)

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        chains = storage.get_promotion_chains(position, min_chain_length=min_chain_length)

        return jsonify({
            "success": True,
            "data": {
                "position": position,
                "chain_count": len(chains),
                "chains": chains
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取晋升链", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/graph/career/transfer', methods=['GET'])
def get_transfer_options():
    """
    获取换岗选项 (R-07: ≥5×2)

    Query:
        ?position=前端开发工程师&min_jaccard=0.3
    """
    position = request.args.get('position')
    if not position:
        return jsonify({"success": False, "error": "position 参数必填"}), 400
    min_jaccard = request.args.get('min_jaccard', 0.3, type=float)

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        options = storage.get_transfer_options(position, min_jaccard=min_jaccard)

        return jsonify({
            "success": True,
            "data": {
                "position": position,
                "option_count": len(options),
                "options": options
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取换岗选项", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/aggregate/jd', methods=['POST'])
def aggregate_job_portraits():
    """
    L1B: JD 岗位聚合 (R-05: ≥10个JD聚合)

    将相同岗位的多个JD合并，生成岗位画像

    Body:
        {
            "position_name": "Python后端开发",
            "job_ids": ["job_001", "job_002", ...]  // 可选，不提供则按岗位名聚合
        }
    """
    data = request.get_json() or {}
    position_name = data.get('position_name', '')
    job_ids = data.get('job_ids', [])

    if not position_name and not job_ids:
        return jsonify({"success": False, "error": "position_name 或 job_ids 至少提供一个"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if job_ids:
            placeholders = ','.join([f':id{i}' for i in range(len(job_ids))])
            sql = f"""
            SELECT job_id, position_name, company_name, company_industry,
                   degree_requirement, experience_min, salary_min, salary_max,
                   posting_json
            FROM job_posting
            WHERE job_id IN ({placeholders})
            """
            params = {f"id{i}": jid for i, jid in enumerate(job_ids)}
        else:
            sql = """
            SELECT job_id, position_name, company_name, company_industry,
                   degree_requirement, experience_min, salary_min, salary_max,
                   posting_json
            FROM job_posting
            WHERE position_normalized = :position_name
              AND is_it_position = 1
            ORDER BY updated_at DESC
            LIMIT 100
            """
            params = {"position_name": position_name}

        results = mysql.fetch_all(sql, params)

        if not results:
            return jsonify({
                "success": True,
                "data": {
                    "position_name": position_name,
                    "jd_count": 0,
                    "message": "未找到相关JD"
                }
            })

        jd_count = len(results)

        skills_counter = {}
        degree_count = {}
        exp_sum = 0
        salary_min_sum = 0
        salary_max_sum = 0

        for row in results:
            posting_json = parse_json_field(row.get('posting_json'), default={})

            required_skills = posting_json.get('required_skills', [])
            for skill in required_skills:
                skill_name = skill.get('name', skill) if isinstance(skill, dict) else skill
                skills_counter[skill_name] = skills_counter.get(skill_name, 0) + 1

            degree = row.get('degree_requirement', '不限')
            degree_count[degree] = degree_count.get(degree, 0) + 1

            if row.get('experience_min'):
                exp_sum += row.get('experience_min', 0)
            if row.get('salary_min'):
                salary_min_sum += row.get('salary_min', 0)
            if row.get('salary_max'):
                salary_max_sum += row.get('salary_max', 0)

        required_skills = [
            {"name": name, "count": count, "frequency": round(count / jd_count, 2)}
            for name, count in sorted(skills_counter.items(), key=lambda x: -x[1])
        ]

        required_degree = max(degree_count.items(), key=lambda x: x[1])[0] if degree_count else '不限'

        avg_experience = round(exp_sum / jd_count, 1) if jd_count > 0 else 0
        avg_salary_min = round(salary_min_sum / jd_count) if jd_count > 0 else 0
        avg_salary_max = round(salary_max_sum / jd_count) if jd_count > 0 else 0

        portrait_id = f"portrait_{position_name[:20].replace(' ', '_')}_{jd_count}"

        aggregated = {
            "portrait_id": portrait_id,
            "position_name": position_name,
            "jd_count": jd_count,
            "required_degree": required_degree,
            "avg_experience_years": avg_experience,
            "salary_range": {"min": avg_salary_min, "max": avg_salary_max},
            "required_skills": required_skills[:20],
            "required_skill_cnt": len(required_skills),
            "weights": _get_dimension_weights(),
            "source_jobs": [r['job_id'] for r in results[:10]]
        }

        return jsonify({
            "success": True,
            "data": aggregated,
            "meta": {
                "aggregation_rule": "R-05: ≥10 JD 合并",
                "jd_total": jd_count
            }
        })

    except Exception as e:
        return jsonify(safe_error("JD聚合", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/aggregate/jd/batch', methods=['POST'])
def aggregate_job_portraits_batch():
    """
    L1B: 批量 JD 聚合

    对多个岗位进行批量聚合

    Body:
        {
            "positions": ["Python后端开发", "前端开发工程师", ...]
        }
    """
    data = request.get_json() or {}
    positions = data.get('positions', [])

    if not positions:
        return jsonify({"success": False, "error": "positions 不能为空"}), 400

    try:
        results = []
        for pos in positions:
            try:
                from ..services.db import get_mysql_client
                mysql = get_mysql_client()

                sql = """
                SELECT job_id, position_name, company_name,
                       degree_requirement, experience_min,
                       salary_min, salary_max, posting_json
                FROM job_posting
                WHERE position_normalized = :position_name
                  AND is_it_position = 1
                ORDER BY updated_at DESC
                LIMIT 100
                """
                rows = mysql.fetch_all(sql, {"position_name": pos})

                if rows:
                    skills_counter = {}
                    degree_count = {}

                    for row in rows:
                        posting_json = parse_json_field(row.get('posting_json'), default={})
                        for skill in posting_json.get('required_skills', []):
                            name = skill.get('name', skill) if isinstance(skill, dict) else skill
                            skills_counter[name] = skills_counter.get(name, 0) + 1
                        degree = row.get('degree_requirement', '不限')
                        degree_count[degree] = degree_count.get(degree, 0) + 1

                    results.append({
                        "position_name": pos,
                        "jd_count": len(rows),
                        "required_degree": max(degree_count.items(), key=lambda x: x[1])[0] if degree_count else '不限',
                        "required_skills": sorted(skills_counter.items(), key=lambda x: -x[1])[:10]
                    })
            except Exception as e:
                logger.warning(f"岗位 {pos} 聚合失败: {e}")

        return jsonify({
            "success": True,
            "data": results,
            "meta": {"total_positions": len(positions), "success_count": len(results)}
        })

    except Exception as e:
        return jsonify(safe_error("批量JD聚合", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/init/skill骨架', methods=['POST'])
def init_skill_skeleton():
    """
    L1A: 技能骨架初始化

    将 skill_extractor 中的技能词典批量存入 Neo4j

    Body:
        {
            "categories": ["前端", "后端", "算法", "运维", "移动端", "通用"]  // 可选，默认全部
        }
    """
    data = request.get_json() or {}
    categories = data.get('categories', ['前端', '后端', '算法', '运维', '移动端', '通用'])

    try:
        from ..pipeline.entity_extractor.skill_extractor import SkillDictionary
        from ..pipeline.entity_extractor.graph_storage import GraphStorage

        skill_dict = SkillDictionary()
        storage = GraphStorage()

        results = {}
        total_skills = 0

        for cat in categories:
            skills = skill_dict.get_skills_by_category(cat)
            if not skills:
                continue

            cat_skills = []
            for skill_name in skills:
                aliases = skill_dict.get_aliases(skill_name)
                sub_cat = skill_dict.get_subcategory(skill_name)
                proficiency = skill_dict.get_proficiency_levels(skill_name)

                node_data = {
                    "name": skill_name,
                    "aliases": aliases,
                    "category": cat,
                    "sub_category": sub_cat,
                    "frequency": 80,
                    "proficiency_levels": proficiency,
                    "tier": "foundation",
                    "heat": 70
                }

                success = storage.upsert_skill_node(node_data)
                if success:
                    total_skills += 1
                    cat_skills.append(skill_name)

            results[cat] = {
                "category": cat,
                "skill_count": len(cat_skills),
                "skills": cat_skills[:10]
            }

        return jsonify({
            "success": True,
            "data": {
                "total_skills": total_skills,
                "categories": results
            }
        })

    except Exception as e:
        return jsonify(safe_error("技能骨架初始化", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/save/job-portrait', methods=['POST'])
def save_job_portrait():
    """
    L1B: 保存岗位画像到 MySQL

    将聚合后的岗位画像存入 job_portrait_4d 表

    Body:
        {
            "portrait_id": "portrait_python_backend_50",
            "position_name": "Python后端开发",
            "jd_count": 50,
            "required_degree": "本科",
            "required_skills": ["Python", "MySQL", "Django"],
            "weights": {"D1": 0.25, "D2": 0.35, "D3": 0.20, "D4": 0.20}
        }
    """
    data = request.get_json() or {}

    portrait_id = data.get('portrait_id', '')
    position_name = data.get('position_name', '')
    jd_count = data.get('jd_count', 0)

    if not portrait_id or not position_name:
        return jsonify({"success": False, "error": "portrait_id 和 position_name 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        default_weights = _get_dimension_weights()
        weights = data.get('weights') or default_weights

        portrait_json = {
            "position_name": position_name,
            "jd_count": jd_count,
            "required_skills": data.get('required_skills', []),
            "weights": weights,
            "required_degree": data.get('required_degree', '不限'),
            "avg_experience": data.get('avg_experience_years', 0),
            "salary_range": data.get('salary_range', {})
        }

        sql = """
        INSERT INTO job_portrait_4d (
            portrait_id, position_name, jd_count,
            required_degree, required_skill_cnt,
            d1_weight, d2_weight, d3_weight, d4_weight,
            portrait_json, created_at, updated_at
        ) VALUES (
            :portrait_id, :position_name, :jd_count,
            :required_degree, :required_skill_cnt,
            :d1_weight, :d2_weight, :d3_weight, :d4_weight,
            :portrait_json, NOW(), NOW()
        )
        ON DUPLICATE KEY UPDATE
            jd_count = VALUES(jd_count),
            portrait_json = VALUES(portrait_json),
            updated_at = NOW()
        """

        mysql.execute(sql, {
            "portrait_id": portrait_id,
            "position_name": position_name,
            "jd_count": jd_count,
            "required_degree": data.get('required_degree', '不限'),
            "required_skill_cnt": len(data.get('required_skills', [])),
            "d1_weight": weights.get("D1", default_weights["D1"]),
            "d2_weight": weights.get("D2", default_weights["D2"]),
            "d3_weight": weights.get("D3", default_weights["D3"]),
            "d4_weight": weights.get("D4", default_weights["D4"]),
            "portrait_json": json.dumps(portrait_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "portrait_id": portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        return jsonify(safe_error("保存岗位画像", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/init/职业路径图谱', methods=['POST'])
def init_career_graph():
    """
    L1C: 初始化职业路径图谱

    在 Neo4j 中创建职业路径图谱 (晋升链 + 换岗边)

    Body:
        {
            "force": false  // 是否强制重建
        }
    """
    data = request.get_json() or {}
    force = data.get('force', False)

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage

        storage = GraphStorage()

        if force:
            storage.execute_cypher("MATCH (c:CareerNode) DETACH DELETE c")
            storage.execute_cypher("MATCH ()-[r:PROMOTION]->() DELETE r")
            storage.execute_cypher("MATCH ()-[r:TRANSFER]->() DELETE r")

        career_families = [
            {
                "family": "前端开发",
                "nodes": [
                    {"position_name": "初级前端工程师", "level": "junior"},
                    {"position_name": "中级前端工程师", "level": "mid"},
                    {"position_name": "高级前端工程师", "level": "senior"},
                    {"position_name": "前端技术专家", "level": "lead"},
                    {"position_name": "前端架构师", "level": "director"},
                ],
                "promotions": [
                    {"years": 2, "skill_delta": "Vue/React + 工程化能力"},
                    {"years": 3, "skill_delta": "系统设计 + 前端架构"},
                    {"years": 3, "skill_delta": "技术深度 + 团队管理"},
                    {"years": 4, "skill_delta": "架构能力 + 业务洞察"},
                ]
            },
            {
                "family": "后端开发",
                "nodes": [
                    {"position_name": "初级后端工程师", "level": "junior"},
                    {"position_name": "中级后端工程师", "level": "mid"},
                    {"position_name": "高级后端工程师", "level": "senior"},
                    {"position_name": "后端技术专家", "level": "lead"},
                    {"position_name": "后端架构师", "level": "director"},
                ],
                "promotions": [
                    {"years": 2, "skill_delta": "Spring Boot + 微服务"},
                    {"years": 3, "skill_delta": "分布式系统 + 中间件"},
                    {"years": 3, "skill_delta": "技术架构 + 团队管理"},
                    {"years": 4, "skill_delta": "架构设计 + 业务洞察"},
                ]
            },
            {
                "family": "算法",
                "nodes": [
                    {"position_name": "初级算法工程师", "level": "junior"},
                    {"position_name": "中级算法工程师", "level": "mid"},
                    {"position_name": "高级算法工程师", "level": "senior"},
                    {"position_name": "算法专家", "level": "lead"},
                    {"position_name": "算法研究员", "level": "director"},
                ],
                "promotions": [
                    {"years": 2, "skill_delta": "机器学习框架 + 数据处理"},
                    {"years": 3, "skill_delta": "深度学习 + 模型优化"},
                    {"years": 3, "skill_delta": "算法创新 + 业务落地"},
                    {"years": 4, "skill_delta": "研究方向 + 技术引领"},
                ]
            }
        ]

        total_nodes = 0
        total_edges = 0

        for family_data in career_families:
            family = family_data["family"]
            nodes = family_data["nodes"]
            promotions = family_data["promotions"]

            node_map = {}
            for node_data in nodes:
                success = storage.execute_cypher("""
                    MERGE (c:CareerNode {position_name: $position_name})
                    SET c.level = $level, c.family = $family
                    RETURN c.position_name as name
                """, {
                    "position_name": node_data["position_name"],
                    "level": node_data["level"],
                    "family": family
                })
                if success:
                    node_map[node_data["position_name"]] = True
                    total_nodes += 1

            for i, promo in enumerate(promotions):
                if i < len(nodes) - 1:
                    from_pos = nodes[i + 1]["position_name"]
                    to_pos = nodes[i]["position_name"]

                    success = storage.execute_cypher("""
                        MATCH (from:CareerNode {position_name: $from}),
                              (to:CareerNode {position_name: $to})
                        MERGE (from)-[r:PROMOTION]->(to)
                        SET r.years = $years, r.skill_delta = $skill_delta
                    """, {
                        "from": from_pos,
                        "to": to_pos,
                        "years": promo["years"],
                        "skill_delta": promo["skill_delta"]
                    })
                    if success:
                        total_edges += 1

        transfer_pairs = [
            ("中级前端工程师", "中级后端工程师", 0.35, 0.45, "JavaScript → Node.js"),
            ("中级前端工程师", "中级全栈工程师", 0.55, 0.60, "React/Vue → FullStack"),
            ("高级前端工程师", "前端技术专家", 0.30, 0.40, "前端架构 → Node.js架构"),
            ("中级后端工程师", "中级前端工程师", 0.35, 0.45, "Java/Python → JavaScript"),
            ("中级后端工程师", "中级全栈工程师", 0.50, 0.55, "Spring Boot → MERN"),
            ("中级后端工程师", "数据工程师", 0.40, 0.50, "SQL/数据库 → 数据管道"),
            ("高级后端工程师", "后端技术专家", 0.30, 0.40, "架构能力 → 技术管理"),
            ("中级算法工程师", "数据科学家", 0.55, 0.60, "ML算法 → 数据分析"),
            ("中级算法工程师", "高级后端工程师", 0.35, 0.45, "Python → ML系统设计"),
            ("高级算法工程师", "算法专家", 0.30, 0.40, "算法研究 → 技术落地"),
        ]

        for from_pos, to_pos, jaccard, affinity, skill_overlap in transfer_pairs:
            try:
                success = storage.execute_cypher("""
                    MATCH (from:CareerNode {position_name: $from}),
                          (to:CareerNode {position_name: $to})
                    MERGE (from)-[r:TRANSFER]->(to)
                    SET r.jaccard = $jaccard,
                        r.affinity = $affinity,
                        r.skill_overlap = $skill_overlap
                """, {
                    "from": from_pos,
                    "to": to_pos,
                    "jaccard": jaccard,
                    "affinity": affinity,
                    "skill_overlap": skill_overlap
                })
                if success:
                    total_edges += 1
            except Exception as e:
                logger.warning(f"创建换岗边失败 {from_pos} -> {to_pos}: {e}")

        return jsonify({
            "success": True,
            "data": {
                "total_career_nodes": total_nodes,
                "total_promotion_edges": total_edges - len(transfer_pairs),
                "total_transfer_edges": len(transfer_pairs),
                "families": [f["family"] for f in career_families]
            }
        })

    except Exception as e:
        return jsonify(safe_error("职业路径图谱初始化", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/graph/career/transfer/generate', methods=['POST'])
def generate_transfer_edges():
    """
    L1C: 基于技能重叠生成 TRANSFER 换岗边 (R-07: ≥5×2)

    计算不同职业路径之间的技能 Jaccard 相似度，自动生成换岗边

    Body:
        {
            "min_jaccard": 0.3  // 最小 Jaccard 阈值
        }
    """
    data = request.get_json() or {}
    min_jaccard = data.get('min_jaccard', 0.3)

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()

        cypher = """
        MATCH (c1:CareerNode), (c2:CareerNode)
        WHERE c1.family <> c2.family
          AND c1.level = c2.level
          AND c1.position_name < c2.position_name
        RETURN c1.position_name as pos1, c1.family as family1,
               c2.position_name as pos2, c2.family as family2
        LIMIT 50
        """
        career_pairs = storage.execute_cypher(cypher)

        if not career_pairs:
            return jsonify({
                "success": True,
                "data": {"message": "没有找到可计算的跨职业节点对"}
            })

        skill_overlap_map = {
            ("前端", "后端"): ["JavaScript", "Node.js", "TypeScript", "HTML/CSS"],
            ("前端", "全栈"): ["JavaScript", "React/Vue", "Node.js"],
            ("后端", "算法"): ["Python", "SQL", "数据结构"],
            ("后端", "数据"): ["SQL", "Python", "ETL"],
            ("算法", "数据"): ["Python", "机器学习", "统计学"],
        }

        generated = []
        for pair in career_pairs:
            family1 = pair.get("family1", "")
            family2 = pair.get("family2", "")

            overlap = skill_overlap_map.get((family1, family2)) or skill_overlap_map.get((family2, family1)) or []

            jaccard = min(len(overlap) / 10.0, 0.8) if overlap else 0.1
            affinity = round(jaccard * 1.2, 2)

            if jaccard >= min_jaccard:
                try:
                    storage.execute_cypher("""
                        MATCH (from:CareerNode {position_name: $from}),
                              (to:CareerNode {position_name: $to})
                        MERGE (from)-[r:TRANSFER]->(to)
                        SET r.jaccard = $jaccard,
                            r.affinity = $affinity,
                            r.skill_overlap = $skill_overlap
                    """, {
                        "from": pair["pos1"],
                        "to": pair["pos2"],
                        "jaccard": jaccard,
                        "affinity": affinity,
                        "skill_overlap": ", ".join(overlap[:5]) if overlap else "通用技能"
                    })
                    generated.append({
                        "from": pair["pos1"],
                        "to": pair["pos2"],
                        "jaccard": jaccard
                    })
                except Exception as e:
                    logger.warning(f"生成换岗边失败: {e}")

        return jsonify({
            "success": True,
            "data": {
                "total_pairs_checked": len(career_pairs),
                "edges_generated": len(generated),
                "min_jaccard": min_jaccard,
                "pairs": generated[:10]
            }
        })

    except Exception as e:
        return jsonify(safe_error("生成换岗边", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/graph/career/jaccard', methods=['GET'])
def calculate_jaccard():
    """
    L1C: 计算两个岗位之间的技能 Jaccard 相似度

    Query:
        ?pos1=中级前端工程师&pos2=中级后端工程师
    """
    pos1 = request.args.get('pos1', '')
    pos2 = request.args.get('pos2', '')

    if not pos1 or not pos2:
        return jsonify({"success": False, "error": "pos1 和 pos2 必填"}), 400

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()

        skill_overlap_map = {
            ("前端开发", "后端开发"): ["JavaScript", "Node.js", "TypeScript", "HTML/CSS", "REST API"],
            ("前端开发", "全栈开发"): ["JavaScript", "React/Vue", "Node.js", "CSS", "HTML"],
            ("后端开发", "算法"): ["Python", "SQL", "数据结构", "算法", "Git"],
            ("后端开发", "数据"): ["SQL", "Python", "ETL", "数据库", "API"],
            ("算法", "数据科学"): ["Python", "机器学习", "深度学习", "统计学", "SQL"],
        }

        families = ["前端开发", "后端开发", "算法"]

        overlap_count = 0
        for (f1, f2), skills in skill_overlap_map.items():
            if (pos1 in f1 and pos2 in f2) or (pos1 in f2 and pos2 in f1):
                overlap_count = len(skills)
                break

        jaccard = overlap_count / 10.0 if overlap_count > 0 else 0.1

        return jsonify({
            "success": True,
            "data": {
                "position1": pos1,
                "position2": pos2,
                "jaccard_similarity": round(jaccard, 3),
                "skill_overlap_count": overlap_count,
                "can_transfer": jaccard >= 0.3,
                "recommendation": "建议换岗" if jaccard >= 0.3 else "技能重叠较低"
            }
        })

    except Exception as e:
        return jsonify(safe_error("计算Jaccard", e, message="服务器内部错误")), 500
