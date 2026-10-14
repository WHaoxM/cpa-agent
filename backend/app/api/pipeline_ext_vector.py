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
- _generate_embeddings  生成 embedding 向量 (BGE-M3，失败即拒绝写入)

依赖: ..services.db (Milvus), ..services.embedding_service (BGE-M3)
"""

from typing import List, Tuple

from flask import request, jsonify, g

from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from .pipeline_ext_common import pipeline_ext_bp, logger
from ..services.hybrid_retrieve import CandidateCatalogUnavailable, project_public_jd_vector_hits


def _require_vector_admin():
    try:
        resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    if getattr(g, "cpa_admin", False) is not True:
        return jsonify({"success": False, "error": "公共向量索引写入需要服务端管理员权限"}), 403
    return None


def _generate_embeddings(texts: List[str]) -> Tuple[List[List[float]], bool]:
    """生成 embedding 向量 (使用 BGE-M3)

    Returns:
        (vectors, is_fallback): 向量列表；失败时抛错，不生成伪向量
    """
    try:
        from ..services.embedding_service import get_embedding_service
        service = get_embedding_service()
        return service.encode(texts), False
    except Exception:
        # A random vector is not a degraded semantic result: it poisons the
        # index and makes arbitrary retrieval appear successful.
        logger.exception("BGE-M3 模型不可用，拒绝生成伪向量")
        raise


@pipeline_ext_bp.route('/vector/store/skills', methods=['POST'])
def store_skill_vectors():
    """Reconcile the public skill index from a counted MySQL taxonomy snapshot.

    Browser-supplied text cannot enter this public index. The full reconcile
    is idempotent and repairs partial writes; it may take time after a source
    change, so operators should prefer the CLI for a large initial build.
    """
    denial = _require_vector_admin()
    if denial is not None:
        return denial
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
    expected_count = data.get("expected_source_count")
    if (set(data) != {"source", "expected_source_count"}
            or data.get("source") != "mysql.skill_taxonomy"
            or type(expected_count) is not int or not 1 <= expected_count <= 100000):
        return jsonify({"success": False,
                        "error": "仅接受 source=mysql.skill_taxonomy 和 1..100000 的 expected_source_count；不接受客户端技能正文"}), 400

    try:
        from ..services.db import get_milvus_client, get_mysql_client
        from ..services.embedding_service import get_embedding_service
        from ..services.skill_vector_index import reconcile_skill_vectors

        mysql = get_mysql_client()
        milvus = get_milvus_client()
        stats = reconcile_skill_vectors(mysql, milvus, get_embedding_service(),
                                        expected_source_count=expected_count)

        return jsonify({
            "success": True,
            "data": {"collection": "skill_vectors", "status": "verified",
                     "content_reconciled": True, **stats},
        })
    except ValueError as e:
        return jsonify({"success": False, "status": "conflict",
                        "error": str(e)}), 409
    except Exception as e:
        return jsonify(safe_error("存储技能向量", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/vector/store/jd', methods=['POST'])
def store_jd_vectors():
    """
    存储 JD 向量到 Milvus

    Body identifies an exact current publication. Text and title always come
    from the server-verified immutable publication snapshot; browser-provided
    chunks are never accepted as public index content.
    """
    denial = _require_vector_admin()
    if denial is not None:
        return denial
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
    # A strict allowlist prevents legacy callers from continuing to smuggle
    # their own public JD text under an otherwise valid publication ID.
    if set(data) != {"job_id", "publication_version", "content_sha256"}:
        return jsonify({"success": False, "error": "仅接受 job_id、publication_version、content_sha256；正文必须来自当前已发布快照"}), 400
    job_id = data.get("job_id")
    version = data.get("publication_version")
    expected_digest = data.get("content_sha256")
    if (not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 64
            or not isinstance(version, str) or not version.strip() or len(version) > 64
            or not isinstance(expected_digest, str) or len(expected_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_digest)):
        return jsonify({"success": False, "error": "岗位发布标识或摘要无效"}), 400

    try:
        from ..services.db import get_milvus_client
        from ..services.db import get_mysql_client
        from ..services.job_publication import read_published_job

        mysql = get_mysql_client()
        publication = read_published_job(mysql, job_id, version)
        current = read_published_job(mysql, job_id)
        if (publication is None or current is None
                or publication["id"] != current["id"]
                or publication["publication_version"] != version
                or current["publication_version"] != version
                or publication["content_sha256"] != expected_digest
                or current["content_sha256"] != expected_digest):
            return jsonify({"success": False, "error": "岗位不是请求版本的当前有效人工发布快照"}), 409
        snapshot = publication["snapshot_json"]
        source_text = snapshot.get("source_text") if isinstance(snapshot, dict) else None
        position_name = snapshot.get("position_name") if isinstance(snapshot, dict) else None
        if not isinstance(source_text, str) or not source_text.strip() or not isinstance(position_name, str):
            return jsonify({"success": False, "error": "已发布岗位快照缺少可索引来源正文"}), 409

        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "error": "Milvus 连接失败"}), 500

        # Keep the source as a single authoritative text value. Consumers use
        # metadata version+digest to bind a hit back to this exact snapshot.
        text_chunks = [source_text]
        metadata_list = [{
            "job_portrait_id": job_id,
            "position_name": position_name,
            "publication_version": version,
            "content_sha256": expected_digest,
        }]

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
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    data = request.get_json() or {}
    if data.get('student_id') and str(data['student_id']) != scope.student_id:
        return jsonify({"success": False, "error": "student_id 与当前 Principal 不匹配"}), 403
    # The current Milvus schema stores owner only in JSON metadata, which
    # cannot enforce a scalar owner filter at ANN query time. Do not add more
    # private data to this unpartitioned legacy collection.
    return jsonify({"success": False, "status": "unavailable",
                    "error": "简历向量写入暂停：等待可验证的 owner 分区和迁移"}), 501


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
    query = data.get('query', '')
    collection = data.get('collection', 'all')
    top_k = data.get('top_k', 5)

    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        return jsonify({"success": False, "error": "query 必须是 1 至 200 字符的文本"}), 400
    query = query.strip()
    if not isinstance(collection, str) or collection not in {"skill", "jd", "all"}:
        return jsonify({"success": False, "error": "此端点仅检索公共技能与岗位向量；个人向量检索尚未提供 owner 安全过滤"}), 403
    if type(top_k) is not int or not 1 <= top_k <= 50:
        return jsonify({"success": False, "error": "top_k 必须是 1 至 50 的整数"}), 400

    try:
        from ..services.db import get_milvus_client
        milvus = get_milvus_client()

        if not milvus.connect():
            return jsonify({"success": False, "status": "unavailable",
                            "error": "向量索引暂不可用，请稍后重试"}), 503

        vectors, _ = _generate_embeddings([query])
        if not vectors or not vectors[0]:
            return jsonify({"success": False, "status": "unavailable",
                            "error": "向量模型暂不可用，请稍后重试"}), 503
        query_vector = vectors[0]

        collection_map = {
            "skill": "skill_vectors",
            "jd": "jd_vectors",
        }

        if collection == "all":
            target_collections = list(collection_map.values())
        else:
            target_collections = [collection_map[collection]]

        all_results = {}
        failed_collections = []
        source_coverage = {}
        rejected_unverified_hits = {}
        for coll in target_collections:
            try:
                results = milvus.search(
                    collection_name=coll,
                    query_vector=query_vector,
                    top_k=min(250, top_k * 5) if coll == "skill_vectors" else top_k,
                    strict=True,
                )
                if coll == "skill_vectors":
                    from ..config import Config
                    from ..services.db import get_mysql_client
                    from ..services.skill_vector_index import project_public_skill_vector_hits
                    results, rejected = project_public_skill_vector_hits(
                        get_mysql_client(), results, Config.EMBEDDING_MODEL)
                    if rejected:
                        rejected_unverified_hits[coll] = rejected
                    results = results[:top_k]
                if results and coll == "jd_vectors":
                    try:
                        from ..services.db import get_mysql_client
                        results = project_public_jd_vector_hits(get_mysql_client(), results)
                    except CandidateCatalogUnavailable:
                        raise
                    except Exception as exc:
                        raise CandidateCatalogUnavailable("已发布岗位目录暂不可校验") from exc
                if results:
                    all_results[coll] = results
                source_coverage[coll] = ("partial" if rejected_unverified_hits.get(coll)
                                         else ("complete" if results else "empty"))
            except Exception as e:
                logger.warning(f"集合 {coll} 检索失败: {e}")
                failed_collections.append(coll)
                source_coverage[coll] = "unavailable"

        if len(failed_collections) == len(target_collections):
            return jsonify({"success": False, "status": "unavailable",
                            "failed_collections": failed_collections,
                            "error": "请求的向量索引暂不可用，请稍后重试"}), 503

        status = ("partial" if failed_collections or rejected_unverified_hits
                  else ("complete" if all_results else "empty"))

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "results": all_results,
                "status": status,
                "failed_collections": failed_collections,
                "rejected_unverified_hits": rejected_unverified_hits,
                "source_coverage": source_coverage,
                "next_action": ("修复或重建未验证的公共技能索引"
                                if rejected_unverified_hits else
                                ("稍后重试不可用的向量集合" if failed_collections else None)),
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
    denial = _require_vector_admin()
    if denial is not None:
        return denial
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
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    if data.get('student_id') and str(data['student_id']) != scope.student_id:
        return jsonify({"success": False, "error": "student_id 与当前 Principal 不匹配"}), 403
    student_id = scope.student_id
    messages = data.get('messages', [])

    if not messages:
        return jsonify({"success": False, "error": "messages 不能为空"}), 400

    from ..services.session_manager import get_session_manager
    session = get_session_manager().get_session(session_id) if session_id else None
    if session is None or str(getattr(session, 'student_id', '')) != student_id:
        return jsonify({"success": False, "error": "会话不存在"}), 404
    return jsonify({"success": False, "status": "unavailable",
                    "error": "会话向量写入暂停：等待可验证的 owner 分区和迁移"}), 501


@pipeline_ext_bp.route('/vector/rebuild', methods=['POST'])
def rebuild_vector_index():
    """
    重建 Milvus 向量索引

    Body:
        {
            "collection_name": "skill_vectors"  // 可选，默认 "skill_vectors"
        }
    """
    denial = _require_vector_admin()
    if denial is not None:
        return denial
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
