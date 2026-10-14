"""
冷启动职业探索 API 路由
/api/career — 职业全景、方向洞察、技能树、学习路径、语义探索
Redis缓存: 热数据预填充, 减少MySQL查询
"""

import json
import hashlib
import re
import uuid
from flask import request, jsonify

from . import career_bp
from ._helpers import (
    ScopeError, clamp_pagination, parse_json_field, require_student_path,
    resolve_request_scope, safe_error, scope_error_response,
)
from ..services.career_role_inference import infer_roles_from_skills
from ..services.db import get_mysql_client, get_neo4j_client, get_redis_client, get_milvus_client
from ..utils.logger import get_logger

# Interest Explore contract: short keywords only — never resume blobs.
_EXPLORE_INTEREST_MAX_LEN = 64
_EXPLORE_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_EXPLORE_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_EXPLORE_URL_RE = re.compile(r"https?://|www\.", re.I)

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

# career_insights is a cold-start direction catalogue, not a reviewed market
# source. Its schema carries no source URI, observation period or geography for
# salary/demand claims. Preserve structural fields, but withhold market facts.
_UNVERIFIED_MARKET_FIELDS = (
    'avg_salary_low', 'avg_salary_high', 'demand_level', 'entry_difficulty',
    'growth_trend', 'hot_cities', 'hot_industries', 'description',
)
_MARKET_CACHE_VERSION = 'v2_unverified_market'


def _unverified_market_meta() -> dict:
    return {
        'status': 'partial',
        'market_data_status': 'unverified_seed',
        'source_coverage': {
            'career_direction_structure': 'seed_available',
            'market_claims': 'unverified_seed',
        },
        'unavailable_fields': list(_UNVERIFIED_MARKET_FIELDS),
        'next_action': 'await_verified_market_source',
    }


def _structural_career_row(row: dict) -> dict:
    projected = dict(row)
    for field in _UNVERIFIED_MARKET_FIELDS:
        projected[field] = None
    return projected


