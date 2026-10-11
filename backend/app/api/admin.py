"""
Admin API - 管理员相关 API
知识库上传 → 自动实体解析 → 意图识别 → 触发更新
"""

import json
import uuid
from enum import Enum
from flask import request, jsonify, g
from sqlalchemy import text

from . import admin_bp
from ._helpers import (
    api_handler,
    clamp_pagination,
    parse_json_field,
    safe_bad_request,
    safe_error,
    safe_not_found,
    ScopeError,
    resolve_request_scope,
    scope_error_response,
)
from ..services.db import get_mysql_client, get_neo4j_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.admin')


@admin_bp.before_request
def require_server_admin_claim():
    """Admin routes require an authenticated, server-populated privilege bit.

    Client headers, body fields and a student Principal alone cannot grant
    publication, deletion, import or graph mutation privileges.
    """
    try:
        resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    if getattr(g, "cpa_admin", False) is not True:
        return jsonify({"success": False, "error": "需要服务端管理员权限"}), 403


class OovStatus(str, Enum):
    """OOV 实体审核状态"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PipelineSourceType(str, Enum):
    """管线构建数据源"""
    DATASET = "dataset"
    KNOWLEDGE_BASE = "knowledge_base"
    UPLOAD = "upload"


# ========== 知识库管理 API ==========

@admin_bp.route('/knowledge-base', methods=['GET'])
@api_handler("获取知识库列表")
def list_knowledge_base():
    """获取知识库列表"""
    mysql = get_mysql_client()
    sql = "SELECT * FROM knowledge_base ORDER BY created_at DESC"
    return mysql.execute(sql)


@admin_bp.route('/knowledge-base/upload', methods=['POST'])
@api_handler("上传知识库")
def upload_knowledge_base():
    """上传知识库"""
    data = request.get_json() or {}
    mysql = get_mysql_client()

    kb_id = f"kb_{uuid.uuid4().hex[:12]}"
    sql = """
    INSERT INTO knowledge_base (id, title, type, status, created_at)
    VALUES (:id, :title, :type, :status, NOW())
    """

    mysql.execute_write(sql, {
        "id": kb_id,
        "title": data.get('title'),
        "type": data.get('type', 'skill'),
        "status": data.get('status', 'pending')
    })

    return {"id": kb_id, "message": "上传成功"}


@admin_bp.route('/knowledge-base/<kb_id>', methods=['DELETE'])
@api_handler("删除知识库")
def delete_knowledge_base(kb_id: str):
    """删除知识库"""
    mysql = get_mysql_client()
    sql = "DELETE FROM knowledge_base WHERE id = :kb_id"
    mysql.execute_write(sql, {"kb_id": kb_id})

    return {"message": f"知识库 {kb_id} 已删除"}


# ========== 数据集管理 API ==========

@admin_bp.route('/dataset', methods=['GET'])
@api_handler("获取数据集列表")
def list_dataset():
    """获取数据集列表"""
    mysql = get_mysql_client()
    sql = "SELECT * FROM job_dataset ORDER BY created_at DESC"
    return mysql.execute(sql)


@admin_bp.route('/dataset/upload', methods=['POST'])
@api_handler("上传数据集")
def upload_dataset():
    """上传数据集"""
    data = request.get_json() or {}
    mysql = get_mysql_client()

    ds_id = f"ds_{uuid.uuid4().hex[:12]}"
    sql = """
    INSERT INTO job_dataset (id, title, count, category, status, created_at)
    VALUES (:id, :title, :count, :category, :status, NOW())
    """

    mysql.execute_write(sql, {
        "id": ds_id,
        "title": data.get('title'),
        "count": data.get('count', 0),
        "category": data.get('category'),
        "status": data.get('status', 'pending')
    })

    return {"id": ds_id, "message": "上传成功"}


@admin_bp.route('/dataset/<ds_id>', methods=['DELETE'])
@api_handler("删除数据集")
def delete_dataset(ds_id: str):
    """删除数据集"""
    mysql = get_mysql_client()
    sql = "DELETE FROM job_dataset WHERE id = :ds_id"
    mysql.execute_write(sql, {"ds_id": ds_id})

    return {"message": f"数据集 {ds_id} 已删除"}


# ========== 管线管理 API ==========

@admin_bp.route('/pipeline/build', methods=['POST'])
def trigger_pipeline_build():
    """
    触发 L1 构建流程（从数据源构建知识图谱）

    Body:
        {
            "source": "dataset|knowledge_base|upload",
            "source_id": "数据集ID",
            "pipeline_type": "full|skills_only|jobs_only",
            "triggered_by": "管理员ID"
        }

    流程:
        1. 验证数据源
        2. 创建 pipeline_task 记录
        3. 触发异步构建（L1提取 → 归一化 → 入图）
    """
    try:
        data = request.get_json() or {}
        source = data.get('source', 'dataset')
        source_id = data.get('source_id', '')
        pipeline_type = data.get('pipeline_type', 'full')
        triggered_by = resolve_request_scope().principal_id

        if not source_id:
            return jsonify(safe_bad_request("source_id 必填")), 400

        mysql = get_mysql_client()

        # 创建任务记录
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        sql = """
        INSERT INTO pipeline_task
        (task_id, pipeline, status, params_json, triggered_by, created_at)
        VALUES (:task_id, :pipeline, 'pending', :params, :triggered_by, NOW())
        """
        mysql.execute_write(sql, {
            "task_id": task_id,
            "pipeline": f"l1_build_{pipeline_type}",
            "params": json.dumps({"source": source, "source_id": source_id}),
            "triggered_by": triggered_by
        })
        rows = mysql.fetch_all(
            "SELECT task_id, status, triggered_by FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
            {"task_id": task_id},
        )
        if (not rows or rows[0].get("task_id") != task_id
                or rows[0].get("status") != "pending"
                or rows[0].get("triggered_by") != triggered_by):
            raise RuntimeError("L1 管理任务写入未通过权威记录回读")

        # 异步任务队列接入见 issue tracker, 当前为同步占位

        return jsonify({
            "success": True,
            "data": {
                "task_id": task_id,
                "status": "pending",
                "message": "L1构建任务已创建",
                "next_steps": [
                    f"GET /api/pipeline/status/{task_id} 查询进度"
                ]
            }
        })

    except Exception as e:
        return jsonify(safe_error("触发L1构建", e)), 500


@admin_bp.route('/pipeline/status/<task_id>', methods=['GET'])
@api_handler("查询任务状态")
def get_pipeline_status(task_id: str):
    """查询管线任务状态"""
    mysql = get_mysql_client()
    sql = "SELECT * FROM pipeline_task WHERE task_id = :task_id"
    results = mysql.execute(sql, {"task_id": task_id})

    if not results:
        return jsonify(safe_not_found("任务不存在")), 404

    task = results[0]
    # pipeline_task 表无 updated_at 列, 改用 completed_at
    completed_at = task.get("completed_at")
    return {
        "task_id": task["task_id"],
        "pipeline": task["pipeline"],
        "status": task["status"],
        "params": parse_json_field(task.get("params_json"), default={}),
        "result": parse_json_field(task.get("result_json"), default=None),
        "triggered_by": task["triggered_by"],
        "created_at": _iso(task["created_at"]),
        "started_at": _iso(task.get("started_at")),
        "completed_at": _iso(completed_at)
    }


# ========== OOV 实体审核 API ==========

@admin_bp.route('/oov/pending', methods=['GET'])
@api_handler("获取OOV列表")
def list_oov_pending():
    """
    获取待审核的 OOV 实体列表

    Query params:
        entity_type: skill/position/major/certificate (可选)
        limit: 返回数量，默认50
        offset: 偏移量，默认0

    用于管理员审核归一化系统未能识别的实体
    """
    entity_type = request.args.get('entity_type', '')
    limit, offset = clamp_pagination(
        request.args.get('limit', type=int),
        request.args.get('offset', type=int),
        default_limit=50,
    )

    mysql = get_mysql_client()

    # 查询 oov_entity 表
    try:
        if entity_type:
            sql = f"""
            SELECT * FROM oov_entity
            WHERE entity_type = :entity_type AND status = '{OovStatus.PENDING.value}'
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.execute(sql, {
                "entity_type": entity_type,
                "limit": limit,
                "offset": offset
            })
        else:
            sql = f"""
            SELECT * FROM oov_entity
            WHERE status = '{OovStatus.PENDING.value}'
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.execute(sql, {"limit": limit, "offset": offset})

        # 统计总数
        count_sql = f"SELECT COUNT(*) as total FROM oov_entity WHERE status = '{OovStatus.PENDING.value}'"
        count_result = mysql.execute(count_sql)
        total = count_result[0]["total"] if count_result else 0

    except Exception as e:
        # 表不存在时记录完整错误, 标记 partial 而非伪装成功
        logger.error(f"oov_entity表查询失败: {e}", exc_info=True)
        return {
            "oov_list": [],
            "total": 0,
            "partial": True,
            "note": "oov_entity表查询失败, 请检查表是否已创建"
        }

    oov_list = []
    for r in results:
        oov_list.append({
            "oov_id": r["oov_id"],
            "raw_entity": r["raw_entity"],
            "entity_type": r["entity_type"],
            "context": r.get("context_json", {}),
            "source": r.get("source", ""),
            "frequency": r.get("frequency", 1),
            "created_at": _iso(r["created_at"])
        })

    return {
        "oov_list": oov_list,
        "total": total,
        "pending_count": len(oov_list)
    }


@admin_bp.route('/oov/<oov_id>/approve', methods=['POST'])
@api_handler("审核OOV")
def approve_oov(oov_id: str):
    """
    审核通过 OOV 实体，归入规范实体库

    Body:
        {
            "canonical_name": "规范名称",
            "category": "分类",
            "aliases": ["别名1", "别名2"],
            "reviewed_by": "管理员ID"
        }
    """
    data = request.get_json() or {}
    canonical_name = data.get('canonical_name', '')
    if not canonical_name:
        return jsonify(safe_bad_request("canonical_name 必填")), 400

    category = data.get('category', '')
    aliases = data.get('aliases', [])
    reviewed_by = data.get('reviewed_by', 'admin')

    mysql = get_mysql_client()

    # 获取 OOV 记录
    oov_results = mysql.execute(
        "SELECT * FROM oov_entity WHERE oov_id = :oov_id",
        {"oov_id": oov_id}
    )
    if not oov_results:
        return jsonify(safe_not_found("OOV记录不存在")), 404

    oov = oov_results[0]
    entity_type = oov["entity_type"]

    # 事务: INSERT canonical_entity + SELECT LAST_INSERT_ID + UPDATE oov_entity
    # 三步必须原子, 避免审核状态与规范库不一致
    with mysql.engine.connect() as conn:
        with conn.begin():  # 自动 commit / 失败时 rollback
            conn.execute(text("""
                INSERT INTO canonical_entity
                (entity_type, canonical_name, aliases_json, category, source, created_at)
                VALUES (:entity_type, :canonical_name, :aliases_json, :category, :source, NOW())
            """), {
                "entity_type": entity_type,
                "canonical_name": canonical_name,
                "aliases_json": json.dumps(aliases),
                "category": category,
                "source": f"oov_approved:{oov_id}"
            })

            # 取回自增 id (必须同一连接才能取到正确的 LAST_INSERT_ID)
            row = conn.execute(text("SELECT LAST_INSERT_ID() AS new_id")).fetchone()
            new_entity_id = row[0] if row else None

            conn.execute(text(f"""
                UPDATE oov_entity SET
                status = '{OovStatus.APPROVED.value}',
                canonical_mapping = :canonical_name,
                reviewed_by = :reviewed_by,
                reviewed_at = NOW()
                WHERE oov_id = :oov_id
            """), {
                "canonical_name": canonical_name,
                "reviewed_by": reviewed_by,
                "oov_id": oov_id
            })

    return {
        "oov_id": oov_id,
        "entity_id": new_entity_id,
        "canonical_name": canonical_name,
        "message": "OOV已审核通过并入规范库"
    }


@admin_bp.route('/oov/<oov_id>/reject', methods=['POST'])
@api_handler("拒绝OOV")
def reject_oov(oov_id: str):
    """拒绝 OOV 实体"""
    data = request.get_json() or {}
    reason = data.get('reason', '')
    reviewed_by = data.get('reviewed_by', 'admin')

    mysql = get_mysql_client()
    sql = f"""
    UPDATE oov_entity SET
    status = '{OovStatus.REJECTED.value}',
    review_notes = :reason,
    reviewed_by = :reviewed_by,
    reviewed_at = NOW()
    WHERE oov_id = :oov_id
    """
    mysql.execute_write(sql, {"reason": reason, "reviewed_by": reviewed_by, "oov_id": oov_id})

    return {"oov_id": oov_id, "message": "已拒绝"}


# ========== 技能图谱状态 API ==========

# MySQL 统计白名单 (Q12: f-string 拼 SQL 仅允许对白名单表名使用)
_GRAPH_STATUS_TABLES = [
    'skill_taxonomy', 'canonical_entity', 'normalization_rule',
    'job_portrait_4d', 'student_portrait_4d', 'match_result'
]


def _neo4j_stats() -> dict:
    """Neo4j 节点与关系统计"""
    neo4j = get_neo4j_client()
    stats = {}
    try:
        # 节点统计
        node_results = neo4j.execute_query(
            "MATCH (n) RETURN labels(n)[0] as label, count(*) as count"
        )
        stats["nodes"] = {r["label"]: r["count"] for r in node_results}
        stats["total_nodes"] = sum(stats["nodes"].values())

        # 关系统计
        edge_results = neo4j.execute_query(
            "MATCH ()-[r]->() RETURN type(r) as type, count(*) as count"
        )
        stats["edges"] = {r["type"]: r["count"] for r in edge_results}
        stats["total_edges"] = sum(stats["edges"].values())
    except Exception as e:
        logger.warning(f"Neo4j统计失败: {e}")
        stats = {"error": str(e)}
    return stats


def _mysql_stats() -> dict:
    """MySQL 知识库表行数统计 (白名单表名, 安全使用 f-string)"""
    mysql = get_mysql_client()
    stats = {}
    for table in _GRAPH_STATUS_TABLES:
        try:
            result = mysql.execute(f"SELECT COUNT(*) as count FROM {table}")
            stats[table] = result[0]["count"] if result else 0
        except Exception as e:
            stats[table] = f"error: {e}"
    return stats


def _milvus_stats() -> dict:
    """Milvus 集合状态 (简化)"""
    return {
        "collections": ["skill_vectors", "jd_vectors", "resume_vectors", "conversation_vectors"],
        "note": "使用 Milvus Lite，统计需通过collection查询"
    }


@admin_bp.route('/graph/status', methods=['GET'])
@api_handler("获取图谱状态")
def get_graph_status():
    """
    获取知识图谱构建状态和统计

    返回:
        - Neo4j 图谱统计（节点数、边数）
        - Milvus 向量库状态
        - MySQL 知识库统计
    """
    neo = _neo4j_stats()
    return {
        "neo4j": neo,
        "mysql": _mysql_stats(),
        "milvus": _milvus_stats(),
        "overall_status": "healthy" if neo.get("total_nodes", 0) > 0 else "warning",
    }


@admin_bp.route('/graph/import-ability-course', methods=['POST'])
def import_ability_course_graph():
    """从仓内默认包或请求 JSON 导入能力/课程图到 Neo4j。"""
    try:
        from ..services.ability_graph_service import load_package, import_role_payload, DEFAULT_PACKAGE

        body = request.get_json(silent=True) or {}
        if body.get("roles"):
            summary = {}
            for item in body["roles"]:
                role = item.get("role")
                if role:
                    summary[role] = import_role_payload(role, item)
            return jsonify({"success": True, "data": {"roles": summary, "source": "request_body"}})

        path = body.get("path")
        result = load_package(path) if path else load_package(DEFAULT_PACKAGE)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify(safe_error("import ability/course graph", e)), 500


@admin_bp.route('/graph/generate-ability-course', methods=['POST'])
def generate_ability_course_graph():
    """从 MySQL 投影生成并写入 Neo4j。"""
    try:
        from ..services.ability_graph_service import generate_from_mysql

        body = request.get_json(silent=True) or {}
        role = (body.get("role") or request.args.get("role") or "").strip()
        if not role:
            return jsonify({"success": False, "error": "role required"}), 400
        result = generate_from_mysql(role)
        if not result:
            return jsonify({"success": False, "error": f"cannot generate for role={role}"}), 404
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify(safe_error("generate ability/course graph", e)), 500
