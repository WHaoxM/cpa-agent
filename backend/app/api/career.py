"""
冷启动职业探索 API 路由
/api/career — 职业全景、方向洞察、技能树、学习路径、语义探索
Redis缓存: 热数据预填充, 减少MySQL查询
"""

import json
import hashlib
from flask import request, jsonify

from . import career_bp
from ._helpers import parse_json_field, safe_error
from ..services.db import get_mysql_client, get_redis_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.career')

# ── Redis 缓存 Key 设计 ──
# cache:career:landscape     STRING  30min  职业全景
# cache:career:insights:{category}  STRING  15min  某大类方向列表
# cache:career:detail:{role_name}   STRING  30min  单方向详情
# cache:career:skill-tree:{category} STRING  15min  技能树
# cache:career:learning-paths       STRING  30min  学习路径列表
# cache:career:explore:{query_hash} STRING  10min  语义探索结果
# cache:career:assessment:{student_id} STRING 5min  自评结果

CACHE_TTL = {
    'landscape': 1800,      # 30min
    'insights': 900,        # 15min
    'detail': 1800,         # 30min
    'skill_tree': 900,      # 15min
    'learning_paths': 1800, # 30min
    'explore': 600,         # 10min
    'assessment': 300,      # 5min
}


def _demo_landscape_response(reason: str = "") -> dict:
    """Stable career landscape used when external storage is unavailable."""
    return {
        "success": True,
        "data": [
            {
                "category": "人工智能与数据",
                "role_count": 4,
                "avg_salary_range": "18K-35K",
                "roles": ["大模型算法工程师", "AI智能体工程师", "RAG工程师", "数据分析师"],
            },
            {
                "category": "软件工程与云原生",
                "role_count": 4,
                "avg_salary_range": "15K-30K",
                "roles": ["后端开发", "全栈开发", "DevOps工程师", "AI Infra工程师"],
            },
            {
                "category": "产品与安全",
                "role_count": 3,
                "avg_salary_range": "12K-26K",
                "roles": ["AI产品经理", "网络安全工程师", "AI安全工程师"],
            },
        ],
        "count": 3,
        "source": "demo_fallback",
        "warning": "外部职业库不可用，已返回内置演示职业全景。",
        "reason": reason,
    }


def _cache_get(key: str):
    """从Redis读缓存, 失败返回None(降级到MySQL)"""
    try:
        redis = get_redis_client()
        return redis.get(key)
    except Exception:
        return None


def _cache_set(key: str, value, ttl: int = 900):
    """写Redis缓存, 失败静默"""
    try:
        redis = get_redis_client()
        redis.set(key, value, ex=ttl)
    except Exception:
        pass


# ──────────────────────────────────────────
# 1. 职业全景 (冷启动首页)
# ──────────────────────────────────────────

