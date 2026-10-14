"""
Admin API - 管理员相关 API
知识库上传 → 自动实体解析 → 意图识别 → 触发更新
"""

import json
import uuid
from enum import Enum
from flask import request, jsonify, g, current_app
from flask_login import current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge

from . import admin_bp
from ._helpers import (
    _iso,
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
from ..services.admin_source_store import (
    MAX_SOURCE_BYTES, checked_path, read_source, remove_staged_source, stage_source,
)
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


@admin_bp.route('/python-info', methods=['GET'])
def python_info():
    """Diagnostic data is covered by the same server-side admin gate as other admin routes."""
    import sys

    return jsonify({
        'python_executable': sys.executable,
        'python_version': sys.version,
        'python_path': sys.path[:5],
    })


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
    """Create metadata, or privately stage an explicitly supplied UTF-8 text file."""
    if request.mimetype == 'multipart/form-data':
        return _upload_admin_source('knowledge-base')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    title = data.get('title')
    kb_type = data.get('type', 'skill')
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return jsonify(safe_bad_request("title 必须是 1 至 200 字符的文本")), 400
    if not isinstance(kb_type, str) or not kb_type.strip() or len(kb_type) > 30:
        return jsonify(safe_bad_request("type 必须是 1 至 30 字符的文本")), 400
    mysql = get_mysql_client()

    kb_id = f"kb_{uuid.uuid4().hex[:12]}"
    sql = """
    INSERT INTO knowledge_base (id, title, type, status, created_at)
    VALUES (:id, :title, :type, :status, NOW())
    """

    mysql.execute_write(sql, {
        "id": kb_id,
        "title": title.strip(),
        "type": kb_type.strip(),
        # Metadata upload does not ingest or process content; clients cannot
        # claim a terminal processing state.
        "status": 'pending'
    })

    return {"id": kb_id, "status": "pending", "content_ingested": False,
            "message": "元数据已创建，内容未入库"}


@admin_bp.route('/knowledge-base/<kb_id>', methods=['DELETE'])
@api_handler("删除知识库")
def delete_knowledge_base(kb_id: str):
    """删除知识库"""
    mysql = get_mysql_client()
    existing = mysql.execute("SELECT id, file_path FROM knowledge_base WHERE id = :kb_id LIMIT 1", {"kb_id": kb_id})
    if not existing:
        return jsonify(safe_not_found(f"知识库不存在: {kb_id}")), 404
    _delete_admin_source(mysql, 'knowledge-base', kb_id, existing[0].get('file_path'))

    remaining = mysql.execute("SELECT id FROM knowledge_base WHERE id = :kb_id LIMIT 1", {"kb_id": kb_id})
    if remaining:
        raise RuntimeError("知识库删除未通过数据库回读验证")

    return {"message": f"知识库 {kb_id} 已删除"}


@admin_bp.route('/knowledge-base/<kb_id>/content', methods=['GET'])
@api_handler("读取知识库暂存内容")
def read_knowledge_base_content(kb_id: str):
    return _read_admin_content('knowledge-base', kb_id)


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
    """Create metadata, or privately stage JSON; never publish job rows."""
    if request.mimetype == 'multipart/form-data':
        return _upload_admin_source('dataset')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    title = data.get('title')
    category = data.get('category')
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return jsonify(safe_bad_request("title 必须是 1 至 200 字符的文本")), 400
    if category is not None and (not isinstance(category, str) or len(category) > 50):
        return jsonify(safe_bad_request("category 必须是最多 50 字符的文本")), 400
    mysql = get_mysql_client()

    ds_id = f"ds_{uuid.uuid4().hex[:12]}"
    sql = """
    INSERT INTO job_dataset (id, title, count, category, status, created_at)
    VALUES (:id, :title, :count, :category, :status, NOW())
    """

    mysql.execute_write(sql, {
        "id": ds_id,
        "title": title.strip(),
        # No job records are ingested here. The public count is observed, not
        # a client-declared estimate of a file this endpoint never received.
        "count": 0,
        "category": category,
        # This endpoint persists metadata only; build/ingest status is server-owned.
        "status": 'pending'
    })

    return {"id": ds_id, "status": "pending", "content_ingested": False,
            "message": "元数据已创建，岗位未入库"}


@admin_bp.route('/dataset/<ds_id>', methods=['DELETE'])
@api_handler("删除数据集")
def delete_dataset(ds_id: str):
    """删除数据集"""
    mysql = get_mysql_client()
    existing = mysql.execute("SELECT id, file_path FROM job_dataset WHERE id = :ds_id LIMIT 1", {"ds_id": ds_id})
    if not existing:
        return jsonify(safe_not_found(f"数据集不存在: {ds_id}")), 404
    _delete_admin_source(mysql, 'dataset', ds_id, existing[0].get('file_path'))

    remaining = mysql.execute("SELECT id FROM job_dataset WHERE id = :ds_id LIMIT 1", {"ds_id": ds_id})
    if remaining:
        raise RuntimeError("数据集删除未通过数据库回读验证")

    return {"message": f"数据集 {ds_id} 已删除"}


@admin_bp.route('/dataset/<ds_id>/content', methods=['GET'])
@api_handler("读取岗位数据集暂存内容")
def read_dataset_content(ds_id: str):
    return _read_admin_content('dataset', ds_id)


def _upload_admin_source(kind: str):
    """Keep source staging distinct from the legacy metadata-only JSON contract."""
    # Reject the multipart envelope before Werkzeug spools a much larger file.
    request.max_content_length = MAX_SOURCE_BYTES + 64 * 1024
    try:
        title = request.form.get('title')
        upload = request.files.get('file')
        field = 'category' if kind == 'dataset' else 'type'
        value = request.form.get(field)
    except RequestEntityTooLarge:
        return jsonify({"success": False, "code": "admin_source_too_large",
                        "error": "multipart 请求超过来源上传上限"}), 413
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return jsonify(safe_bad_request("title 必须是 1 至 200 字符的文本")), 400
    limit = 50 if kind == 'dataset' else 30
    if value is not None and len(value) > limit:
        return jsonify(safe_bad_request(f"{field} 最多 {limit} 字符")), 400
    if upload is None:
        return jsonify(safe_bad_request("multipart 上传必须包含 file")), 400
    source_id = ('ds_' if kind == 'dataset' else 'kb_') + uuid.uuid4().hex[:12]
    try:
        staged = stage_source(source_id, kind, upload)
    except ValueError as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    try:
        mysql = get_mysql_client()
        if kind == 'dataset':
            sql = """
                INSERT INTO job_dataset
                    (id, title, count, category, status, file_path, file_size,
                     content_sha256, staged_record_count, uploaded_by, created_at)
                VALUES (:id, :title, 0, :category, 'pending', :file_path, :file_size,
                        :content_sha256, :staged_record_count, :uploaded_by, NOW())
            """
            params = {"id": source_id, "title": title.strip(), "category": value,
                      "uploaded_by": resolve_request_scope().principal_id, **staged}
        else:
            sql = """
                INSERT INTO knowledge_base
                    (id, title, type, status, file_path, file_size,
                     content_sha256, uploaded_by, created_at)
                VALUES (:id, :title, :type, 'pending', :file_path, :file_size,
                        :content_sha256, :uploaded_by, NOW())
            """
            params = {"id": source_id, "title": title.strip(), "type": value or 'text',
                      "uploaded_by": resolve_request_scope().principal_id,
                      **{k: v for k, v in staged.items() if k != 'staged_record_count'}}
        table = 'job_dataset' if kind == 'dataset' else 'knowledge_base'
        with mysql.engine.begin() as conn:
            conn.execute(text(sql), params)
            saved = conn.execute(text(
                f"SELECT file_path, file_size, content_sha256, status"
                + (", count, staged_record_count" if kind == 'dataset' else "")
                + f" FROM {table} WHERE id=:id LIMIT 1"), {"id": source_id}).mappings().first()
            if (not saved or saved['file_path'] != staged['file_path']
                    or saved['file_size'] != staged['file_size']
                    or saved['content_sha256'] != staged['content_sha256']
                    or saved['status'] != 'pending'
                    or (kind == 'dataset' and (saved['count'] != 0
                        or saved['staged_record_count'] != staged['staged_record_count']))):
                raise RuntimeError("管理员来源写入未通过数据库回读验证")
    except Exception:
        remove_staged_source(staged['file_path'])
        raise
    return {"id": source_id, "status": "pending", "content_staged": True,
            "content_ingested": False, "file_size": staged['file_size'],
            "content_sha256": staged['content_sha256'],
            "staged_record_count": staged['staged_record_count'],
            "count": 0 if kind == 'dataset' else None,
            "message": "私有来源文件已暂存；未发布、未建图"}


def _read_admin_content(kind: str, source_id: str):
    table = 'job_dataset' if kind == 'dataset' else 'knowledge_base'
    mysql = get_mysql_client()
    rows = mysql.execute(
        f"SELECT id, file_path, file_size, content_sha256"
        + (", staged_record_count" if kind == 'dataset' else "")
        + f" FROM {table} WHERE id=:id LIMIT 1", {"id": source_id})
    if not rows:
        return jsonify(safe_not_found("管理员来源不存在")), 404
    row = rows[0]
    if not row.get('file_path'):
        return jsonify(safe_bad_request("该记录仅有元数据，没有暂存内容")), 409
    try:
        content = read_source(source_id, kind, row)
    except (OSError, ValueError) as exc:
        logger.error("admin source content unavailable: %s: %s", source_id, exc)
        return jsonify({"success": False, "code": "admin_source_unavailable",
                        "error": "暂存来源文件不可用或完整性校验失败"}), 503
    return {"id": source_id, "content": content,
            "file_size": row['file_size'], "content_sha256": row['content_sha256'],
            "staged_record_count": row.get('staged_record_count', 0),
            "published": False, "graph_built": False}


def _delete_admin_source(mysql, kind: str, source_id: str, file_path):
    table = 'job_dataset' if kind == 'dataset' else 'knowledge_base'
    tombstone = None
    path = None
    if file_path:
        path = checked_path(source_id, kind, file_path)
        if path.exists():
            tombstone = path.with_name(f".{path.name}.{uuid.uuid4().hex}.deleting")
            path.replace(tombstone)
    try:
        with mysql.engine.begin() as conn:
            deleted = conn.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": source_id})
            if deleted.rowcount != 1:
                raise RuntimeError("管理员来源删除未影响唯一记录")
    except Exception:
        if tombstone is not None:
            tombstone.replace(path)
        raise
    if tombstone is not None:
        tombstone.unlink()


# ========== 管线管理 API ==========

@admin_bp.route('/pipeline/build', methods=['POST'])
def trigger_pipeline_build():
    """Do not claim a build was accepted until source ingestion and dispatch exist."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    source_id = data.get('source_id')
    if not isinstance(source_id, str) or not source_id.strip():
        return jsonify(safe_bad_request("source_id 必须为非空文本")), 400
    return jsonify({
        "success": False,
        "code": "admin_pipeline_build_unavailable",
        "error": "管理端建图尚未接入数据源读取与执行器，未创建任务",
        "retryable": False,
    }), 503


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
        count_params = {}
        if entity_type:
            count_sql += " AND entity_type = :entity_type"
            count_params["entity_type"] = entity_type
        count_result = mysql.execute(count_sql, count_params)
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
            "aliases": ["别名1", "别名2"]
        }
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    canonical_name = data.get('canonical_name', '')
    if not isinstance(canonical_name, str) or not canonical_name.strip():
        return jsonify(safe_bad_request("canonical_name 必须是非空文本")), 400
    canonical_name = canonical_name.strip()

    category = data.get('category', '')
    aliases = data.get('aliases', [])
    if not isinstance(category, str) or not isinstance(aliases, list) or any(
        not isinstance(alias, str) or not alias.strip() for alias in aliases
    ):
        return jsonify(safe_bad_request("category 必须是文本，aliases 必须是文本数组")), 400
    reviewed_by = resolve_request_scope().principal_id

    mysql = get_mysql_client()

    # Lock and verify the pending state in the same transaction as the
    # canonical insert, so a reviewed row cannot be replayed or reversed.
    with mysql.engine.connect() as conn:
        with conn.begin():
            oov = conn.execute(text(
                "SELECT entity_type, status FROM oov_entity "
                "WHERE oov_id = :oov_id FOR UPDATE"
            ), {"oov_id": oov_id}).mappings().first()
            if not oov:
                return jsonify(safe_not_found("OOV记录不存在")), 404
            if oov["status"] != OovStatus.PENDING.value:
                return jsonify({"success": False, "error": "OOV记录已审核，不能重复变更"}), 409
            entity_type = oov["entity_type"]

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

            updated = conn.execute(text(f"""
                UPDATE oov_entity SET
                status = '{OovStatus.APPROVED.value}',
                canonical_mapping = :canonical_name,
                reviewed_by = :reviewed_by,
                reviewed_at = NOW()
                WHERE oov_id = :oov_id AND status = '{OovStatus.PENDING.value}'
            """), {
                "canonical_name": canonical_name,
                "reviewed_by": reviewed_by,
                "oov_id": oov_id
            })
            if updated.rowcount != 1:
                raise RuntimeError("OOV审核状态已变化，操作已回滚")

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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    reason = data.get('reason', '')
    if not isinstance(reason, str):
        return jsonify(safe_bad_request("reason 必须是文本")), 400
    reviewed_by = resolve_request_scope().principal_id

    mysql = get_mysql_client()
    with mysql.engine.connect() as conn:
        with conn.begin():
            current = conn.execute(text(
                "SELECT status FROM oov_entity WHERE oov_id = :oov_id FOR UPDATE"
            ), {"oov_id": oov_id}).mappings().first()
            if not current:
                return jsonify(safe_not_found("OOV记录不存在")), 404
            if current["status"] != OovStatus.PENDING.value:
                return jsonify({"success": False, "error": "OOV记录已审核，不能重复变更"}), 409
            updated = conn.execute(text(f"""
                UPDATE oov_entity SET
                status = '{OovStatus.REJECTED.value}',
                review_notes = :reason,
                reviewed_by = :reviewed_by,
                reviewed_at = NOW()
                WHERE oov_id = :oov_id AND status = '{OovStatus.PENDING.value}'
            """), {"reason": reason, "reviewed_by": reviewed_by, "oov_id": oov_id})
            if updated.rowcount != 1:
                raise RuntimeError("OOV审核状态已变化，操作已回滚")

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


# ========== 可信岗位发布：正式账号的二次摘要确认 ==========

def _job_publication_admin():
    """Do not let a test/dev g.cpa_admin bit stand in for an account login."""
    if (not current_app.extensions.get("cpa_account_auth_enabled")
            or not current_app.extensions.get("cpa_account_session_schema_ready")):
        return None
    if (not current_user.is_authenticated or current_user.row["role"] != "admin"
            or current_user.row["status"] != "active"
            or current_user.id != resolve_request_scope().principal_id):
        return None
    return current_user.id


def _publication_body():
    if request.content_length is not None and request.content_length > 256_000:
        return None
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _preview_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="cpa-job-publication-preview-v1")


@admin_bp.route('/job-reviews', methods=['GET'])
def list_admin_job_source_reviews():
    """Read-only model audit queue; not an approval or source-currentness API."""
    from ..services.admin_job_review import list_admin_job_reviews, JobReviewCorrupt

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    status = request.args.get("status")
    limit_raw = request.args.get("limit", "20")
    offset_raw = request.args.get("offset", "0")
    if (status not in (None, "blocked", "candidate_ready")
            or not limit_raw.isdecimal() or not offset_raw.isdecimal()):
        return jsonify(safe_bad_request("status、limit 或 offset 无效")), 400
    try:
        data = list_admin_job_reviews(get_mysql_client(), status=status,
                                      limit=int(limit_raw), offset=int(offset_raw))
    except ValueError as exc:
        if isinstance(exc, JobReviewCorrupt):
            logger.exception("岗位模型审查列表完整性检查失败")
            return jsonify({"success": False, "error": "岗位模型审查记录暂不可用"}), 503
        return jsonify(safe_bad_request(str(exc))), 400
    except SQLAlchemyError:
        logger.exception("岗位模型审查列表读取失败")
        return jsonify({"success": False, "error": "岗位模型审查记录暂不可用"}), 503
    response = jsonify({"success": True, **data})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-reviews/<review_id>', methods=['GET'])
def get_admin_job_source_review(review_id):
    from ..services.admin_job_review import read_admin_job_review, JobReviewCorrupt

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    try:
        detail = read_admin_job_review(get_mysql_client(), review_id)
    except ValueError as exc:
        if isinstance(exc, JobReviewCorrupt):
            logger.exception("岗位模型审查详情完整性检查失败")
            return jsonify({"success": False, "error": "岗位模型审查记录暂不可用"}), 503
        return jsonify(safe_bad_request(str(exc))), 400
    except SQLAlchemyError:
        logger.exception("岗位模型审查详情读取失败")
        return jsonify({"success": False, "error": "岗位模型审查记录暂不可用"}), 503
    if detail is None:
        return jsonify(safe_not_found("模型审查记录不存在")), 404
    response = jsonify({"success": True, **detail})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-source-listings', methods=['GET'])
def list_admin_source_only_jobs():
    from ..services.job_source_listing import list_admin_source_listings, SourceListingUnavailable

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    limit, offset = clamp_pagination(request.args.get("limit", type=int),
                                     request.args.get("offset", type=int))
    try:
        data = list_admin_source_listings(get_mysql_client(), limit=limit, offset=offset)
    except (SourceListingUnavailable, ValueError, SQLAlchemyError):
        logger.exception("来源专用岗位审核列表回读失败")
        return jsonify({"success": False, "error": "来源专用岗位记录暂不可用"}), 503
    response = jsonify({"success": True, **data})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-source-listings/<int:listing_id>', methods=['GET'])
def get_admin_source_only_job(listing_id):
    from ..services.job_source_listing import read_admin_source_listing, SourceListingUnavailable

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    try:
        detail = read_admin_source_listing(get_mysql_client(), listing_id)
    except (SourceListingUnavailable, ValueError, SQLAlchemyError):
        logger.exception("来源专用岗位审核详情回读失败")
        return jsonify({"success": False, "error": "来源专用岗位记录暂不可用"}), 503
    if detail is None:
        return jsonify(safe_not_found("来源专用岗位版本不存在")), 404
    response = jsonify({"success": True, **detail})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-source-listings', methods=['POST'])
def publish_admin_source_only_job():
    """Account-approved original official source, never a scored publication."""
    from ..services.job_source_listing import (
        list_official_source_only, SourceListingConflict,
        SourceListingForbidden, SourceListingUnavailable,
    )
    from ..services.admin_job_review import JobReviewCorrupt

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    if body.get("confirm_source_only") is not True:
        return jsonify(safe_bad_request("必须明确确认仅展示官方原文、不参与评分与匹配")), 400
    try:
        result = list_official_source_only(
            get_mysql_client(), review_id=body.get("review_id"),
            listing_version=body.get("listing_version"),
            expected_review_sha256=body.get("expected_review_sha256"),
            expected_record_sha256=body.get("expected_source_record_sha256"),
            actor_id=actor_id, ip=request.remote_addr)
    except LookupError:
        return jsonify(safe_not_found("模型岗位审查不存在")), 404
    except SourceListingConflict as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except SourceListingForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except IntegrityError:
        return jsonify({"success": False, "error": "该来源展示版本已存在"}), 409
    except (SourceListingUnavailable, JobReviewCorrupt, SQLAlchemyError):
        logger.exception("官方来源专用岗位审核暂不可用")
        return jsonify({"success": False, "error": "官方来源暂不可核验或数据库不可用"}), 503
    except (ValueError, TypeError) as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    return jsonify({"success": True, **result}), 201


@admin_bp.route('/job-source-listings/<int:listing_id>/revoke', methods=['POST'])
def revoke_admin_source_only_job(listing_id):
    from ..services.job_source_listing import (
        revoke_source_only, SourceListingConflict, SourceListingForbidden,
        SourceListingUnavailable,
    )

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    try:
        result = revoke_source_only(get_mysql_client(), listing_id=listing_id,
                                    expected_digest=body.get("expected_content_sha256"),
                                    reason=body.get("reason"), actor_id=actor_id,
                                    ip=request.remote_addr)
    except SourceListingConflict as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except SourceListingForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except (SourceListingUnavailable, SQLAlchemyError):
        logger.exception("来源专用岗位撤回暂不可用")
        return jsonify({"success": False, "error": "来源专用岗位数据库暂不可用"}), 503
    except ValueError as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    if result is None:
        return jsonify(safe_not_found("来源专用岗位不存在")), 404
    return jsonify({"success": True, **result})


# ========== 学校档位：教育部原文人工审核（不改学生 L0 简历） ==========

@admin_bp.route('/school-tier-reviews', methods=['POST'])
def approve_admin_school_tier():
    from ..services.school_tier_review import (
        approve_school_tier, SchoolTierReviewConflict, SchoolTierReviewForbidden,
        SchoolTierSourceUnavailable,
    )

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    try:
        result = approve_school_tier(get_mysql_client(), body=body,
                                     actor_id=actor_id, ip=request.remote_addr)
    except SchoolTierReviewForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except SchoolTierReviewConflict as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except (SchoolTierSourceUnavailable, SQLAlchemyError):
        logger.exception("学校档位官方来源审核暂不可用")
        return jsonify({"success": False, "error": "官方来源或审核数据库暂不可用"}), 503
    except ValueError as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    response = jsonify({"success": True, "data": result})
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if result["idempotent"] else 201


@admin_bp.route('/school-tier-reviews/current', methods=['GET'])
def current_admin_school_tier():
    from ..services.school_tier_review import latest_review, SchoolTierSourceUnavailable

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    student_id = request.args.get("student_id", "")
    task_id = request.args.get("confirmation_task_id", "")
    if (not student_id or len(student_id) > 64 or not task_id or len(task_id) > 64):
        return jsonify(safe_bad_request("需要 student_id 与 confirmation_task_id")), 400
    try:
        row = latest_review(get_mysql_client(), student_id=student_id,
                            confirmation_task_id=task_id)
    except (SchoolTierSourceUnavailable, SQLAlchemyError):
        logger.exception("学校档位审核回读暂不可用")
        return jsonify({"success": False, "error": "学校档位审核数据库暂不可用"}), 503
    if row is None:
        return jsonify(safe_not_found("当前确认简历尚无学校档位审核")), 404
    public = {key: row[key] for key in (
        "id", "student_id", "confirmation_task_id", "school_name", "school_tier",
        "source_kind", "source_uri", "source_sha256", "source_quote", "quote_sha256",
        "evidence_sha256", "source_checked_at", "status", "reviewed_by", "reviewed_at",
        "revoked_by", "revoked_at", "revoke_reason")}
    response = jsonify({"success": True, "data": public})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/school-tier-reviews/<int:review_id>/revoke', methods=['POST'])
def revoke_admin_school_tier(review_id):
    from ..services.school_tier_review import (
        revoke_school_tier, SchoolTierReviewConflict, SchoolTierReviewForbidden,
        SchoolTierSourceUnavailable,
    )

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    try:
        result = revoke_school_tier(
            get_mysql_client(), review_id=review_id,
            expected_digest=body.get("expected_evidence_sha256"),
            reason=body.get("reason"), actor_id=actor_id, ip=request.remote_addr)
    except SchoolTierReviewForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except SchoolTierReviewConflict as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except (SchoolTierSourceUnavailable, SQLAlchemyError):
        logger.exception("学校档位审核撤回暂不可用")
        return jsonify({"success": False, "error": "学校档位审核数据库暂不可用"}), 503
    except ValueError as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    if result is None:
        return jsonify(safe_not_found("学校档位审核版本不存在")), 404
    response = jsonify({"success": True, "data": result})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-publications', methods=['GET'])
def list_admin_job_publications():
    from ..services.admin_job_publication import list_admin_publications, PublicationConflict

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    limit, offset = clamp_pagination(request.args.get("limit", type=int),
                                     request.args.get("offset", type=int))
    try:
        data = list_admin_publications(get_mysql_client(), limit=limit, offset=offset)
    except (PublicationConflict, ValueError, SQLAlchemyError):
        logger.exception("管理员岗位发布列表回读失败")
        return jsonify({"success": False, "error": "岗位发布记录暂不可用"}), 503
    return jsonify({"success": True, **data})


@admin_bp.route('/job-publications/<int:publication_id>', methods=['GET'])
def get_admin_job_publication(publication_id):
    from ..services.admin_job_publication import read_admin_publication, PublicationConflict

    if _job_publication_admin() is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    try:
        detail = read_admin_publication(get_mysql_client(), publication_id)
    except (PublicationConflict, ValueError, SQLAlchemyError):
        logger.exception("管理员岗位发布详情回读失败")
        return jsonify({"success": False, "error": "岗位发布记录暂不可用"}), 503
    if detail is None:
        return jsonify(safe_not_found("发布版本不存在")), 404
    response = jsonify({"success": True, **detail})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-publications/preview', methods=['POST'])
def preview_job_publication():
    from ..services.admin_job_publication import prepare_candidate, PublicationNotReady

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    try:
        prepared = prepare_candidate(job_portrait_id=body.get("job_portrait_id"),
                                     publication_version=body.get("publication_version"),
                                     candidate=body.get("snapshot"), actor_id=actor_id)
    except PublicationNotReady as exc:
        return jsonify({"success": False, "error": str(exc),
                        "missing_job_fields": exc.missing}), 422
    except (ValueError, TypeError, OverflowError) as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    signed = _preview_signer().dumps({
        "actor": actor_id, "job": prepared["job_portrait_id"],
        "version": prepared["publication_version"],
        "candidate_sha256": prepared["candidate_sha256"],
        "source_text_sha256": prepared["source_text_sha256"],
    })
    response = jsonify({"success": True, "status": "preview_not_approved",
                        "job_portrait_id": prepared["job_portrait_id"],
                        "publication_version": prepared["publication_version"],
                        "candidate_sha256": prepared["candidate_sha256"],
                        "source_text_sha256": prepared["source_text_sha256"],
                        "snapshot_for_review": prepared["preview_snapshot"],
                        "preview_token": signed, "expires_in_seconds": 600,
                        "vacancy_validity": "unknown"})
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route('/job-publications', methods=['POST'])
def publish_admin_job():
    from ..services.admin_job_publication import (
        prepare_candidate, publish_reviewed_candidate, PublicationNotReady,
        PublicationConflict, PublicationForbidden,
    )

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    confirmation = body.get("confirm_review")
    required = ("source_checked", "requirements_checked", "license_checked",
                "publish_exact_digest")
    if not isinstance(confirmation, dict) or any(confirmation.get(k) is not True for k in required):
        return jsonify(safe_bad_request("缺少来源、要求、许可及精确摘要的明确审核确认")), 400
    try:
        prepared = prepare_candidate(job_portrait_id=body.get("job_portrait_id"),
                                     publication_version=body.get("publication_version"),
                                     candidate=body.get("snapshot"), actor_id=actor_id)
    except PublicationNotReady as exc:
        return jsonify({"success": False, "error": str(exc),
                        "missing_job_fields": exc.missing}), 422
    except (ValueError, TypeError, OverflowError) as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    try:
        signed = _preview_signer().loads(body.get("preview_token", ""), max_age=600)
    except (BadSignature, TypeError):
        return jsonify({"success": False, "error": "预检确认已失效，请重新预检"}), 409
    expected = {"actor": actor_id, "job": prepared["job_portrait_id"],
                "version": prepared["publication_version"],
                "candidate_sha256": prepared["candidate_sha256"],
                "source_text_sha256": prepared["source_text_sha256"]}
    if (signed != expected or body.get("expected_candidate_sha256") != prepared["candidate_sha256"]
            or body.get("expected_source_text_sha256") != prepared["source_text_sha256"]):
        return jsonify({"success": False, "error": "候选或来源已改变，请重新预检"}), 409
    try:
        result = publish_reviewed_candidate(get_mysql_client(), prepared=prepared,
                                            candidate=body["snapshot"], actor_id=actor_id,
                                            ip=request.remote_addr)
    except (PublicationConflict, IntegrityError) as exc:
        return jsonify({"success": False, "error": str(exc) if isinstance(exc, PublicationConflict)
                        else "该岗位发布版本已存在"}), 409
    except PublicationForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except SQLAlchemyError:
        logger.exception("岗位发布数据库事务失败")
        return jsonify({"success": False, "error": "岗位发布数据库暂不可用"}), 503
    return jsonify({"success": True, **result}), 201


@admin_bp.route('/job-publications/<int:publication_id>/revoke', methods=['POST'])
def revoke_admin_job(publication_id):
    from ..services.admin_job_publication import (
        revoke_publication, PublicationConflict, PublicationForbidden,
    )

    actor_id = _job_publication_admin()
    if actor_id is None:
        return jsonify({"success": False, "error": "需要正式登录的管理员账号"}), 403
    body = _publication_body()
    if body is None:
        return jsonify(safe_bad_request("请求体必须是至多 256 KB 的 JSON 对象")), 400
    try:
        result = revoke_publication(get_mysql_client(), publication_id=publication_id,
                                    expected_digest=body.get("expected_content_sha256"),
                                    reason=body.get("reason"), actor_id=actor_id,
                                    ip=request.remote_addr)
    except PublicationConflict as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    except PublicationForbidden:
        return jsonify({"success": False, "error": "管理员账号不再有效"}), 403
    except ValueError as exc:
        return jsonify(safe_bad_request(str(exc))), 400
    except SQLAlchemyError:
        logger.exception("岗位撤回数据库事务失败")
        return jsonify({"success": False, "error": "岗位撤回数据库暂不可用"}), 503
    if result is None:
        return jsonify(safe_not_found("发布版本不存在")), 404
    return jsonify({"success": True, **result})
