"""
Admin API - 管理员相关 API
"""

import uuid
from flask import request, jsonify

from . import admin_bp
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
        logger.error(f"获取知识库列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/knowledge-base/upload', methods=['POST'])
def upload_knowledge_base():
    """上传知识库"""
    try:
        data = request.get_json()
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
        logger.error(f"上传知识库失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/knowledge-base/<kb_id>', methods=['DELETE'])
def delete_knowledge_base(kb_id: str):
    """删除知识库"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM knowledge_base WHERE id = :kb_id"
        mysql.execute_write(sql, {"kb_id": kb_id})

        return jsonify({"success": True, "data": {"message": f"知识库 {kb_id} 已删除"}})

    except Exception as e:
        logger.error(f"删除知识库失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/dataset', methods=['GET'])
def list_dataset():
    """获取数据集列表"""
    try:
        mysql = get_mysql_client()
        sql = "SELECT * FROM job_dataset ORDER BY created_at DESC"
        results = mysql.execute(sql)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        logger.error(f"获取数据集列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/dataset/upload', methods=['POST'])
def upload_dataset():
    """上传数据集"""
    try:
        data = request.get_json()
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
        logger.error(f"上传数据集失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/dataset/<ds_id>', methods=['DELETE'])
def delete_dataset(ds_id: str):
    """删除数据集"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM job_dataset WHERE id = :ds_id"
        mysql.execute_write(sql, {"ds_id": ds_id})

        return jsonify({"success": True, "data": {"message": f"数据集 {ds_id} 已删除"}})

    except Exception as e:
        logger.error(f"删除数据集失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