def _unavailable_landscape_response(reason: str = "") -> dict:
    """Never substitute unsourced market cards when the catalogue is down."""
    return {
        "success": True,
        "data": [],
        "count": 0,
        "source": "unavailable",
        "market_data_status": "unavailable",
        "unavailable_fields": list(_UNVERIFIED_MARKET_FIELDS),
        # Keep the legacy HTTP 200 contract, but not substitute fake facts.
        "status": "partial",
        "source_coverage": {"career_insights": "unavailable"},
        "failed_sources": ["career_insights"],
        "next_action": "retry_when_sources_available",
        "warning": "职业方向目录暂不可用，未展示未经核验的替代内容。",
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


def _cache_delete(key: str):
    """Invalidate a user-owned projection after its authoritative row changes."""
    try:
        get_redis_client().delete(key)
    except Exception:
        pass


def _cache_key(prefix: str, **parts) -> str:
    """Q31: 构造缓存 key, None 值统一为 'all'"""
    suffix = ":".join(str(v) if v is not None else 'all' for v in parts.values())
    return f"cache:career:{prefix}:{suffix}"


def _parse_json_fields(row: dict, fields) -> None:
    """Q32: 原地解析 row 中的多个 JSON 字段 (解析失败为 None)"""
    for field in fields:
        row[field] = parse_json_field(row.get(field))


# ──────────────────────────────────────────
# 1. 职业全景 (冷启动首页)
# ──────────────────────────────────────────

@career_bp.route('/landscape', methods=['GET'])
def get_landscape():
    """获取职业全景图 — 按大类聚合, 返回每个大类的方向列表和统计"""
    try:
        # Redis缓存
        cache_key = f"cache:career:landscape:{_MARKET_CACHE_VERSION}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT
            category,
            COUNT(*) as role_count,
            GROUP_CONCAT(role_name ORDER BY role_name SEPARATOR '||') as roles
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
                "avg_salary_range": "",
                "roles": roles[:8],  # 每类最多展示8个
            })

        resp = {"success": True, "data": landscape, "count": len(landscape),
                **_unverified_market_meta()}
        _cache_set(cache_key, resp, CACHE_TTL['landscape'])
        return jsonify(resp)

    except Exception as e:
        logger.error(f"获取职业全景失败: {e}")
        return jsonify(_unavailable_landscape_response(str(e)))


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
        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

        if demand_level:
            return jsonify({
                "success": False,
                "reason": "market_filter_unverified",
                "message": "需求等级暂无可核验的市场来源，不能据此筛选职业方向。",
                **_unverified_market_meta(),
            }), 409

        # Redis缓存 (仅无keyword时缓存, keyword查询太分散)
        if not keyword:
            cache_key = _cache_key(f"insights-{_MARKET_CACHE_VERSION}", category=category, sub_category=sub_category,
                                   demand_level=demand_level, limit=limit, offset=offset)
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
        if keyword:
            conditions.append("role_name LIKE :keyword")
            params["keyword"] = f"%{keyword}%"

        where = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
        SELECT
            role_name, category, sub_category,
            core_skills, skill_count
        FROM career_insights
        WHERE {where}
        ORDER BY role_name ASC
        LIMIT :limit OFFSET :offset
        """
        results = mysql.execute(sql, params)

        # 解析 JSON 字段
        for row in results:
            _parse_json_fields(row, ('core_skills',))
        results = [_structural_career_row(row) for row in results]

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
            "offset": offset,
            **_unverified_market_meta(),
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
        cache_key = _cache_key(f"detail-{_MARKET_CACHE_VERSION}", role_name=role_name)
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        mysql = get_mysql_client()

        sql = """
        SELECT id, role_name, category, sub_category, core_skills,
               skill_count, day_in_life, updated_at
        FROM career_insights WHERE role_name = :role_name
        """
        results = mysql.execute(sql, {"role_name": role_name})

        if not results:
            return jsonify({"success": False, "error": f"未找到方向: {role_name}"}), 404

        row = results[0]
        _parse_json_fields(row, ('core_skills',))

        resp = {"success": True, "data": _structural_career_row(row),
                **_unverified_market_meta()}
        _cache_set(cache_key, resp, CACHE_TTL['detail'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("获取方向洞察详情", e)), 500


def _split_day_in_life(text: str) -> list:
    """把 day_in_life 日程串拆成 responsibilities 列表（仅拆分，不注入默认文案）。"""
    if not text or not isinstance(text, str):
        return []
    parts = []
    for chunk in text.replace("->", "→").split("→"):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _build_role_intro(row: dict) -> dict:
    """Only stable seed structure may enter formal RoleIntro presentation."""
    core_skills = parse_json_field(row.get("core_skills")) or []

    return {
        "title": str(row.get("role_name") or ""),
        "summary": "",
        "responsibilities": _split_day_in_life(row.get("day_in_life") or ""),
        "requirements": [],
        "skills": core_skills if isinstance(core_skills, list) else [],
        "topRegions": [],
        "outlook": "",
    }


@career_bp.route('/role-intro', methods=['GET'])
def get_role_intro():
    """能力图谱侧栏角色简介 — 拼 career_insights；无匹配返回 data:null。"""
    try:
        role = (request.args.get("role") or "").strip()
        if not role:
            return jsonify({"success": False, "error": "role 必填"}), 400

        # FE 常用展示名（如「Vue 前端工程师」）→ 图谱包别名解析出的库内 role_name
        lookup_roles = [role]
        try:
            from ..services.ability_graph_service import resolve_ability_role
            resolved = resolve_ability_role(role)
            if resolved and resolved not in lookup_roles:
                lookup_roles.append(resolved)
        except Exception:
            pass

        mysql = get_mysql_client()
        rows = []
        for key in lookup_roles:
            rows = mysql.execute(
                """
                SELECT role_name, category, core_skills, day_in_life
                FROM career_insights
                WHERE role_name = :role OR role_name LIKE :role_like
                LIMIT 1
                """,
                {"role": key, "role_like": f"%{key}%"},
            )
            if rows:
                break

        if not rows:
            return jsonify({
                "success": True,
                "data": None,
                "message": f"role not found: {role}",
            })

        return jsonify({"success": True, "data": _build_role_intro(rows[0]),
                        **_unverified_market_meta()})
    except Exception as e:
        return jsonify(safe_error("获取角色简介", e)), 500


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

        cache_key = _cache_key("skill-tree", category=category, dimension=dimension, tier=tier)
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
                row[field] = parse_json_field(row.get(field), default=row.get(field))

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

        cache_key = _cache_key("learning-paths", target_role=target_role, target_level=target_level)
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
        cache_key = _cache_key("learning-path-detail", path_id=path_id)
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

def _mysql_fuzzy_match(query: str, top_k: int) -> list:
    """Q29: MySQL 模糊匹配职业方向 (兜底, 不依赖 Milvus)"""
    mysql = get_mysql_client()
    sql = """
    SELECT
        role_name, category, sub_category,
        core_skills, skill_count
    FROM career_insights
    WHERE role_name LIKE :q
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
    return [_structural_career_row(row) for row in results]


