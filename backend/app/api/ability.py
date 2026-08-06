"""
能力图谱只读 API — /api/ability-graph
"""

from flask import request, jsonify

from . import ability_bp
from ._helpers import safe_error
from ..services.ability_graph_service import get_ability_graph
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.api.ability")


@ability_bp.route("/ability-graph", methods=["GET"])
def ability_graph():
    """按 role 读取 Neo4j 能力富图；无数据返回 success=false（冒烟要求有图）。"""
    try:
        role = (request.args.get("role") or "").strip()
        if not role:
            return jsonify({"success": False, "error": "role required"}), 400
        data = get_ability_graph(role)
        if not data or not data.get("nodes"):
            return jsonify({
                "success": False,
                "error": f"ability graph empty for role={role}",
                "data": None,
            }), 404
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify(safe_error("get ability graph", e)), 500
