"""
Pipeline_ext Vector 子模块

Milvus 向量存储与检索 API (7 路由 + 1 私有 helper):
- POST /vector/store/skills        存储技能向量
- POST /vector/store/jd            存储 JD 向量
- POST /vector/store/resume        存储简历向量
- POST /vector/search              向量语义检索
- POST /vector/init                初始化 Milvus Collection
- POST /vector/store/conversation  存储对话记忆向量
- POST /vector/rebuild             重建 Milvus 向量索引

私有 helper:
- _generate_embeddings  生成 embedding 向量 (BGE-M3, 失败降级随机向量)

依赖: ..services.db (Milvus), ..services.embedding_service (BGE-M3)
"""

import random
import time
from typing import List, Tuple

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger


def _generate_embeddings(texts: List[str]) -> Tuple[List[List[float]], bool]:
    """生成 embedding 向量 (使用 BGE-M3)

    Returns:
        (vectors, is_fallback): 向量列表 + 是否使用了降级随机向量
    """
    try:
        from ..services.embedding_service import get_embedding_service
        service = get_embedding_service()
        return service.encode(texts), False
    except Exception as e:
        logger.warning(f"BGE-M3 模型加载失败，使用随机向量: {e}")
        return [[random.random() for _ in range(1024)] for _ in texts], True


