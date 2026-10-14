"""
匹配与分析 API 路由
/api/match — 四维匹配、技能差距、职业路径推荐、hybrid 召回、可解释匹配

匹配总分由规则评分器计算；候选检索和结果解释不修改该分数。
"""

from flask import request, jsonify

from . import match_bp
from ._helpers import (
    ScopeError,
    build_dimensions_d1_d4,
    clamp_pagination,
    parse_json_field,
    require_student_path,
    resolve_request_scope,
    safe_error,
    scope_error_response,
    sub_dimensions_from_payload,
)
from ..config import Config
from ..services.db import get_mysql_client
from ..services.job_source_listing import is_source_only_id
from ..services.hybrid_retrieve import (
    CandidateCatalogUnavailable, build_match_explain, hybrid_recommend,
)
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.match')


def _stale_match_response(mysql, student_id: str, job_portrait_id: str, match_id):
    """Gate legacy score/explanation projections on the current L3 evidence."""
    if Config.DEMO_MODE:
        return None
    if match_id is None:
        return jsonify({"success": False, "status": "stale", "data": {
            "job_portrait_id": job_portrait_id,
            "reason": "match_result_unavailable",
            "next_action": "run_match",
        }}), 409
    from ..services.agent_runtime.match_report_resources import read_match_report_resource
    resource = read_match_report_resource(
        mysql, student_id=student_id, uri=f"career://me/matches/{match_id}")
    if not resource.get("business_artifact_verified"):
        return jsonify({"success": False, "status": "stale", "data": {
            "resource_id": str(match_id), "job_portrait_id": job_portrait_id,
            "reason": resource.get("reason"), "next_action": resource.get("next_action"),
        }}), 409
    return None


@match_bp.route('/run', methods=['POST'])
def run_match():
    """执行人岗匹配 (L3 管线)"""
    try:
        student_id = resolve_request_scope().student_id
    except ScopeError as e:
        return scope_error_response(e)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
    job_portrait_id = data.get('job_portrait_id') or data.get('job_id')

    if (not isinstance(job_portrait_id, str) or not job_portrait_id.strip()
            or len(job_portrait_id) > 64):
        return jsonify({
            "success": False,
            "error": "job_portrait_id 必须是 1–64 字符的非空文本"
        }), 400
    job_portrait_id = job_portrait_id.strip()
    if is_source_only_id(job_portrait_id):
        return jsonify({"success": False, "code": "job_source_only_unscored",
                        "error": "该岗位仅供查看官方来源，暂不可评分或匹配"}), 409

    logger.info(f"触发人岗匹配: student_id={student_id}, job_portrait_id={job_portrait_id}")

    try:
        from ..services.pipeline import PipelineLevel, PipelineRunner

        params = {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id
        }
        result = PipelineRunner().run(PipelineLevel.L3_RUN_MATCH, params)
        status = result.get("status") if isinstance(result, dict) else None
        if status == "completed":
            rows = get_mysql_client().fetch_all(
                "SELECT status FROM pipeline_task WHERE task_id=:task_id AND student_id=:student_id LIMIT 1",
                {"task_id": result["task_id"], "student_id": student_id},
            )
            if not rows or rows[0].get("status") != "completed":
                status = "unverified"
        elif status not in {"partial", "failed", "needs_input"}:
            status = "unverified"
        return jsonify({"success": status == "completed", "status": status, "data": {
            "task_id": result["task_id"], "status": status, "result": result,
        }})

    except Exception as e:
        return jsonify(safe_error("人岗匹配执行", e, message="匹配执行失败，请稍后重试")), 500


