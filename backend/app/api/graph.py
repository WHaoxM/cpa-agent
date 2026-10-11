"""
知识图谱相关 API 路由
/api/graph — 当前只开放 CareerNode 公共路径查询；共享 Skill/JobPortrait 读面停用

安全说明:
- 所有 Cypher 查询必须使用预编译模板,不允许动态拼接
- 预编译模板定义在 SAFE_CYPHER_TEMPLATES 中
"""

from flask import request, jsonify

from . import graph_bp
from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from ..services.db import get_neo4j_client
from ..utils.logger import get_logger
from ..services.public_skill_graph import query_public_skills

logger = get_logger('cpa-agent.api.graph')

SAFE_CYPHER_TEMPLATES = {
    "career_path": {
        "cypher": """
        MATCH path=(c1:CareerNode)-[:PROMOTION*1..3]->(c2:CareerNode)
        WHERE c1.position_name = $position_name AND NOT c1:Person AND NOT c2:Person
          AND all(n IN nodes(path) WHERE labels(n) = ['CareerNode'])
        RETURN c1.position_name as start, c2.position_name as end, length(path) as steps
        ORDER BY steps
        LIMIT 20
        """,
        "params": ["position_name"]
    },
}

# JobPortrait is also created from runtime JD input without an owner field.
# Public API cannot safely distinguish seeded portraits from private ones.
DISABLED_PRIVATE_TEMPLATES = frozenset({
    "job_portrait", "promotion_chains", "transfer_options", "job_requirements",
    "skill_by_category",
    "skill_prerequisite",
})

PUBLIC_NODE_FIELDS = frozenset({
    "position_name", "level", "family",
})
PUBLIC_NODE_LABELS = frozenset({"CareerNode"})
PUBLIC_REL_TYPES = frozenset({"PROMOTION", "TRANSFER"})
PUBLIC_EDGE_FIELDS = frozenset({"years", "skill_delta"})
PUBLIC_SCALAR_FIELDS = frozenset({"start", "end", "steps"})


def _node_labels(node) -> list:
    labels = getattr(node, "labels", ())
    return list(labels() if callable(labels) else labels)


def _public_node_labels(node) -> list:
    labels = _node_labels(node)
    return labels if labels and set(labels) <= PUBLIC_NODE_LABELS else []


def _relationship_type(relation) -> str:
    rel_type = getattr(relation, "type", "")
    return str(rel_type() if callable(rel_type) else rel_type)


def _public_properties(value, fields: frozenset) -> dict:
    return {
        key: val for key, val in value.items()
        if key in fields and isinstance(val, (str, int, float, bool))
    }


def _execute_safe_query(template_name: str, params: dict) -> list:
    """执行预编译安全查询"""
    template = SAFE_CYPHER_TEMPLATES.get(template_name)
    if not template:
        raise ValueError(f"未知查询模板: {template_name}")

    required_params = set(template["params"])
    provided_params = set(params.keys())
    if required_params != provided_params:
        raise ValueError(f"参数不匹配: 需要 {required_params}, 提供 {provided_params}")

    neo4j = get_neo4j_client()
    return neo4j.execute_query(template["cypher"], params, read_only=True)


@graph_bp.route('/overview', methods=['GET'])
def graph_overview():
    """Whole-database counts include Graph B; no public-scope count exists yet."""
    return jsonify({
        "success": False,
        "error": "整库概览含未分区的个人图谱，已停用；待公共图谱标记/隔离后恢复",
    }), 410


