"""
Demo Admin API - 管理员相关 Demo API
"""

import uuid
from flask import jsonify
from . import admin_bp

MOCK_KNOWLEDGE_BASE = [
    {
        "id": "kb_001",
        "title": "Python 技能图谱",
        "type": "skill",
        "status": "published",
        "createdAt": "2026-04-01",
        "nodeCount": 128,
        "edgeCount": 256
    },
    {
        "id": "kb_002",
        "title": "前端技能图谱",
        "type": "skill",
        "status": "published",
        "createdAt": "2026-04-05",
        "nodeCount": 96,
        "edgeCount": 180
    }
]

MOCK_JOB_DATASET = [
    {
        "id": "jd_001",
        "title": "Python后端开发 JD 集合",
        "count": 500,
        "category": "后端开发",
        "status": "active",
        "createdAt": "2026-03-15"
    },
    {
        "id": "jd_002",
        "title": "前端开发 JD 集合",
        "count": 350,
        "category": "前端开发",
        "status": "active",
        "createdAt": "2026-03-20"
    }
]


@admin_bp.route('/knowledge-base', methods=['GET'])
def list_knowledge_base():
    """获取知识库列表"""
    return jsonify({
        "success": True,
        "data": MOCK_KNOWLEDGE_BASE
    })


@admin_bp.route('/knowledge-base/upload', methods=['POST'])
def upload_knowledge_base():
    """上传知识库"""
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    return jsonify({
        "success": True,
        "data": {"id": kb_id, "message": "上传成功"}
    })


@admin_bp.route('/knowledge-base/<kb_id>', methods=['DELETE'])
def delete_knowledge_base(kb_id: str):
    """删除知识库"""
    return jsonify({
        "success": True,
        "data": {"message": f"知识库 {kb_id} 已删除"}
    })


@admin_bp.route('/knowledge-base/<kb_id>/reindex', methods=['POST'])
def reindex_knowledge_base(kb_id: str):
    """重建知识库索引"""
    return jsonify({
        "success": True,
        "data": {"message": f"知识库 {kb_id} 索引重建完成"}
    })


@admin_bp.route('/dataset', methods=['GET'])
def list_dataset():
    """获取数据集列表"""
    return jsonify({
        "success": True,
        "data": MOCK_JOB_DATASET
    })


@admin_bp.route('/dataset/upload', methods=['POST'])
def upload_dataset():
    """上传数据集"""
    ds_id = f"ds_{uuid.uuid4().hex[:8]}"
    return jsonify({
        "success": True,
        "data": {"id": ds_id, "message": "上传成功"}
    })


@admin_bp.route('/dataset/<ds_id>', methods=['DELETE'])
def delete_dataset(ds_id: str):
    """删除数据集"""
    return jsonify({
        "success": True,
        "data": {"message": f"数据集 {ds_id} 已删除"}
    })


@admin_bp.route('/dataset/<ds_id>', methods=['PATCH'])
def update_dataset(ds_id: str):
    """更新数据集"""
    return jsonify({
        "success": True,
        "data": {"message": f"数据集 {ds_id} 已更新"}
    })
