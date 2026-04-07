"""
知识图谱相关 API 路由
/api/graph — Neo4j 图谱查询、构建、可视化数据
"""

from flask import request, jsonify

from . import graph_bp
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.graph')


@graph_bp.route('/overview', methods=['GET'])
def graph_overview():
    """获取图谱概览统计 (节点数/边数/类型分布)"""
    # TODO: 从 Neo4j 查询图谱统计信息
    return jsonify({
        "success": True,
        "data": {
            "node_count": 0,
            "edge_count": 0,
            "node_types": {},
            "edge_types": {}
        }
    })


@graph_bp.route('/query', methods=['POST'])
def graph_query():
    """执行 Cypher 查询 (仅限只读)"""
    data = request.get_json()
    cypher = data.get('cypher', '')

    if not cypher:
        return jsonify({"success": False, "error": "cypher 参数不能为空"}), 400

    # TODO: 执行 Neo4j 只读查询, 返回节点+边
    return jsonify({
        "success": True,
        "data": {"nodes": [], "edges": []}
    })


@graph_bp.route('/career-path/<job_id>', methods=['GET'])
def career_path(job_id: str):
    """查询岗位的晋升路径和换岗路径"""
    # TODO: Cypher 查询 promotion_edges + transfer_edges
    return jsonify({
        "success": True,
        "data": {
            "promotion_path": [],
            "transfer_options": []
        }
    })


@graph_bp.route('/skill-neighbors/<skill_name>', methods=['GET'])
def skill_neighbors(skill_name: str):
    """查询技能节点的邻居 (相关技能/岗位/前置技能)"""
    # TODO: Cypher MATCH (s:Skill {name: $name})-[r]-(n) RETURN s, r, n
    return jsonify({
        "success": True,
        "data": {"center": skill_name, "neighbors": []}
    })


@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """触发知识图谱构建 (L1 管线)"""
    # TODO: 异步触发 L1 构建, 返回 task_id
    return jsonify({
        "success": True,
        "data": {"task_id": "", "status": "pending"}
    })
