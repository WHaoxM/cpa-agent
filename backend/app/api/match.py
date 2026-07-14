"""
匹配与分析 API 路由
/api/match — 四维匹配、技能差距、职业路径推荐
"""

import uuid
from flask import request, jsonify

from . import match_bp
from ._helpers import build_dimensions_d1_d4, build_sub_dimensions, parse_json_field, safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger
from ..exceptions import ErrorCode

logger = get_logger('cpa-agent.api.match')


@match_bp.route('/run', methods=['POST'])
def run_match():
    """执行人岗匹配 (L3 管线)"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    job_portrait_id = data.get('job_portrait_id') or data.get('job_id')

    if not student_id or not job_portrait_id:
        return jsonify({
            "success": False,
            "error": "student_id 和 job_portrait_id 必填"
        }), 400

    task_id = f"task_{uuid.uuid4().hex[:12]}"

    logger.info(f"触发人岗匹配: student_id={student_id}, job_portrait_id={job_portrait_id}")

    try:
        from ..services.tools.pipeline_run import PipelineRunTool

        pipeline_tool = PipelineRunTool()
        params = {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id
        }

        result = pipeline_tool._run_l3_run_match(params)

        if result.get("success"):
            return jsonify({
                "success": True,
                "data": {
                    "task_id": task_id,
                    "status": "completed",
                    "result": result
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("summary", "匹配执行失败")
            }), 500

    except Exception as e:
        logger.error(f"人岗匹配执行失败: {e}")
        return jsonify({
            "success": False,
            "error": "匹配执行失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@match_bp.route('/result/<student_id>/<job_portrait_id>', methods=['GET'])
def get_match_result(student_id: str, job_portrait_id: str):
    """获取匹配结果"""
    try:
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
            mr.d5_match,
            mr.d6_match,
            mr.d7_match,
            mr.d8_match,
            mr.d9_match,
            mr.d10_match,
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
            "data": {
                "student_id": result['student_id'],
                "student_name": result.get('student_name'),
                "school": result.get('school'),
                "job_portrait_id": result['job_portrait_id'],
                "position_name": result.get('position_name'),
                "dimensions": dimensions,
                "sub_dimensions": build_sub_dimensions(
                    result.get('d2_match', 0),
                    result.get('d5_match') or 0,
                    result.get('d6_match') or 0,
                    result.get('d7_match') or 0,
                    result.get('d8_match') or 0,
                    result.get('d9_match') or 0,
                    result.get('d10_match') or 0,
                ),
                "total_score": result.get('total_score', 0),
                "is_matched": result.get('is_matched', False),
                "match_level": result.get('match_level'),
                "created_at": result.get('created_at')
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取匹配结果", e)), 500


@match_bp.route('/skill-gap/<student_id>/<job_portrait_id>', methods=['GET'])
def get_skill_gap(student_id: str, job_portrait_id: str):
    """获取技能差距分析"""
    try:
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

        import json
        gap_json = gap.get('gap_json')
        gaps = parse_json_field(gap_json, default=[])
        if not isinstance(gaps, list):
            gaps = []

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
                "created_at": gap.get('created_at')
            }
        })

    except Exception as e:
        logger.error(f"获取技能差距失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@match_bp.route('/career-recommendation/<student_id>', methods=['GET'])
def get_career_recommendation(student_id: str):
    """获取职业发展推荐"""
    try:
        mysql = get_mysql_client()

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

    except Exception as e:
        logger.error(f"获取职业推荐失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@match_bp.route('/list/<student_id>', methods=['GET'])
def list_match_results(student_id: str):
    """获取学生的所有匹配结果"""
    try:
        mysql = get_mysql_client()

        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        sql = """
        SELECT
            mr.id,
            mr.student_id,
            mr.job_portrait_id,
            mr.d1_match,
            mr.d2_match,
            mr.d3_match,
            mr.d4_match,
            mr.d5_match,
            mr.d6_match,
            mr.d7_match,
            mr.d8_match,
            mr.d9_match,
            mr.d10_match,
            mr.total_score,
            mr.is_matched,
            mr.match_level,
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

        # 构造 sub_dimensions 字段 (与 /result 端点保持一致)
        for row in results:
            row['sub_dimensions'] = build_sub_dimensions(
                row.get('d2_match', 0),
                row.get('d5_match') or 0,
                row.get('d6_match') or 0,
                row.get('d7_match') or 0,
                row.get('d8_match') or 0,
                row.get('d9_match') or 0,
                row.get('d10_match') or 0,
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

    except Exception as e:
        return jsonify(safe_error("获取匹配结果列表", e)), 500
