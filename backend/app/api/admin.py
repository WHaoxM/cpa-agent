"""
Admin API - 管理员相关 API
知识库上传 → 自动实体解析 → 意图识别 → 触发更新
"""

import json
import uuid
import os
from flask import request, jsonify

from . import admin_bp
from ._helpers import parse_json_field, safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.admin')


@admin_bp.route('/knowledge-base', methods=['GET'])
def list_knowledge_base():
    """获取知识库列表"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM knowledge_base ORDER BY created_at DESC"
        results = mysql.execute(sql)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        return jsonify(safe_error("获取知识库列表", e)), 500


@admin_bp.route('/knowledge-base/upload', methods=['POST'])
def upload_knowledge_base():
    """上传知识库"""
    try:
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

        return jsonify({"success": True, "data": {"id": kb_id, "message": "上传成功"}})

    except Exception as e:
        return jsonify(safe_error("上传知识库", e)), 500


@admin_bp.route('/knowledge-base/<kb_id>', methods=['DELETE'])
def delete_knowledge_base(kb_id: str):
    """删除知识库"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM knowledge_base WHERE id = :kb_id"
        mysql.execute_write(sql, {"kb_id": kb_id})

        return jsonify({"success": True, "data": {"message": f"知识库 {kb_id} 已删除"}})

    except Exception as e:
        return jsonify(safe_error("删除知识库", e)), 500