@graph_bp.route('/query', methods=['POST'])
def graph_query():
    """
    执行预定义 Cypher 查询模板

    Body: {"template": "career_path", "params": {"position_name": "初级前端工程师"}}
    Other historical templates return 410 until public/private graph provenance exists.
    """
    data = request.get_json() or {}
    template_name = data.get('template', '')
    params = data.get('params', {})

    if not template_name:
        return jsonify({
            "success": False,
            "error": "template 参数必填"
        }), 400

    if template_name in {"skills", "all_skills", "skill_neighbors", "skill_categories"}:
        template_name = "skills" if template_name == "all_skills" else template_name
        if template_name == "skill_neighbors" and isinstance(params, dict) and "skill_name" in params:
            params = {"entity_name" if k == "skill_name" else k: v for k, v in params.items()}
        return _public_skill_response(template_name, params)

    if template_name in DISABLED_PRIVATE_TEMPLATES:
        return jsonify({
            "success": False,
            "error": "该 Skill/JobPortrait 查询没有可验证的公共/用户归属边界，已停用；仅可查询 CareerNode 公共路径",
        }), 410

    if template_name not in SAFE_CYPHER_TEMPLATES:
        return jsonify({
            "success": False,
            "error": f"未知模板: {template_name}"
        }), 400

    try:
        results = _execute_safe_query(template_name, params)

        nodes = []
        edges = []
        scalar_rows = []

        for record in results:
            scalar_row = {
                key: value for key, value in record.items()
                if key in PUBLIC_SCALAR_FIELDS
                and (value is None or isinstance(value, (str, int, float, bool)))
            }
            if scalar_row:
                scalar_rows.append(scalar_row)
            for key, value in record.items():
                if hasattr(value, 'labels'):
                    labels = _public_node_labels(value)
                    if not labels:
                        continue
                    nodes.append({
                        "id": value.element_id,
                        "labels": labels,
                        "properties": _public_properties(value, PUBLIC_NODE_FIELDS),
                    })
                elif hasattr(value, 'type'):
                    rel_type = _relationship_type(value)
                    if (rel_type not in PUBLIC_REL_TYPES
                            or not _public_node_labels(value.start_node)
                            or not _public_node_labels(value.end_node)):
                        continue
                    edges.append({
                        "id": value.element_id,
                        "type": rel_type,
                        "start": value.start_node.get('name', value.start_node.element_id),
                        "end": value.end_node.get('name', value.end_node.element_id),
                        "properties": _public_properties(value, PUBLIC_EDGE_FIELDS),
                    })
                elif isinstance(value, list):
                    for item in value:
                        if hasattr(item, 'labels'):
                            labels = _public_node_labels(item)
                            if not labels:
                                continue
                            nodes.append({
                                "id": item.element_id,
                                "labels": labels,
                                "properties": _public_properties(item, PUBLIC_NODE_FIELDS),
                            })
                        elif hasattr(item, 'type'):
                            rel_type = _relationship_type(item)
                            if (rel_type not in PUBLIC_REL_TYPES
                                    or not _public_node_labels(item.start_node)
                                    or not _public_node_labels(item.end_node)):
                                continue
                            edges.append({
                                "id": item.element_id,
                                "type": rel_type,
                                "start": item.start_node.get('name', item.start_node.element_id),
                                "end": item.end_node.get('name', item.end_node.element_id),
                                "properties": _public_properties(item, PUBLIC_EDGE_FIELDS),
                            })

        unique_nodes = {n['id']: n for n in nodes}.values()
        unique_edges = [e for e in edges if e['id']]

        return jsonify({
            "success": True,
            "data": {
                "nodes": list(unique_nodes),
                "edges": unique_edges,
                "total_nodes": len(unique_nodes),
                "total_edges": len(unique_edges),
                "rows": scalar_rows[:50],
                "total_rows": len(results),
            }
        })

    except Exception as e:
        return jsonify(safe_error("Cypher 查询", e, message="查询执行失败，请稍后重试")), 500


@graph_bp.route('/career-path/<job_id>', methods=['GET'])
def career_path(job_id: str):
    """Legacy JobPortrait path has no owner contract; use public CareerNode query."""
    return jsonify({
        "success": False,
        "error": "JobPortrait 路径查询已停用；请调用 /api/graph/query 的 career_path 模板并传 position_name",
    }), 410


@graph_bp.route('/skill-neighbors/<path:skill_name>', methods=['GET'])
def skill_neighbors(skill_name: str):
    """Only the isolated seed partition; no fallback to legacy Skill."""
    return _public_skill_response("skill_neighbors", {"entity_name": skill_name})


def _public_skill_response(template, params):
    try:
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        result = query_public_skills(template, params, client=get_neo4j_client())
        return jsonify({"success": result["success"], "data": result}), 200 if result["success"] else 503
    except ValueError:
        return jsonify({"success": False, "error": "公共技能查询参数无效"}), 400
    except Exception as exc:
        return jsonify(safe_error("公共技能查询", exc, message="公共技能分区暂不可用")), 503


@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """触发知识图谱构建 (L1 管线)"""
    data = request.get_json() or {}
    build_type = data.get('type', 'full')
    jd_texts = data.get('jd_texts', [])
    profile_text = data.get('profile_text', '')

    try:
        scope = resolve_request_scope()
    except ScopeError as error:
        return scope_error_response(error)

    try:
        from ..services.pipeline import PipelineRunner, PipelineLevel

        params = {
            "type": build_type,
            # Never trust body.student_id for a user-owned graph mutation.
            "student_id": scope.student_id,
            "jd_texts": jd_texts,
            "profile_text": profile_text
        }

        runner = PipelineRunner()
        result = runner.run(PipelineLevel.L1_BUILD_GRAPH.value, params)

        task_id = result.get("task_id") if isinstance(result, dict) else None
        if not task_id:
            return jsonify({"success": False, "error": "图谱构建未返回任务记录"}), 500
        rows = runner.mysql.execute(
            "SELECT task_id, status FROM pipeline_task "
            "WHERE task_id=:task_id AND student_id=:student_id LIMIT 1",
            {"task_id": task_id, "student_id": scope.student_id},
        )
        if not rows:
            return jsonify({"success": False, "error": "图谱任务归属或持久化状态无法核验"}), 500
        status = rows[0].get("status")

        return jsonify({
            "success": status == "completed",
            "data": {
                "task_id": task_id,
                "status": status,
                "type": build_type,
                "result": result,
                "message": "图谱构建完成" if status == "completed" else "图谱任务未完成"
            }
        })

    except Exception as e:
        return jsonify(safe_error("图谱构建触发", e, message="任务创建失败，请稍后重试")), 500


@graph_bp.route('/skill-categories', methods=['GET'])
def skill_categories():
    """Count only source-verified PublicSkill nodes, never all legacy nodes."""
    return _public_skill_response("skill_categories", {})


@graph_bp.route('/job-requirements/<job_id>', methods=['GET'])
def job_requirements(job_id: str):
    """Legacy JobPortrait lookup lacks owner metadata and is fail-closed."""
    return jsonify({
        "success": False,
        "error": "JobPortrait 岗位画像缺少公共/归属标记，要求查询已停用",
    }), 410