@match_bp.route('/result/<student_id>/<job_portrait_id>', methods=['GET'])
def get_match_result(student_id: str, job_portrait_id: str):
    """获取匹配结果"""
    try:
        require_student_path(student_id, resolve_request_scope())
        mysql = get_mysql_client()

        sql = """
        SELECT
            mr.id,
            mr.student_id,
            mr.job_portrait_id,
            mr.d1_match,
            mr.d2_match,
            mr.d3_match,
            mr.d4_match,
            mr.total_score,
            mr.d1_pass,
            mr.skill_coverage,
            mr.is_matched,
            mr.match_level,
            mr.result_json,
            mr.created_at,
            sp.name as student_name,
            sp.school,
            jp.position_name
        FROM match_result mr
        LEFT JOIN student_profile sp ON mr.student_id = sp.student_id
        LEFT JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id
        WHERE mr.student_id = :student_id AND mr.job_portrait_id = :job_portrait_id
        """

        results = mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id
        })

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到匹配结果: student={student_id}, job={job_portrait_id}"
            }), 404

        result = results[0]

        # A legacy row is not by itself an authoritative current match.  The
        # shared resource reader checks its task, digest, owner and all source
        # versions (including a revoked job publication) before exposing scores.
        from ..services.agent_runtime.match_report_resources import read_match_report_resource
        resource = read_match_report_resource(
            mysql, student_id=student_id, uri=f"career://me/matches/{result['id']}")
        if not resource.get("business_artifact_verified"):
            return jsonify({"success": False, "status": "stale", "data": {
                "resource_id": str(result["id"]), "job_portrait_id": job_portrait_id,
                "reason": resource.get("reason"), "next_action": resource.get("next_action"),
            }}), 409

        result['result_json'] = parse_json_field(result.get('result_json'))

        # 构造 D1-D4 维度 (match 特有 passed/skill_coverage 字段在 helper 返回基础上补充)
        dimensions = build_dimensions_d1_d4(
            result.get('d1_match', 0),
            result.get('d2_match', 0),
            result.get('d3_match', 0),
            result.get('d4_match', 0),
        )
        dimensions["D1"]["passed"] = result.get('d1_pass', False)
        dimensions["D2"]["skill_coverage"] = result.get('skill_coverage', 0)

        return jsonify({
            "success": True,
            "status": resource["status"],
            "data": {
                "student_id": result['student_id'],
                "student_name": result.get('student_name'),
                "school": result.get('school'),
                "job_portrait_id": result['job_portrait_id'],
                "position_name": result.get('position_name'),
                "dimensions": dimensions,
                # 七维子分未落列；从 result_json 取，缺省为 0（schema 仅存 D1–D4）
                "sub_dimensions": sub_dimensions_from_payload(
                    result.get('d2_match', 0),
                    result.get('result_json'),
                ),
                "total_score": result.get('total_score', 0),
                "is_matched": result.get('is_matched', False),
                "match_level": result.get('match_level'),
                "status": resource["status"],
                "score_kind": resource.get("data", {}).get("score_kind"),
                "quality_status": resource.get("data", {}).get("quality_status"),
                "created_at": result.get('created_at')
            }
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取匹配结果", e)), 500


@match_bp.route('/skill-gap/<student_id>/<job_portrait_id>', methods=['GET'])
def get_skill_gap(student_id: str, job_portrait_id: str):
    """获取技能差距分析"""
    try:
        require_student_path(student_id, resolve_request_scope())
        mysql = get_mysql_client()

        sql = """
        SELECT
            sg.id,
            sg.student_id,
            sg.job_portrait_id,
            sg.critical_count,
            sg.quick_learn_count,
            sg.total_gap_count,
            sg.gap_json,
            sg.created_at,
            jp.position_name
        FROM skill_gap sg
        LEFT JOIN job_portrait_4d jp ON sg.job_portrait_id = jp.portrait_id
        WHERE sg.student_id = :student_id AND sg.job_portrait_id = :job_portrait_id
        """

        results = mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id
        })

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到技能差距分析: student={student_id}, job={job_portrait_id}"
            }), 404

        gap = results[0]

        # Enrich with explain fields (does not change stored scores)
        mr_rows = mysql.execute(
            """
            SELECT id, total_score, skill_coverage, result_json
            FROM match_result
            WHERE student_id = :student_id AND job_portrait_id = :job_portrait_id
            LIMIT 1
            """,
            {"student_id": student_id, "job_portrait_id": job_portrait_id},
        ) or []
        mr = mr_rows[0] if mr_rows else {}
        stale = _stale_match_response(mysql, student_id, job_portrait_id, mr.get("id"))
        if stale is not None:
            return stale
        gaps = parse_json_field(gap.get('gap_json'), default=[])
        if not isinstance(gaps, list):
            gaps = []
        result_json = parse_json_field(mr.get("result_json"), default={}) or {}
        matched = result_json.get("matched_skills") or []
        missing = result_json.get("missing_skills") or [
            g.get("skill") for g in gaps if isinstance(g, dict) and g.get("skill")
        ]
        explain = build_match_explain(
            student_id=student_id,
            job_portrait_id=job_portrait_id,
            matched_skills=matched,
            missing_skills=missing,
            total_score=mr.get("total_score"),
            skill_coverage=mr.get("skill_coverage"),
            position_name=gap.get("position_name"),
            gaps=gaps,
        )

        return jsonify({
            "success": True,
            "data": {
                "student_id": gap['student_id'],
                "job_portrait_id": gap['job_portrait_id'],
                "position_name": gap.get('position_name'),
                "critical_count": gap.get('critical_count', 0),
                "quick_learn_count": gap.get('quick_learn_count', 0),
                "total_gap_count": gap.get('total_gap_count', 0),
                "gaps": gaps,
                "created_at": gap.get('created_at'),
                "keyword_coverage": explain.get("keyword_coverage"),
                "evidence": explain.get("evidence"),
                "graph_paths": explain.get("graph_paths"),
                "related_courses": explain.get("related_courses"),
            }
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取技能差距", e, message="查询失败，请稍后重试")), 500


@match_bp.route('/explain/<student_id>/<job_portrait_id>', methods=['GET'])
def explain_match(student_id: str, job_portrait_id: str):
    """可解释匹配：evidence + 关键词覆盖 + 图谱路径；分数只读自 match_result。"""
    try:
        require_student_path(student_id, resolve_request_scope())
        mysql = get_mysql_client()
        mr_rows = mysql.execute(
            """
            SELECT
                mr.id,
                mr.total_score,
                mr.skill_coverage,
                mr.result_json,
                mr.is_matched,
                mr.match_level,
                jp.position_name
            FROM match_result mr
            LEFT JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id
            WHERE mr.student_id = :student_id AND mr.job_portrait_id = :job_portrait_id
            LIMIT 1
            """,
            {"student_id": student_id, "job_portrait_id": job_portrait_id},
        ) or []
        if not mr_rows:
            return jsonify({
                "success": False,
                "error": f"未找到匹配结果: student={student_id}, job={job_portrait_id}",
            }), 404

        mr = mr_rows[0]
        stale = _stale_match_response(mysql, student_id, job_portrait_id, mr.get("id"))
        if stale is not None:
            return stale
        result_json = parse_json_field(mr.get("result_json"), default={}) or {}
        matched = result_json.get("matched_skills") or []
        missing = result_json.get("missing_skills") or result_json.get("missing_required") or []

        gap_rows = mysql.execute(
            """
            SELECT gap_json FROM skill_gap
            WHERE student_id = :student_id AND job_portrait_id = :job_portrait_id
            LIMIT 1
            """,
            {"student_id": student_id, "job_portrait_id": job_portrait_id},
        ) or []
        gaps = []
        if gap_rows:
            gaps = parse_json_field(gap_rows[0].get("gap_json"), default=[]) or []
            if not isinstance(gaps, list):
                gaps = []
            if not missing:
                missing = [
                    g.get("skill") for g in gaps if isinstance(g, dict) and g.get("skill")
                ]

        explain = build_match_explain(
            student_id=student_id,
            job_portrait_id=job_portrait_id,
            matched_skills=matched,
            missing_skills=missing,
            total_score=mr.get("total_score"),
            skill_coverage=mr.get("skill_coverage"),
            position_name=mr.get("position_name"),
            gaps=gaps,
        )
        explain["is_matched"] = mr.get("is_matched")
        explain["match_level"] = mr.get("match_level")
        return jsonify({"success": True, "data": explain})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("匹配解释", e, message="查询失败，请稍后重试")), 500


@match_bp.route('/hybrid-candidates', methods=['POST'])
def hybrid_candidates():
    """Hybrid 召回候选（向量+关键词+图 hop）。返回 retrieve_score，不含确定性 match 总分。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是 JSON 对象"}), 400
    skills = data.get("skills")
    if (not isinstance(skills, list) or not 1 <= len(skills) <= 40
            or any(not ((isinstance(item, str) and 1 <= len(item.strip()) <= 100)
                        or (isinstance(item, dict) and set(item) == {"name"}
                            and isinstance(item["name"], str)
                            and 1 <= len(item["name"].strip()) <= 100)) for item in skills)):
        return jsonify({"success": False, "error": "skills 必须是 1 至 40 个非空技能名"}), 400
    preferred_role = data.get("preferred_role", data.get("role"))
    if preferred_role is not None and (not isinstance(preferred_role, str)
                                       or not 1 <= len(preferred_role.strip()) <= 200):
        return jsonify({"success": False, "error": "preferred_role 必须是非空文本"}), 400
    if isinstance(preferred_role, str):
        preferred_role = preferred_role.strip()
    top_k = data.get("top_k", 10)
    if type(top_k) is not int or not 1 <= top_k <= 50:
        return jsonify({"success": False, "error": "top_k 必须是 1 至 50 的整数"}), 400
    enable_graph = data.get("enable_graph_hop", True)
    if type(enable_graph) is not bool:
        return jsonify({"success": False, "error": "enable_graph_hop 必须是布尔值"}), 400
    try:
        payload = hybrid_recommend(
            skills,
            preferred_role=preferred_role,
            top_k=top_k,
            enable_graph_hop=enable_graph,
        )
        return jsonify({"success": True, "data": payload})
    except CandidateCatalogUnavailable:
        logger.exception("Hybrid 候选目录不可校验")
        return jsonify({"success": False, "status": "unavailable",
                        "error": "候选岗位来源暂不可校验，请稍后重试"}), 503
    except Exception as e:
        return jsonify(safe_error("hybrid 召回", e, message="召回失败，请稍后重试")), 500


@match_bp.route('/career-recommendation/<student_id>', methods=['GET'])
def get_career_recommendation(student_id: str):
    """读取职业路径；严格模式仅可用已核验 L3 + 公共职业图作参考。"""
    try:
        require_student_path(student_id, resolve_request_scope())
        mysql = get_mysql_client()

        if not Config.DEMO_MODE:
            return _verified_career_path(mysql, student_id)

        job_portrait_id = request.args.get('job_portrait_id')

        if job_portrait_id:
            sql = """
            SELECT
                cp.id,
                cp.student_id,
                cp.job_portrait_id,
                cp.recommended_dir,
                cp.promotion_steps,
                cp.lateral_count,
                cp.path_json,
                cp.created_at,
                jp.position_name
            FROM career_path cp
            LEFT JOIN job_portrait_4d jp ON cp.job_portrait_id = jp.portrait_id
            WHERE cp.student_id = :student_id AND cp.job_portrait_id = :job_portrait_id
            """
            results = mysql.execute(sql, {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id
            })
        else:
            sql = """
            SELECT
                cp.id,
                cp.student_id,
                cp.job_portrait_id,
                cp.recommended_dir,
                cp.promotion_steps,
                cp.lateral_count,
                cp.path_json,
                cp.created_at,
                jp.position_name
            FROM career_path cp
            LEFT JOIN job_portrait_4d jp ON cp.job_portrait_id = jp.portrait_id
            WHERE cp.student_id = :student_id
            ORDER BY cp.created_at DESC
            LIMIT 1
            """
            results = mysql.execute(sql, {"student_id": student_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到职业路径: student={student_id}"
            }), 404

        path = results[0]

        import json
        path_json = path.get('path_json')
        paths = parse_json_field(path_json, default=[])
        if not isinstance(paths, list):
            paths = []

        return jsonify({
            "success": True,
            "data": {
                "student_id": path['student_id'],
                "job_portrait_id": path.get('job_portrait_id'),
                "position_name": path.get('position_name'),
                "recommended_dir": path.get('recommended_dir'),
                "promotion_steps": path.get('promotion_steps', 0),
                "lateral_options": path.get('lateral_count', 0),
                "paths": paths,
                "created_at": path.get('created_at')
            }
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取职业推荐", e, message="查询失败，请稍后重试")), 500


def _verified_career_path(mysql, student_id: str):
    """Never promote a legacy client-written career_path row to verified advice."""
    job_portrait_id = request.args.get("job_portrait_id")
    if job_portrait_id is not None:
        if not job_portrait_id.strip() or len(job_portrait_id) > 64:
            return jsonify({"success": False, "error": "job_portrait_id 必须是 1–64 字符的非空文本"}), 400
        job_portrait_id = job_portrait_id.strip()
        rows = mysql.execute(
            "SELECT id, job_portrait_id FROM match_result "
            "WHERE student_id=:student_id AND job_portrait_id=:job_portrait_id "
            "ORDER BY id DESC LIMIT 1",
            {"student_id": student_id, "job_portrait_id": job_portrait_id},
        ) or []
    else:
        rows = mysql.execute(
            "SELECT id, job_portrait_id FROM match_result "
            "WHERE student_id=:student_id ORDER BY id DESC LIMIT 1",
            {"student_id": student_id},
        ) or []
    if not rows:
        return jsonify({"success": False, "status": "not_available", "data": {
            "reason": "verified_match_unavailable", "next_action": "run_match",
        }}), 404

    row = rows[0]
    from ..services.agent_runtime.match_report_resources import read_match_report_resource
    resource = read_match_report_resource(
        mysql, student_id=student_id, uri=f"career://me/matches/{row['id']}")
    if not resource.get("business_artifact_verified"):
        return jsonify({"success": False, "status": "stale", "data": {
            "resource_id": str(row["id"]), "job_portrait_id": row.get("job_portrait_id"),
            "reason": resource.get("reason"), "next_action": resource.get("next_action"),
        }}), 409

    position_name = (resource.get("data") or {}).get("position_name")
    if not isinstance(position_name, str) or not position_name.strip():
        return jsonify({"success": False, "status": "stale", "data": {
            "resource_id": str(row["id"]), "reason": "verified_job_name_unavailable",
            "next_action": "run_match",
        }}), 409

    data = {
        "student_id": student_id, "job_portrait_id": row["job_portrait_id"],
        "source_match_id": str(row["id"]), "position_name": position_name,
        "recommended_dir": None, "promotion_steps": None, "lateral_options": None,
        "paths": [], "path_kind": "public_generic_promotion_reference",
        "personalized_recommendation_verified": False,
        "basis": "verified_l3_job_snapshot_and_public_career_node_exact_name",
        "coverage": "partial", "unavailable_sources": [],
    }
    # Reuse the same bounded public-only template as /api/graph/query. No
    # private Person/Skill/JobPortrait fallback or job-title similarity guess.
    try:
        from .graph import _execute_safe_query
        graph_rows = _execute_safe_query("career_path", {"position_name": position_name})
    except Exception as exc:
        logger.warning("公共职业路径暂不可用: %s", type(exc).__name__)
        data.update({"reason": "public_career_graph_unavailable",
                     "unavailable_sources": ["public_career_graph"],
                     "next_action": "retry_public_career_graph"})
        return jsonify({"success": True, "status": "partial", "data": data})

    data["paths"] = [
        {"start": position_name, "end": item["end"], "steps": item["steps"]}
        for item in graph_rows
        if isinstance(item, dict) and item.get("start") == position_name
        and isinstance(item.get("end"), str) and item["end"].strip()
        and isinstance(item.get("steps"), int) and 1 <= item["steps"] <= 3
    ]
    if not data["paths"]:
        data.update({"reason": "exact_public_promotion_path_unavailable",
                     "next_action": "review_public_career_mapping"})
    return jsonify({"success": True, "status": "partial", "data": data})


@match_bp.route('/list/<student_id>', methods=['GET'])
def list_match_results(student_id: str):
    """获取学生的所有匹配结果"""
    try:
        require_student_path(student_id, resolve_request_scope())
        mysql = get_mysql_client()

        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

        sql = """
        SELECT
            mr.id,
            mr.student_id,
            mr.job_portrait_id,
            mr.d1_match,
            mr.d2_match,
            mr.d3_match,
            mr.d4_match,
            mr.total_score,
            mr.is_matched,
            mr.match_level,
            mr.result_json,
            mr.created_at,
            jp.position_name,
            sp.name as student_name
        FROM match_result mr
        LEFT JOIN student_profile sp ON mr.student_id = sp.student_id
        LEFT JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id
        WHERE mr.student_id = :student_id
        ORDER BY mr.total_score DESC
        LIMIT :limit OFFSET :offset
        """

        results = mysql.execute(sql, {
            "student_id": student_id,
            "limit": limit,
            "offset": offset
        })

        # Legacy rows can outlive the job publication/profile sources used to
        # calculate them. Reuse the authoritative resource reader before any
        # score or result_json is returned from this collection endpoint.
        if not Config.DEMO_MODE:
            from ..services.agent_runtime.match_report_resources import read_match_report_resource
            for row in results:
                resource = read_match_report_resource(
                    mysql, student_id=student_id,
                    uri=f"career://me/matches/{row['id']}",
                )
                if not resource.get("business_artifact_verified"):
                    return jsonify({"success": False, "status": "stale", "data": {
                        "resource_id": str(row["id"]),
                        "job_portrait_id": row.get("job_portrait_id"),
                        "reason": resource.get("reason"),
                        "next_action": resource.get("next_action"),
                    }}), 409

        # 构造 sub_dimensions 字段 (与 /result 端点保持一致)
        for row in results:
            row['result_json'] = parse_json_field(row.get('result_json'))
            row['sub_dimensions'] = sub_dimensions_from_payload(
                row.get('d2_match', 0),
                row.get('result_json'),
            )

        count_sql = "SELECT COUNT(*) as total FROM match_result WHERE student_id = :student_id"
        count_result = mysql.execute(count_sql, {"student_id": student_id})
        total = count_result[0]['total'] if count_result else 0

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取匹配结果列表", e)), 500