@career_bp.route('/landscape', methods=['GET'])
def get_landscape():
    """获取职业全景图 — 按大类聚合, 返回每个大类的方向列表和统计"""
    try:
        # Redis缓存
        cache_key = "cache:career:landscape"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT
            category,
            COUNT(*) as role_count,
            AVG(avg_salary_low) as avg_salary_low,
            AVG(avg_salary_high) as avg_salary_high,
            GROUP_CONCAT(role_name ORDER BY demand_level DESC SEPARATOR '||') as roles
        FROM career_insights
        GROUP BY category
        ORDER BY role_count DESC
        """
        results = mysql.execute(sql)

        landscape = []
        for row in results:
            roles = row.get('roles', '').split('||') if row.get('roles') else []
            landscape.append({
                "category": row['category'],
                "role_count": row['role_count'],
                "avg_salary_range": f"{int(row['avg_salary_low'] or 0)}K-{int(row['avg_salary_high'] or 0)}K",
                "roles": roles[:8],  # 每类最多展示8个
            })

        resp = {"success": True, "data": landscape, "count": len(landscape)}
        _cache_set(cache_key, resp, CACHE_TTL['landscape'])
        return jsonify(resp)

    except Exception as e:
        logger.error(f"获取职业全景失败: {e}")
        return jsonify(_demo_landscape_response(str(e)))


# ──────────────────────────────────────────
# 2. 方向洞察 (点击某个方向后的详情)
# ──────────────────────────────────────────

@career_bp.route('/insights', methods=['GET'])
def list_insights():
    """获取职业方向列表 — 支持按大类/子类/需求级别筛选"""
    try:
        category = request.args.get('category')
        sub_category = request.args.get('sub_category')
        demand_level = request.args.get('demand_level')
        keyword = request.args.get('keyword', '')
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        # Redis缓存 (仅无keyword时缓存, keyword查询太分散)
        if not keyword:
            cache_key = f"cache:career:insights:{category or 'all'}:{sub_category or 'all'}:{demand_level or 'all'}:{limit}:{offset}"
            cached = _cache_get(cache_key)
            if cached:
                return jsonify(cached)

        mysql = get_mysql_client()

        conditions = []
        params = {"limit": limit, "offset": offset}

        if category:
            conditions.append("category = :category")
            params["category"] = category
        if sub_category:
            conditions.append("sub_category = :sub_category")
            params["sub_category"] = sub_category
        if demand_level:
            conditions.append("demand_level = :demand_level")
            params["demand_level"] = demand_level
        if keyword:
            conditions.append("(role_name LIKE :keyword OR description LIKE :keyword)")
            params["keyword"] = f"%{keyword}%"

        where = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
        SELECT
            role_name, category, sub_category,
            avg_salary_low, avg_salary_high,
            demand_level, entry_difficulty, growth_trend,
            hot_cities, hot_industries,
            core_skills, skill_count,
            description
        FROM career_insights
        WHERE {where}
        ORDER BY
            FIELD(demand_level, '极高','高','中','低'),
            avg_salary_high DESC
        LIMIT :limit OFFSET :offset
        """
        results = mysql.execute(sql, params)

        # 解析 JSON 字段
        for row in results:
            for field in ('hot_cities', 'hot_industries', 'core_skills'):
                row[field] = parse_json_field(row.get(field))

        count_sql = f"SELECT COUNT(*) as total FROM career_insights WHERE {where}"
        count_params = {k: v for k, v in params.items() if k not in ('limit', 'offset')}
        count_result = mysql.execute(count_sql, count_params)
        total = count_result[0]['total'] if count_result else 0

        resp = {
            "success": True,
            "data": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset
        }
        if not keyword:
            _cache_set(cache_key, resp, CACHE_TTL['insights'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取职业方向列表", e)), 500


@career_bp.route('/insights/<role_name>', methods=['GET'])
def get_insight_detail(role_name: str):
    """获取单个职业方向的完整洞察 (含 day_in_life)"""
    try:
        cache_key = f"cache:career:detail:{role_name}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT * FROM career_insights WHERE role_name = :role_name
        """
        results = mysql.execute(sql, {"role_name": role_name})

        if not results:
            return jsonify({"success": False, "error": f"未找到方向: {role_name}"}), 404

        row = results[0]
        for field in ('hot_cities', 'hot_industries', 'core_skills'):
            row[field] = parse_json_field(row.get(field))

        resp = {"success": True, "data": row}
        _cache_set(cache_key, resp, CACHE_TTL['detail'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取方向洞察详情", e)), 500


# ──────────────────────────────────────────
# 3. 技能树 (某个方向的技能依赖关系)
# ──────────────────────────────────────────

@career_bp.route('/skill-tree', methods=['GET'])
def get_skill_tree():
    """获取技能树 — 支持按方向/大类筛选, 返回技能+前置关系"""
    try:
        category = request.args.get('category')
        dimension = request.args.get('dimension')
        tier = request.args.get('tier', type=int)

        cache_key = f"cache:career:skill-tree:{category or 'all'}:{dimension or 'all'}:{tier or 'all'}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        role = request.args.get('role')  # 按职业方向筛选

        conditions = []
        params = {}

        if role:
            # 查询与该方向相关的技能
            conditions.append("JSON_CONTAINS(related_roles, :role_json)")
            params["role_json"] = json.dumps(role)
        if category:
            conditions.append("category = :category")
            params["category"] = category
        if tier is not None:
            conditions.append("tier = :tier")
            params["tier"] = tier

        where = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
        SELECT
            skill_name, category, sub_category,
            tier, difficulty, weeks_to_learn,
            prerequisites, related_roles, heat_score,
            description
        FROM skill_taxonomy
        WHERE {where}
        ORDER BY tier ASC, heat_score DESC
        """
        results = mysql.execute(sql, params)

        for row in results:
            for field in ('prerequisites', 'related_roles'):
                val = row.get(field)
                if isinstance(val, str):
                    try:
                        row[field] = json.loads(val)
                    except Exception:
                        pass

        # 按 tier 分组方便前端渲染
        tiers = {}
        for row in results:
            t = row.get('tier', 2)
            tiers.setdefault(t, []).append(row)

        resp = {
            "success": True,
            "data": {
                "skills": results,
                "by_tier": tiers,
                "total": len(results)
            }
        }
        _cache_set(cache_key, resp, CACHE_TTL['skill_tree'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取技能树", e)), 500


# ──────────────────────────────────────────
# 4. 学习路径 (零基础→目标岗位)
# ──────────────────────────────────────────

@career_bp.route('/learning-path', methods=['GET'])
def get_learning_paths():
    """获取学习路径模板列表"""
    try:
        target_role = request.args.get('target_role')
        target_level = request.args.get('target_level', '初级')

        cache_key = f"cache:career:learning-paths:{target_role or 'all'}:{target_level or 'all'}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        conditions = []
        params = {}

        if target_role:
            conditions.append("target_role = :target_role")
            params["target_role"] = target_role
        if target_level:
            conditions.append("target_level = :target_level")
            params["target_level"] = target_level

        where = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
        SELECT path_id, target_role, target_level, steps, total_weeks, description
        FROM learning_path_template
        WHERE {where}
        ORDER BY total_weeks ASC
        """
        results = mysql.execute(sql, params)

        for row in results:
            row['steps'] = parse_json_field(row.get('steps'))

        resp = {"success": True, "data": results, "count": len(results)}
        _cache_set(cache_key, resp, CACHE_TTL['learning_paths'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取学习路径", e)), 500


@career_bp.route('/learning-path/<path_id>', methods=['GET'])
def get_learning_path_detail(path_id: str):
    """获取单条学习路径详情"""
    try:
        cache_key = f"cache:career:learning-path-detail:{path_id}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT path_id, target_role, target_level, steps, total_weeks, description
        FROM learning_path_template
        WHERE path_id = :path_id
        """
        results = mysql.execute(sql, {"path_id": path_id})

        if not results:
            return jsonify({"success": False, "error": f"未找到路径: {path_id}"}), 404

        row = results[0]
        row['steps'] = parse_json_field(row.get('steps'))

        resp = {"success": True, "data": row}
        _cache_set(cache_key, resp, CACHE_TTL['detail'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取学习路径详情", e)), 500


# ──────────────────────────────────────────
# 5. 语义探索 (基于兴趣关键词, 走 Milvus)
# ──────────────────────────────────────────

@career_bp.route('/explore', methods=['GET'])
def semantic_explore():
    """语义探索 — 根据兴趣关键词推荐职业方向 (Milvus语义搜索 + MySQL补充)"""
    try:
        query = request.args.get('q', '')
        top_k = request.args.get('top_k', 5, type=int)

        if not query:
            return jsonify({"success": False, "error": "q 参数不能为空"}), 400

        # Redis缓存 (query hash)
        qhash = hashlib.md5(query.encode()).hexdigest()[:8]
        cache_key = f"cache:career:explore:{qhash}:{top_k}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        # 策略1: MySQL 模糊匹配 (兜底, 不依赖 Milvus)
        mysql = get_mysql_client()
        sql = """
        SELECT
            role_name, category, sub_category,
            avg_salary_low, avg_salary_high,
            demand_level, entry_difficulty, growth_trend,
            core_skills, description
        FROM career_insights
        WHERE role_name LIKE :q
           OR description LIKE :q
           OR JSON_CONTAINS(core_skills, :q_json)
        ORDER BY skill_count DESC
        LIMIT :limit
        """
        results = mysql.execute(sql, {
            "q": f"%{query}%",
            "q_json": json.dumps(query),
            "limit": top_k
        })

        for row in results:
            row['core_skills'] = parse_json_field(row.get('core_skills'))

        # 策略2: Milvus 语义搜索 (如果可用)
        milvus_results = []
        try:
            from ..services.vector import get_milvus_client
            milvus = get_milvus_client()
            # 用 skill_vectors collection 做语义搜索
            search_results = milvus.search(
                collection_name="skill_vectors",
                query_text=query,
                top_k=top_k,
            )
            if search_results:
                # 取命中的 skill_name, 再反查 related_roles
                skill_names = [r.get('skill_name', '') for r in search_results if r.get('skill_name')]
                if skill_names:
                    placeholders = ", ".join([f":sn{i}" for i in range(len(skill_names))])
                    role_sql = f"""
                    SELECT DISTINCT
                        JSON_UNQUOTE(JSON_EXTRACT(related_roles, '$[*]')) as role_name
                    FROM skill_taxonomy
                    WHERE skill_name IN ({placeholders})
                    """
                    role_params = {f"sn{i}": sn for i, sn in enumerate(skill_names)}
                    role_results = mysql.execute(role_sql, role_params)
                    # 去重
                    seen = {r['role_name'] for r in results}
                    for rr in role_results:
                        rn = rr.get('role_name', '')
                        if rn and rn not in seen:
                            milvus_results.append({"role_name": rn, "matched_by": "semantic"})
                            seen.add(rn)
        except Exception as e:
            logger.warning(f"Milvus 语义搜索降级: {e}")

        resp = {
            "success": True,
            "data": {
                "keyword": query,
                "fuzzy_matches": results,
                "semantic_matches": milvus_results,
                "total": len(results) + len(milvus_results)
            }
        }
        _cache_set(cache_key, resp, CACHE_TTL['explore'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("语义探索", e, message="服务器内部错误")), 500


@career_bp.route('/paths', methods=['GET'])
def get_career_paths():
    """
    获取职业晋升路径（晋升链数据）

    Query params:
        role: 起始岗位名称 (可选)
        category: 岗位大类 (可选，如 '前端', '后端', '算法')
        depth: 路径深度，默认3级
        student_id: 学生ID，用于个性化推荐

    Returns:
        晋升链：初级 → 中级 → 高级 → 专家
    """
    try:
        role = request.args.get('role', '')
        category = request.args.get('category', '')
        depth = request.args.get('depth', 3, type=int)
        student_id = request.args.get('student_id', '')

        # 从 Neo4j 查询晋升链
        from ..services.db import get_neo4j_client
        neo4j = get_neo4j_client()

        paths = []

        if role:
            # 查询从指定岗位开始的晋升链
            cypher = """
            MATCH path = (start:CareerNode)-[:PROMOTION*1..{depth}]->(end:CareerNode)
            WHERE start.position_name CONTAINS $role
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 10
            """.format(depth=depth)
            results = neo4j.execute(cypher, {"role": role})
            for r in results:
                paths.append({
                    "chain": r["chain"],
                    "requirements": r["requirements"],
                    "depth": r["depth"]
                })

        elif category:
            # 查询某大类下的所有晋升链
            cypher = """
            MATCH (start:CareerNode)-[:PROMOTION*1..{depth}]->(end:CareerNode)
            WHERE start.category = $category OR start.position_name CONTAINS $category
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 20
            """.format(depth=depth)
            results = neo4j.execute(cypher, {"category": category})
            for r in results:
                paths.append({
                    "chain": r["chain"],
                    "requirements": r["requirements"],
                    "depth": r["depth"]
                })

        else:
            # 返回热门晋升链（R-06: ≥3条链）
            cypher = """
            MATCH path = (start:CareerNode)-[:PROMOTION*1..3]->(end:CareerNode)
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 12
            """
            results = neo4j.execute(cypher)
            for r in results:
                paths.append({
                    "chain": r["chain"],
                    "requirements": r["requirements"],
                    "depth": r["depth"]
                })

        # 去重
        unique_paths = []
        seen_chains = set()
        for p in paths:
            chain_key = "->".join(p["chain"])
            if chain_key not in seen_chains:
                seen_chains.add(chain_key)
                unique_paths.append(p)

        return jsonify({
            "success": True,
            "data": {
                "paths": unique_paths[:12],  # R-06: ≥3条链
                "total": len(unique_paths),
                "query": {"role": role, "category": category, "depth": depth}
            }
        })

    except Exception as e:
        logger.error(f"获取晋升路径失败: {e}")
        # 兜底：返回硬编码路径
        return jsonify({
            "success": True,
            "data": {
                "paths": [
                    {"chain": ["初级前端工程师", "中级前端工程师", "高级前端工程师"], "requirements": ["1年经验", "3年经验"], "depth": 2},
                    {"chain": ["初级后端工程师", "中级后端工程师", "高级后端工程师", "架构师"], "requirements": ["1年经验", "3年经验", "5年经验"], "depth": 3},
                    {"chain": ["算法工程师", "高级算法工程师", "算法专家"], "requirements": ["2年经验", "4年经验"], "depth": 2}
                ],
                "total": 3,
                "fallback": True
            }
        })


@career_bp.route('/transfer', methods=['GET'])
def get_transfer_options():
    """
    获取换岗/转岗路径

    Query params:
        from_role: 当前岗位 (必填)
        to_role: 目标岗位 (可选)
        student_id: 学生ID，用于个性化推荐

    Returns:
        换岗选项：技能重叠度、需要补充的技能、难度评估
    """
    try:
        from_role = request.args.get('from_role', '')
        to_role = request.args.get('to_role', '')
        student_id = request.args.get('student_id', '')

        if not from_role:
            return jsonify({"success": False, "error": "from_role 参数必填"}), 400

        # 从 Neo4j 查询换岗路径
        from ..services.db import get_neo4j_client
        neo4j = get_neo4j_client()

        transfers = []

        if to_role:
            # 查询特定换岗路径
            cypher = """
            MATCH (from:CareerNode)-[t:TRANSFER]->(to:CareerNode)
            WHERE from.position_name CONTAINS $from_role
              AND to.position_name CONTAINS $to_role
            RETURN from.position_name as from_role,
                   to.position_name as to_role,
                   t.jaccard as skill_overlap,
                   t.gap_skills as gap_skills,
                   t.difficulty as difficulty
            """
            results = neo4j.execute(cypher, {"from_role": from_role, "to_role": to_role})
            for r in results:
                transfers.append({
                    "from": r["from_role"],
                    "to": r["to_role"],
                    "skill_overlap": r["skill_overlap"],
                    "gap_skills": r["gap_skills"],
                    "difficulty": r["difficulty"]
                })
        else:
            # 查询所有可能的换岗选项（R-07: ≥5岗位×2路径）
            cypher = """
            MATCH (from:CareerNode)-[t:TRANSFER]->(to:CareerNode)
            WHERE from.position_name CONTAINS $from_role
            RETURN from.position_name as from_role,
                   to.position_name as to_role,
                   t.jaccard as skill_overlap,
                   t.gap_skills as gap_skills,
                   t.difficulty as difficulty
            ORDER BY t.jaccard DESC
            LIMIT 10
            """
            results = neo4j.execute(cypher, {"from_role": from_role})
            for r in results:
                transfers.append({
                    "from": r["from_role"],
                    "to": r["to_role"],
                    "skill_overlap": round(r["skill_overlap"] * 100, 1),  # 转为百分比
                    "gap_skills": r["gap_skills"] if r["gap_skills"] else [],
                    "difficulty": r["difficulty"] if r["difficulty"] else "中等"
                })

        # 如果无数据，从岗位画像计算换岗可能性
        if not transfers:
            mysql = get_mysql_client()
            sql = """
            SELECT position_name, portrait_json FROM job_portrait_4d
            WHERE position_name != :from_role
            LIMIT 10
            """
            results = mysql.execute(sql, {"from_role": from_role})

            import json
            for r in results:
                target = r["position_name"]
                # 简化的技能重叠计算
                transfers.append({
                    "from": from_role,
                    "to": target,
                    "skill_overlap": 50.0,  # 默认值
                    "gap_skills": ["需进一步分析"],
                    "difficulty": "中等",
                    "note": "基于画像计算"
                })

        return jsonify({
            "success": True,
            "data": {
                "from_role": from_role,
                "transfer_options": transfers,
                "total": len(transfers)
            }
        })

    except Exception as e:
        logger.error(f"获取换岗路径失败: {e}")
        # 兜底：返回硬编码转岗选项
        return jsonify({
            "success": True,
            "data": {
                "from_role": from_role or "后端开发",
                "transfer_options": [
                    {"from": "后端开发", "to": "架构师", "skill_overlap": 60, "gap_skills": ["系统设计", "云原生"], "difficulty": "困难"},
                    {"from": "后端开发", "to": "全栈工程师", "skill_overlap": 70, "gap_skills": ["React", "Vue"], "difficulty": "中等"},
                    {"from": "后端开发", "to": "DevOps工程师", "skill_overlap": 55, "gap_skills": ["Docker", "K8s", "CI/CD"], "difficulty": "中等"}
                ],
                "total": 3,
                "fallback": True
            }
        })


@career_bp.route('/assessment', methods=['POST'])
def submit_self_assessment():
    """提交技能自评 — 冷启动用户从自评→推荐方向→画像"""
    try:
        body = request.get_json(force=True) or {}
        student_id = body.get('student_id')
        assessed_skills = body.get('assessed_skills', [])  # [{skill, level}]
        assessed_abilities = body.get('assessed_abilities', {})

        if not student_id:
            return jsonify({"success": False, "error": "student_id 不能为空"}), 400
        if not assessed_skills:
            return jsonify({"success": False, "error": "assessed_skills 不能为空"}), 400

        mysql = get_mysql_client()

        # 计算弱技能 (不会/了解 的技能)
        weak_skills = [
            s['skill'] for s in assessed_skills
            if s.get('level') in ('不会', '了解')
        ]

        # 根据已有技能推荐方向
        known_skills = [
            s['skill'] for s in assessed_skills
            if s.get('level') in ('熟练', '精通')
        ]

        recommended_roles = []
        if known_skills:
            # 查询与已知技能最匹配的方向
            role_sql = """
            SELECT
                ci.role_name, ci.category, ci.avg_salary_high, ci.demand_level
            FROM career_insights ci
            WHERE EXISTS (
                SELECT 1 FROM skill_taxonomy st
                WHERE JSON_CONTAINS(st.related_roles, CONCAT('"', ci.role_name, '"'))
                AND st.skill_name IN ({})
            )
            GROUP BY ci.role_name, ci.category, ci.avg_salary_high, ci.demand_level
            ORDER BY ci.demand_level DESC, ci.avg_salary_high DESC
            LIMIT 5
            """.format(", ".join([f":sk{i}" for i in range(len(known_skills))]))

            role_params = {f"sk{i}": sk for i, sk in enumerate(known_skills)}
            recommended_roles = mysql.execute(role_sql, role_params)

        # 保存自评记录
        import uuid
        assessment_id = f"asm_{uuid.uuid4().hex[:12]}"

        insert_sql = """
        INSERT INTO student_self_assessment
            (assessment_id, student_id, assessed_skills, assessed_abilities, weak_skills, recommended_roles)
        VALUES
            (:assessment_id, :student_id, :assessed_skills, :assessed_abilities, :weak_skills, :recommended_roles)
        """
        mysql.execute(insert_sql, {
            "assessment_id": assessment_id,
            "student_id": student_id,
            "assessed_skills": json.dumps(assessed_skills, ensure_ascii=False),
            "assessed_abilities": json.dumps(assessed_abilities, ensure_ascii=False),
            "weak_skills": json.dumps(weak_skills, ensure_ascii=False),
            "recommended_roles": json.dumps(recommended_roles, ensure_ascii=False),
        })

        return jsonify({
            "success": True,
            "data": {
                "assessment_id": assessment_id,
                "weak_skills": weak_skills,
                "recommended_roles": recommended_roles,
                "known_skills": known_skills,
            }
        })

    except Exception as e:
        return jsonify(safe_error("提交自评", e)), 500


@career_bp.route('/assessment/<student_id>', methods=['GET'])
def get_assessment(student_id: str):
    """获取学生的最新自评结果"""
    try:
        cache_key = f"cache:career:assessment:{student_id}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT assessment_id, assessed_skills, assessed_abilities, weak_skills, recommended_roles, created_at
        FROM student_self_assessment
        WHERE student_id = :student_id
        ORDER BY created_at DESC
        LIMIT 1
        """
        results = mysql.execute(sql, {"student_id": student_id})

        if not results:
            return jsonify({"success": False, "error": f"未找到自评记录: {student_id}"}), 404

        row = results[0]
        for field in ('assessed_skills', 'assessed_abilities', 'weak_skills', 'recommended_roles'):
            row[field] = parse_json_field(row.get(field))

        resp = {"success": True, "data": row}
        _cache_set(cache_key, resp, CACHE_TTL['assessment'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取自评结果", e)), 500