@admin_bp.route('/dataset', methods=['GET'])
def list_dataset():
    """获取数据集列表"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM job_dataset ORDER BY created_at DESC"
        results = mysql.execute(sql)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        return jsonify(safe_error("获取数据集列表", e)), 500


@admin_bp.route('/dataset/upload', methods=['POST'])
def upload_dataset():
    """上传数据集"""
    try:
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

        return jsonify({"success": True, "data": {"id": ds_id, "message": "上传成功"}})

    except Exception as e:
        return jsonify(safe_error("上传数据集", e)), 500


@admin_bp.route('/dataset/<ds_id>', methods=['DELETE'])
def delete_dataset(ds_id: str):
    """删除数据集"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM job_dataset WHERE id = :ds_id"
        mysql.execute_write(sql, {"ds_id": ds_id})

        return jsonify({"success": True, "data": {"message": f"数据集 {ds_id} 已删除"}})

    except Exception as e:
        return jsonify(safe_error("删除数据集", e)), 500


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
        triggered_by = data.get('triggered_by', 'admin')

        if not source_id:
            return jsonify({"success": False, "error": "source_id 必填"}), 400

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

        # 模拟异步触发（实际应接入 Celery/Redis Queue）
        # TODO: 接入异步任务队列

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
def get_pipeline_status(task_id: str):
    """查询管线任务状态"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM pipeline_task WHERE task_id = :task_id"
        results = mysql.execute(sql, {"task_id": task_id})

        if not results:
            return jsonify({"success": False, "error": "任务不存在"}), 404

        task = results[0]
        # pipeline_task 表无 updated_at 列, 改用 completed_at
        completed_at = task.get("completed_at")
        return jsonify({
            "success": True,
            "data": {
                "task_id": task["task_id"],
                "pipeline": task["pipeline"],
                "status": task["status"],
                "params": parse_json_field(task.get("params_json"), default={}),
                "result": parse_json_field(task.get("result_json"), default=None),
                "triggered_by": task["triggered_by"],
                "created_at": task["created_at"].isoformat() if task["created_at"] else None,
                "started_at": task.get("started_at").isoformat() if task.get("started_at") else None,
                "completed_at": completed_at.isoformat() if completed_at else None
            }
        })

    except Exception as e:
        return jsonify(safe_error("查询任务状态", e)), 500


# ========== OOV 实体审核 API ==========

@admin_bp.route('/oov/pending', methods=['GET'])
def list_oov_pending():
    """
    获取待审核的 OOV 实体列表

    Query params:
        entity_type: skill/position/major/certificate (可选)
        limit: 返回数量，默认50
        offset: 偏移量，默认0

    用于管理员审核归一化系统未能识别的实体
    """
    try:
        entity_type = request.args.get('entity_type', '')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        mysql = get_mysql_client()

        # 查询 oov_entity 表（需要创建此表）
        try:
            if entity_type:
                sql = """
                SELECT * FROM oov_entity
                WHERE entity_type = :entity_type AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
                results = mysql.execute(sql, {
                    "entity_type": entity_type,
                    "limit": limit,
                    "offset": offset
                })
            else:
                sql = """
                SELECT * FROM oov_entity
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
                results = mysql.execute(sql, {"limit": limit, "offset": offset})

            # 统计总数
            count_sql = "SELECT COUNT(*) as total FROM oov_entity WHERE status = 'pending'"
            count_result = mysql.execute(count_sql)
            total = count_result[0]["total"] if count_result else 0

        except Exception as e:
            # 表不存在时返回空
            logger.warning(f"oov_entity表查询失败: {e}")
            return jsonify({
                "success": True,
                "data": {
                    "oov_list": [],
                    "total": 0,
                    "note": "oov_entity表可能不存在"
                }
            })

        oov_list = []
        for r in results:
            oov_list.append({
                "oov_id": r["oov_id"],
                "raw_entity": r["raw_entity"],
                "entity_type": r["entity_type"],
                "context": r.get("context_json", {}),
                "source": r.get("source", ""),
                "frequency": r.get("frequency", 1),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None
            })

        return jsonify({
            "success": True,
            "data": {
                "oov_list": oov_list,
                "total": total,
                "pending_count": len(oov_list)
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取OOV列表", e)), 500


@admin_bp.route('/oov/<oov_id>/approve', methods=['POST'])
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
    try:
        data = request.get_json() or {}
        canonical_name = data.get('canonical_name', '')
        category = data.get('category', '')
        aliases = data.get('aliases', [])
        reviewed_by = data.get('reviewed_by', 'admin')

        if not canonical_name:
            return jsonify({"success": False, "error": "canonical_name 必填"}), 400

        mysql = get_mysql_client()

        # 获取 OOV 记录
        sql = "SELECT * FROM oov_entity WHERE oov_id = :oov_id"
        results = mysql.execute(sql, {"oov_id": oov_id})
        if not results:
            return jsonify({"success": False, "error": "OOV记录不存在"}), 404

        oov = results[0]
        entity_type = oov["entity_type"]

        # 添加到规范实体库 (canonical_entity.id 为自增主键, 无 entity_id 列)
        sql = """
        INSERT INTO canonical_entity
        (entity_type, canonical_name, aliases_json, category, source, created_at)
        VALUES (:entity_type, :canonical_name, :aliases_json, :category, :source, NOW())
        """
        mysql.execute_write(sql, {
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "aliases_json": json.dumps(aliases),
            "category": category,
            "source": f"oov_approved:{oov_id}"
        })

        # 取回自增 id
        row = mysql.execute_single("SELECT LAST_INSERT_ID() AS new_id")
        new_entity_id = row.get("new_id") if row else None

        # 更新 OOV 状态
        sql = """
        UPDATE oov_entity SET
        status = 'approved',
        canonical_mapping = :canonical_name,
        reviewed_by = :reviewed_by,
        reviewed_at = NOW()
        WHERE oov_id = :oov_id
        """
        mysql.execute_write(sql, {
            "canonical_name": canonical_name,
            "reviewed_by": reviewed_by,
            "oov_id": oov_id
        })

        return jsonify({
            "success": True,
            "data": {
                "oov_id": oov_id,
                "entity_id": new_entity_id,
                "canonical_name": canonical_name,
                "message": "OOV已审核通过并入规范库"
            }
        })

    except Exception as e:
        return jsonify(safe_error("审核OOV", e)), 500


@admin_bp.route('/oov/<oov_id>/reject', methods=['POST'])
def reject_oov(oov_id: str):
    """拒绝 OOV 实体"""
    try:
        data = request.get_json() or {}
        reason = data.get('reason', '')
        reviewed_by = data.get('reviewed_by', 'admin')

        mysql = get_mysql_client()
        sql = """
        UPDATE oov_entity SET
        status = 'rejected',
        review_notes = :reason,
        reviewed_by = :reviewed_by,
        reviewed_at = NOW()
        WHERE oov_id = :oov_id
        """
        mysql.execute_write(sql, {"reason": reason, "reviewed_by": reviewed_by, "oov_id": oov_id})

        return jsonify({"success": True, "data": {"oov_id": oov_id, "message": "已拒绝"}})

    except Exception as e:
        return jsonify(safe_error("拒绝OOV", e)), 500


# ========== 技能图谱状态 API ==========

@admin_bp.route('/graph/status', methods=['GET'])
def get_graph_status():
    """
    获取知识图谱构建状态和统计

    返回:
        - Neo4j 图谱统计（节点数、边数）
        - Milvus 向量库状态
        - MySQL 知识库统计
    """
    try:
        # Neo4j 统计
        from ..services.db import get_neo4j_client
        neo4j = get_neo4j_client()

        neo_stats = {}
        try:
            # 节点统计
            cypher_nodes = """
            MATCH (n)
            RETURN labels(n)[0] as label, count(*) as count
            """
            node_results = neo4j.execute_query(cypher_nodes)
            neo_stats["nodes"] = {r["label"]: r["count"] for r in node_results}
            neo_stats["total_nodes"] = sum(neo_stats["nodes"].values())

            # 关系统计
            cypher_edges = """
            MATCH ()-[r]->()
            RETURN type(r) as type, count(*) as count
            """
            edge_results = neo4j.execute_query(cypher_edges)
            neo_stats["edges"] = {r["type"]: r["count"] for r in edge_results}
            neo_stats["total_edges"] = sum(neo_stats["edges"].values())

        except Exception as e:
            logger.warning(f"Neo4j统计失败: {e}")
            neo_stats = {"error": str(e)}

        # MySQL 统计
        mysql = get_mysql_client()
        mysql_stats = {}
        tables = [
            'skill_taxonomy', 'canonical_entity', 'normalization_rule',
            'job_portrait_4d', 'student_portrait_4d', 'match_result'
        ]
        for table in tables:
            try:
                sql = f"SELECT COUNT(*) as count FROM {table}"
                result = mysql.execute(sql)
                mysql_stats[table] = result[0]["count"] if result else 0
            except Exception as e:
                mysql_stats[table] = f"error: {e}"

        # Milvus 统计（简化）
        milvus_stats = {
            "collections": ["skill_vectors", "jd_vectors", "resume_vectors", "conversation_vectors"],
            "note": "使用 Milvus Lite，统计需通过collection查询"
        }

        return jsonify({
            "success": True,
            "data": {
                "neo4j": neo_stats,
                "mysql": mysql_stats,
                "milvus": milvus_stats,
                "overall_status": "healthy" if neo_stats.get("total_nodes", 0) > 0 else "warning"
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取图谱状态", e)), 500