def _skill_name_from_hit(hit: dict) -> str:
    """Normalize skill_vectors hit → skill_name (metadata JSON or text prefix)."""
    meta = hit.get("metadata")
    if isinstance(meta, str) and meta.strip():
        try:
            meta = json.loads(meta)
        except (TypeError, json.JSONDecodeError):
            meta = {}
    if isinstance(meta, dict):
        name = (meta.get("skill_name") or "").strip()
        if name:
            return name
    text = (hit.get("text") or "").strip()
    if not text:
        return ""
    # seed format: "{skill_name}。{rich_desc}"
    for sep in ("。", ".", "\n"):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text[:64]


def _milvus_semantic_match(query: str, top_k: int, existing_results: list) -> list:
    """Q29: embed → Milvus 向量检索 → 反查 related_roles, 与 existing_results 去重.

    契约与 SemanticSearchTool / milvus_client.search 一致：只接受 query_vector，
    不接受 pymilvus 不存在的 query_text。
    """
    milvus_results = []
    try:
        from ..services.embedding_service import get_embedding_service

        embedding = get_embedding_service()
        if not embedding.is_available():
            logger.warning("Milvus 语义搜索降级: embedding 不可用")
            return milvus_results

        query_vector = embedding.encode([query])[0]
        milvus = get_milvus_client()
        if not milvus.connect():
            logger.warning("Milvus 语义搜索降级: 未连接")
            return milvus_results

        search_results = milvus.search(
            collection_name="skill_vectors",
            query_vector=query_vector,
            top_k=top_k,
        )
        if not search_results:
            return milvus_results

        skill_names = []
        seen_skills = set()
        for hit in search_results:
            name = _skill_name_from_hit(hit if isinstance(hit, dict) else {})
            if name and name not in seen_skills:
                seen_skills.add(name)
                skill_names.append(name)
        if not skill_names:
            return milvus_results

        mysql = get_mysql_client()
        placeholders = ", ".join([f":sn{i}" for i in range(len(skill_names))])
        role_sql = f"""
        SELECT skill_name, related_roles
        FROM skill_taxonomy
        WHERE skill_name IN ({placeholders})
        """
        role_params = {f"sn{i}": sn for i, sn in enumerate(skill_names)}
        role_rows = mysql.execute(role_sql, role_params)
        seen = {r.get("role_name") for r in existing_results}
        for row in role_rows or []:
            roles = parse_json_field(row.get("related_roles"))
            if not isinstance(roles, list):
                continue
            for rn in roles:
                if not isinstance(rn, str):
                    continue
                rn = rn.strip()
                if rn and rn not in seen:
                    milvus_results.append({"role_name": rn, "matched_by": "semantic"})
                    seen.add(rn)
    except Exception as e:
        logger.warning(f"Milvus 语义搜索降级: {e}")
    return milvus_results


def _validate_interest_query(query: str) -> str | None:
    """Return error message if ``q`` violates Interest Explore contract; else None."""
    q = (query or "").strip()
    if not q:
        return "q 参数不能为空"
    if len(q) > _EXPLORE_INTEREST_MAX_LEN:
        return (
            f"explore 仅接受短兴趣关键词（≤{_EXPLORE_INTEREST_MAX_LEN}字），"
            "简历方向推断请用 POST /api/career/infer-role（skills）"
        )
    if _EXPLORE_EMAIL_RE.search(q) or _EXPLORE_PHONE_RE.search(q) or _EXPLORE_URL_RE.search(q):
        return (
            "explore 不接受联系方式/链接等简历字段；"
            "请传入兴趣关键词，或对抽取技能使用 POST /api/career/infer-role"
        )
    # Dense resume paste often has many separators / bullets in a short window
    if q.count("·") + q.count("|") + q.count("；") + q.count(";") >= 3:
        return "explore 疑似收到简历片段；请改用 POST /api/career/infer-role"
    return None


