"""Read-only public facts; never upgrades reference jobs to scored publications."""
from flask import jsonify, request
from . import career_bp
from ..services.db import get_mysql_client, get_neo4j_client, get_milvus_client
from ..services.career_pathway import read_pathway
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.career_pathway')


def _still_current(payload):
    latest = read_pathway(get_mysql_client(), payload['role_id'])
    return latest is not None and all(latest[k] == payload[k] for k in ('revision', 'content_sha256'))


@career_bp.route('/role-pathways/<role_id>', methods=['GET'])
def get_role_pathway(role_id):
    try:
        p = read_pathway(get_mysql_client(), role_id)
        if p is None:
            return jsonify(success=False, reason='pathway_not_found'), 404
        return jsonify(success=True, data=p)
    except Exception:
        logger.exception('Public pathway unavailable')
        return jsonify(success=False, reason='pathway_unavailable', message='职业路径暂不可用，请重试'), 503


@career_bp.route('/role-pathways/<role_id>/graph', methods=['GET'])
def get_role_pathway_graph(role_id):
    try:
        from ..services.career_pathway_projection import read_graph
        p = read_pathway(get_mysql_client(), role_id)
        if p is None:
            return jsonify(success=False, reason='pathway_not_found'), 404
        result = read_graph(get_neo4j_client(), p)
        if not _still_current(p):
            return jsonify(success=False, reason='current_revision_changed'), 503
        ready = result.get('status') == 'ready'
        return jsonify(success=ready, data=result), (200 if ready else 503)
    except Exception:
        logger.exception('Role graph unavailable')
        return jsonify(success=False, reason='graph_unavailable'), 503


@career_bp.route('/role-pathways/<role_id>/search', methods=['GET'])
def search_role_pathway(role_id):
    query = request.args.get('q', '').strip()
    if not 1 <= len(query) <= 160:
        return jsonify(success=False, reason='query_must_be_1_to_160_characters'), 400
    try:
        from ..services.career_pathway_projection import search_vectors
        p = read_pathway(get_mysql_client(), role_id)
        if p is None:
            return jsonify(success=False, reason='pathway_not_found'), 404
        result = search_vectors(get_milvus_client(), p, query, top_k=5)
        if not _still_current(p):
            return jsonify(success=False, reason='current_revision_changed'), 503
        ready = result.get('status') in {'ready', 'empty'}
        return jsonify(success=ready, data=result), (200 if ready else 503)
    except Exception:
        logger.exception('Role semantic search unavailable')
        return jsonify(success=False, reason='semantic_search_unavailable', message='语义检索暂不可用，目录仍可浏览'), 503
