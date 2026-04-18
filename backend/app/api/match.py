"""
匹配与分析 API 路由
/api/match — 四维匹配、技能差距、职业路径推荐
"""

import uuid
from flask import request, jsonify

from . import match_bp
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

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

    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "pending",
            "message": "人岗匹配任务已创建，请通过 task_id 查询进度"
        }
    })


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

        import json
        result_json = result.get('result_json')
        if isinstance(result_json, str):
            try:
                result['result_json'] = json.loads(result_json)
            except:
                pass

        return jsonify({
            "success": True,
            "data": {
                "student_id": result['student_id'],
                "student_name": result.get('student_name'),
                "school": result.get('school'),
                "job_portrait_id": result['job_portrait_id'],
                "position_name": result.get('position_name'),
                "dimensions": {
                    "D1": {
                        "name": "基础要求",
                        "score": result.get('d1_match', 0),
                        "passed": result.get('d1_pass', False),
                        "description": "学历、学校、专业等基础条件是否满足"
                    },
                    "D2": {
                        "name": "职业技能",
                        "score": result.get('d2_match', 0),
                        "skill_coverage": result.get('skill_coverage', 0),
                        "description": "技术技能匹配程度"
                    },
                    "D3": {
                        "name": "职业素养",
                        "score": result.get('d3_match', 0),
                        "description": "沟通、团队、领导力等软技能"
                    },
                    "D4": {
                        "name": "发展潜力",
                        "score": result.get('d4_match', 0),
                        "description": "学习能力、成长空间、潜力评估"
                    }
                },
                "total_score": result.get('total_score', 0),
                "is_matched": result.get('is_matched', False),
                "match_level": result.get('match_level'),
                "created_at": result.get('created_at')
            }
        })

    except Exception as e:
        logger.error(f"获取匹配结果失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


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
        gaps = []
        if isinstance(gap_json, str):
            try:
                gaps = json.loads(gap_json)
            except:
                pass
        elif isinstance(gap_json, list):
            gaps = gap_json

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
            "error": f"查询失败: {str(e)}"
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
        paths = []
        if isinstance(path_json, str):
            try:
                paths = json.loads(path_json)
            except:
                pass
        elif isinstance(path_json, list):
            paths = path_json

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
            "error": f"查询失败: {str(e)}"
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
        logger.error(f"获取匹配结果列表失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500