@career_bp.route('/infer-role', methods=['POST'])
def infer_role_from_skills():
    """从抽取技能推断职业方向 — 与 Interest Explore 分离的 Role Inference 缝。"""
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
        skills = body.get("skills") or []
        if not isinstance(skills, list):
            return jsonify({"success": False, "error": "skills 必须为数组"}), 400
        top_k = body.get("top_k", 5)
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5
        preferred = body.get("preferred_role") or body.get("hint") or None
        if preferred is not None:
            preferred = str(preferred).strip() or None

        data = infer_roles_from_skills(
            skills,
            top_k=top_k,
            preferred_role=preferred,
        )
        return jsonify({"success": True, "status": data.get("status", "complete"), "data": data})
    except Exception as e:
        return jsonify(safe_error("职业方向推断", e, message="服务器内部错误")), 500


@career_bp.route('/explore', methods=['GET'])
def semantic_explore():
    """Interest Explore — 短兴趣关键词 → 职业方向（禁止简历原文）。"""
    try:
        query = request.args.get('q', '')
        top_k = request.args.get('top_k', 5, type=int)
        # Match Role Inference's bounded result contract before passing the
        # value to either MySQL LIMIT or the Milvus search API.
        top_k = max(1, min(top_k if top_k is not None else 5, 20))

        err = _validate_interest_query(query)
        if err:
            return jsonify({"success": False, "error": err}), 400

        query = query.strip()

        # Redis缓存 (query hash)
        qhash = hashlib.md5(query.encode()).hexdigest()[:8]
        cache_key = _cache_key(f"explore-{_MARKET_CACHE_VERSION}", qhash=qhash, top_k=top_k)
        cached = _cache_get(cache_key)
        if cached:
            return jsonify(cached)

        # Q29: 拆分为两个私有函数
        fuzzy_results = _mysql_fuzzy_match(query, top_k)
        semantic_results = _milvus_semantic_match(query, top_k, fuzzy_results)

        resp = {
            "success": True,
            "data": {
                "keyword": query,
                "fuzzy_matches": fuzzy_results,
                "semantic_matches": semantic_results,
                "total": len(fuzzy_results) + len(semantic_results)
            },
            **_unverified_market_meta(),
        }
        _cache_set(cache_key, resp, CACHE_TTL['explore'])
        return jsonify(resp)

    except Exception as e:
        return jsonify(safe_error("语义探索", e, message="服务器内部错误")), 500