@pipeline_ext_bp.route('/vector/store/skills', methods=['POST'])
def store_skill_vectors():
    """
    存储技能向量到 Milvus

    Body:
        {
            "skills": [
                {"name": "Python", "category": "编程语言", "description": "..."},
                ...
            ]
        }
    """
    data = request.get_json() or {}
    skills = data.get('skills', [])

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        texts = []
        metadata_list = []
        for skill in skills:
            text = skill.get("description", skill.get("name", ""))
            if not text:
                text = skill.get("name", "")
            texts.append(text)
            metadata_list.append({
                "skill_name": skill.get("name", ""),
                "category": skill.get("category", ""),
            })

        vectors, _ = _generate_embeddings(texts)

        ids = milvus.insert(
            collection_name="skill_vectors",
            texts=texts,
            vectors=vectors,
            metadata=metadata_list
        )

        return jsonify({
            "success": True,
            "data": {
                "collection": "skill_vectors",
                "count": len(ids),
                "ids": ids[:10] if len(ids) > 10 else ids
            }
        })

    except Exception as e:
        return jsonify(safe_error("存储技能向量", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/store/jd', methods=['POST'])
def store_jd_vectors():
    """
    存储 JD 向量到 Milvus

    Body:
        {
            "job_id": "job_001",
            "position_name": "Python后端开发",
            "text_chunks": ["负责后端开发...", "熟练掌握Python..."]
        }
    """
    data = request.get_json() or {}
    job_id = data.get('job_id', '')
    position_name = data.get('position_name', '')
    text_chunks = data.get('text_chunks', [])

    if not text_chunks:
        return jsonify({"success": False, "error": "text_chunks 不能为空"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        metadata_list = [{
            "job_id": job_id,
            "position_name": position_name,
        } for _ in text_chunks]

        vectors, _ = _generate_embeddings(text_chunks)

        ids = milvus.insert(
            collection_name="jd_vectors",
            texts=text_chunks,
            vectors=vectors,
            metadata=metadata_list
        )

        return jsonify({
            "success": True,
            "data": {
                "collection": "jd_vectors",
                "count": len(ids),
                "ids": ids[:10] if len(ids) > 10 else ids
            }
        })

    except Exception as e:
        return jsonify(safe_error("存储JD向量", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/store/resume', methods=['POST'])
def store_resume_vectors():
    """
    存储简历向量到 Milvus

    Body:
        {
            "student_id": "stu_001",
            "section_type": "education|work|project|skill",
            "text_chunks": ["XX大学计算机专业...", "项目经验..."]
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', '')
    section_type = data.get('section_type', '')
    text_chunks = data.get('text_chunks', [])

    if not text_chunks:
        return jsonify({"success": False, "error": "text_chunks 不能为空"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        metadata_list = [{
            "student_id": student_id,
            "section_type": section_type,
        } for _ in text_chunks]

        vectors, _ = _generate_embeddings(text_chunks)

        ids = milvus.insert(
            collection_name="resume_vectors",
            texts=text_chunks,
            vectors=vectors,
            metadata=metadata_list
        )

        return jsonify({
            "success": True,
            "data": {
                "collection": "resume_vectors",
                "count": len(ids),
                "ids": ids[:10] if len(ids) > 10 else ids
            }
        })

    except Exception as e:
        return jsonify(safe_error("存储简历向量", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/search', methods=['POST'])
def search_vectors():
    """
    向量语义检索

    Body:
        {
            "query": "Python后端开发技能",
            "collection": "skill|jd|resume|conversation|all",
            "top_k": 5
        }
    """
    data = request.get_json() or {}
    query = data.get('query', '')
    collection = data.get('collection', 'all')
    top_k = data.get('top_k', 5)

    if not query:
        return jsonify({"success": False, "error": "query 不能为空"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        query_vector, _ = _generate_embeddings([query])

        collection_map = {
            "skill": "skill_vectors",
            "jd": "jd_vectors",
            "resume": "resume_vectors",
            "conversation": "conversation_vectors",
        }

        if collection == "all":
            target_collections = list(collection_map.values())
        else:
            target_collections = [collection_map.get(collection, collection)]

        all_results = {}
        for coll in target_collections:
            try:
                results = milvus.search(
                    collection_name=coll,
                    query_vector=query_vector,
                    top_k=top_k,
                )
                if results:
                    all_results[coll] = results
            except Exception as e:
                logger.warning(f"集合 {coll} 检索失败: {e}")

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "results": all_results
            }
        })

    except Exception as e:
        return jsonify(safe_error("向量检索", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/init', methods=['POST'])
def init_vector_collections():
    """
    初始化 Milvus Collection

    Body:
        {
            "collections": ["skill_vectors", "jd_vectors", "resume_vectors", "conversation_vectors"]
        }
    """
    data = request.get_json() or {}
    collections = data.get('collections', ["skill_vectors", "jd_vectors", "resume_vectors", "conversation_vectors"])

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        results = {}
        for coll in collections:
            try:
                milvus.create_collection(
                    collection_name=coll,
                    dimension=1024,
                    description=f"{coll} collection",
                    metric_type="COSINE"
                )
                results[coll] = "created or exists"
            except Exception as e:
                results[coll] = "error: 创建失败"

        return jsonify({
            "success": True,
            "data": results
        })

    except Exception as e:
        return jsonify(safe_error("初始化Collection", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/store/conversation', methods=['POST'])
def store_conversation_vectors():
    """
    存储对话记忆向量到 Milvus

    Body:
        {
            "session_id": "sess_001",
            "student_id": "stu_001",
            "messages": [
                {"role": "user", "content": "我想学习Python"},
                {"role": "assistant", "content": "Python是个好选择..."}
            ]
        }
    """
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    student_id = data.get('student_id', '')
    messages = data.get('messages', [])

    if not messages:
        return jsonify({"success": False, "error": "messages 不能为空"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        texts = []
        metadata_list = []
        for msg in messages:
            content = msg.get('content', '')
            if content:
                texts.append(content)
                metadata_list.append({
                    "session_id": session_id,
                    "student_id": student_id,
                    "role": msg.get('role', 'unknown'),
                    "timestamp": int(time.time())
                })

        if not texts:
            return jsonify({"success": False, "error": "消息内容为空"}), 400

        vectors, _ = _generate_embeddings(texts)

        ids = milvus.insert(
            collection_name="conversation_vectors",
            texts=texts,
            vectors=vectors,
            metadata=metadata_list
        )

        return jsonify({
            "success": True,
            "data": {
                "collection": "conversation_vectors",
                "count": len(ids),
                "ids": ids[:10] if len(ids) > 10 else ids
            }
        })

    except Exception as e:
        return jsonify(safe_error("存储对话向量", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/rebuild', methods=['POST'])
def rebuild_vector_index():
    """
    重建 Milvus 向量索引

    Body:
        {
            "collection_name": "skill_vectors"  // 可选，默认 "skill_vectors"
        }
    """
    data = request.get_json() or {}
    collection_name = data.get('collection_name', 'skill_vectors')

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        milvus.flush(collection_name=collection_name)

        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 128}
        }
        milvus.create_index(
            collection_name=collection_name,
            field_name="embedding",
            index_params=index_params
        )

        return jsonify({
            "success": True,
            "data": {
                "collection": collection_name,
                "status": "rebuilt",
                "index_type": "IVF_FLAT",
                "metric_type": "COSINE"
            }
        })

    except Exception as e:
        return jsonify(safe_error("重建向量索引", e, message="服务器内部错误")), 500