def _query_promotion_chains(cypher: str, params: dict) -> list:
    """Q28: 执行晋升链 Cypher 并转换为 path dict 列表"""
    neo4j = get_neo4j_client()
    results = neo4j.execute(cypher, params)
    return [
        {
            "chain": r["chain"],
            "requirements": r["requirements"],
            "depth": r["depth"]
        }
        for r in results
    ]


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

        # Q20: depth 范围保护 (Cypher *1..{depth} 不支持参数化, 需限范围防拖垮 Neo4j)
        depth = min(max(depth, 1), 5)

        # Q28: 3 个分支统一用 _query_promotion_chains, 仅构造 cypher 和 params
        if role:
            cypher = """
            MATCH path = (start:CareerNode)-[:PROMOTION*1..{depth}]->(end:CareerNode)
            WHERE start.position_name CONTAINS $role
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 10
            """.format(depth=depth)
            paths = _query_promotion_chains(cypher, {"role": role})
        elif category:
            cypher = """
            MATCH (start:CareerNode)-[:PROMOTION*1..{depth}]->(end:CareerNode)
            WHERE start.category = $category OR start.position_name CONTAINS $category
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 20
            """.format(depth=depth)
            paths = _query_promotion_chains(cypher, {"category": category})
        else:
            # 返回热门晋升链（R-06: ≥3条链）
            cypher = """
            MATCH path = (start:CareerNode)-[:PROMOTION*1..3]->(end:CareerNode)
            RETURN [node in nodes(path) | node.position_name] as chain,
                   [rel in relationships(path) | rel.requirements] as requirements,
                   length(path) as depth
            LIMIT 12
            """
            paths = _query_promotion_chains(cypher, {})

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
        # Q25: 不再返回硬编码假路径, 返回空列表 + fallback 标记
        logger.error(f"获取晋升路径失败: {e}")
        return jsonify({
            "success": True,
            "data": {
                "paths": [],
                "total": 0,
                "query": {"role": role, "category": category, "depth": depth},
                "fallback": True,
                "message": "晋升路径数据暂不可用"
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
                    "skill_overlap": round(r["skill_overlap"] * 100, 1),  # Q21: 统一为百分比
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
        # Q25: 不再返回硬编码假转岗选项, 返回空列表 + fallback 标记
        logger.error(f"获取换岗路径失败: {e}")
        return jsonify({
            "success": True,
            "data": {
                "from_role": from_role,
                "transfer_options": [],
                "total": 0,
                "fallback": True,
                "message": "换岗路径数据暂不可用"
            }
        })


@career_bp.route('/assessment', methods=['POST'])
def submit_self_assessment():
    """提交技能自评 — 冷启动用户从自评→推荐方向→画像"""
    try:
        body = request.get_json(force=True) or {}
        student_id = resolve_request_scope().student_id
        assessed_skills = body.get('assessed_skills', [])  # [{skill, level}]
        assessed_abilities = body.get('assessed_abilities', {})

        if not isinstance(assessed_skills, list) or not assessed_skills:
            return jsonify({"success": False, "error": "assessed_skills 不能为空"}), 400
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get('skill'), str)
            or not item['skill'].strip()
            or not isinstance(item.get('level'), str)
            or not item['level'].strip()
            for item in assessed_skills
        ):
            return jsonify({"success": False, "error": "assessed_skills 每项必须包含非空 skill 和 level"}), 400

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
        assessment_id = f"asm_{uuid.uuid4().hex[:12]}"

        insert_sql = """
        INSERT INTO student_self_assessment
            (assessment_id, student_id, assessed_skills, assessed_abilities, weak_skills, recommended_roles)
        VALUES
            (:assessment_id, :student_id, :assessed_skills, :assessed_abilities, :weak_skills, :recommended_roles)
        """
        mysql.execute_write(insert_sql, {
            "assessment_id": assessment_id,
            "student_id": student_id,
            "assessed_skills": json.dumps(assessed_skills, ensure_ascii=False),
            "assessed_abilities": json.dumps(assessed_abilities, ensure_ascii=False),
            "weak_skills": json.dumps(weak_skills, ensure_ascii=False),
            "recommended_roles": json.dumps(recommended_roles, ensure_ascii=False),
        })

        persisted = mysql.execute(
            "SELECT assessment_id FROM student_self_assessment "
            "WHERE assessment_id = :assessment_id AND student_id = :student_id LIMIT 1",
            {"assessment_id": assessment_id, "student_id": student_id},
        )
        if not persisted or str(persisted[0].get("assessment_id")) != assessment_id:
            return jsonify({"success": False, "error": "自评写入后未能核验归属"}), 500
        _cache_delete(_cache_key("assessment", student_id=student_id))

        return jsonify({
            "success": True,
            "data": {
                "assessment_id": assessment_id,
                "weak_skills": weak_skills,
                "recommended_roles": recommended_roles,
                "known_skills": known_skills,
            }
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("提交自评", e)), 500


@career_bp.route('/assessment/<student_id>', methods=['GET'])
def get_assessment(student_id: str):
    """获取学生的最新自评结果"""
    try:
        require_student_path(student_id, resolve_request_scope())
        cache_key = _cache_key("assessment", student_id=student_id)
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
        _parse_json_fields(row, ('assessed_skills', 'assessed_abilities', 'weak_skills', 'recommended_roles'))

        resp = {"success": True, "data": row}
        _cache_set(cache_key, resp, CACHE_TTL['assessment'])
        return jsonify(resp)

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取自评结果", e)), 500
