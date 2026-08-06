"""
Pipeline_ext Match 子模块

L3 匹配结果存储与查询 API (6 路由):
- POST /save/match-result    保存匹配结果到 MySQL
- POST /save/skill-gap       保存技能差距分析到 MySQL
- POST /save/career-path     保存职业路径推荐到 MySQL
- POST /match/full           完整匹配管线 (匹配+差距+路径+存储)
- GET  /match/list           查询匹配结果列表
- GET  /match/trend          分析学生匹配历史趋势

依赖: ..services.db (get_mysql_client)
      ..pipeline.entity_extractor.matcher (calculate_match_score, _get_match_level)
      ..pipeline.entity_extractor.graph_storage (GraphStorage)
"""

import json

from flask import request, jsonify

from ._helpers import clamp_pagination, safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger, _normalize_skills


@pipeline_ext_bp.route('/save/match-result', methods=['POST'])
def save_match_result():
    """
    L3: 保存匹配结果到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "total_score": 73.8,
            "d1_score": 80, "d2_score": 75, "d3_score": 70, "d4_score": 68,
            "d1_pass": true,
            "skill_coverage": 0.8,
            "is_matched": true,
            "match_level": "良好"
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        result_json = {
            "d1_score": data.get('d1_score', 0),
            "d2_score": data.get('d2_score', 0),
            "d3_score": data.get('d3_score', 0),
            "d4_score": data.get('d4_score', 0),
            "total_score": data.get('total_score', 0),
            "skill_coverage": data.get('skill_coverage', 0),
            "d1_pass": data.get('d1_pass', False),
            "match_level": data.get('match_level', ''),
        }

        sql = """
        INSERT INTO match_result (
            student_id, job_portrait_id,
            d1_match, d2_match, d3_match, d4_match, total_score,
            d1_pass, skill_coverage, is_matched, match_level,
            result_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :d1_score, :d2_score, :d3_score, :d4_score, :total_score,
            :d1_pass, :skill_coverage, :is_matched, :match_level,
            :result_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            total_score = VALUES(total_score),
            skill_coverage = VALUES(skill_coverage),
            is_matched = VALUES(is_matched),
            result_json = VALUES(result_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "d1_score": data.get('d1_score', 0),
            "d2_score": data.get('d2_score', 0),
            "d3_score": data.get('d3_score', 0),
            "d4_score": data.get('d4_score', 0),
            "total_score": data.get('total_score', 0),
            "d1_pass": data.get('d1_pass', False),
            "skill_coverage": data.get('skill_coverage', 0),
            "is_matched": data.get('is_matched', False),
            "match_level": data.get('match_level', ''),
            "result_json": json.dumps(result_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        return jsonify(safe_error("保存匹配结果", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/save/skill-gap', methods=['POST'])
def save_skill_gap():
    """
    L3: 保存技能差距分析到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "critical_count": 3,
            "quick_learn_count": 5,
            "total_gap_count": 8,
            "gap_skills": [
                {"skill": "Kubernetes", "gap": 3, "priority": "critical"},
                {"skill": "Redis", "gap": 1, "priority": "quick_learn"}
            ]
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        gap_json = {
            "critical_count": data.get('critical_count', 0),
            "quick_learn_count": data.get('quick_learn_count', 0),
            "gap_skills": data.get('gap_skills', [])
        }

        sql = """
        INSERT INTO skill_gap (
            student_id, job_portrait_id,
            critical_count, quick_learn_count, total_gap_count,
            gap_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :critical_count, :quick_learn_count, :total_gap_count,
            :gap_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            critical_count = VALUES(critical_count),
            quick_learn_count = VALUES(quick_learn_count),
            total_gap_count = VALUES(total_gap_count),
            gap_json = VALUES(gap_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "critical_count": data.get('critical_count', 0),
            "quick_learn_count": data.get('quick_learn_count', 0),
            "total_gap_count": data.get('total_gap_count', 0),
            "gap_json": json.dumps(gap_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        return jsonify(safe_error("保存技能差距", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/save/career-path', methods=['POST'])
def save_career_path():
    """
    L3: 保存职业路径推荐到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "recommended_dir": "高级后端工程师",
            "promotion_steps": 4,
            "lateral_count": 3,
            "path_json": {...}
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        path_json = data.get('path_json', {})

        sql = """
        INSERT INTO career_path (
            student_id, job_portrait_id,
            recommended_dir, promotion_steps, lateral_count,
            path_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :recommended_dir, :promotion_steps, :lateral_count,
            :path_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            recommended_dir = VALUES(recommended_dir),
            promotion_steps = VALUES(promotion_steps),
            lateral_count = VALUES(lateral_count),
            path_json = VALUES(path_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "recommended_dir": data.get('recommended_dir', ''),
            "promotion_steps": data.get('promotion_steps', 0),
            "lateral_count": data.get('lateral_count', 0),
            "path_json": json.dumps(path_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        return jsonify(safe_error("保存职业路径", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/match/full', methods=['POST'])
def full_match_pipeline():
    """
    L3: 完整匹配管线 (匹配 + 差距分析 + 路径推荐 + 存储)

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "student_skills": [...],
            "job_skills": [...]
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_skills = _normalize_skills(data.get('job_skills', []))

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..pipeline.entity_extractor.matcher import calculate_match_score, _get_match_level

        match_result = calculate_match_score(student_skills, job_skills)

        skill_coverage = match_result.get('skill_coverage', 0)
        is_matched = skill_coverage >= 0.8 and match_result.get('d1_pass', False)

        # match_level 走 matcher._get_match_level (统一权威源, 消除硬编码不一致 bug)
        match_level = _get_match_level(match_result['total_score'])

        missing_skills = match_result.get('missing_skills', [])
        level_gaps = match_result.get('gap_priorities', [])

        gap_skills = []
        critical_count = 0
        quick_learn_count = 0

        for gap in level_gaps:
            skill_name = gap.get('skill', '')
            gap_value = gap.get('gap', 0)
            priority = gap.get('priority', 'quick_learn')

            gap_skills.append({
                "skill": skill_name,
                "gap": gap_value,
                "priority": priority
            })

            if priority == "critical":
                critical_count += 1
            else:
                quick_learn_count += 1

        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        career_info = storage.get_transfer_options(job_portrait_id.split('_')[1] if '_' in job_portrait_id else "工程师", min_jaccard=0.3)
        promotion_chains = storage.get_promotion_chains("中级工程师", min_chain_length=3)

        recommended_dir = promotion_chains[0]['positions'][-1] if promotion_chains else "高级工程师"
        promotion_steps = len(promotion_chains[0]['positions']) - 1 if promotion_chains else 3
        lateral_count = len(career_info)

        save_match_url = f"{request.host_url}api/pipeline/ext/save/match-result"
        save_gap_url = f"{request.host_url}api/pipeline/ext/save/skill-gap"
        save_path_url = f"{request.host_url}api/pipeline/ext/save/career-path"

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "match_result": {
                    "total_score": match_result['total_score'],
                    "d1_score": match_result['d1_score'],
                    "d2_score": match_result['d2_score'],
                    "d3_score": match_result['d3_score'],
                    "d4_score": match_result['d4_score'],
                    "skill_coverage": skill_coverage,
                    "d1_pass": match_result['d1_score'] >= 60,
                    "is_matched": is_matched,
                    "match_level": match_level,
                    "matched_skills": match_result.get('matched_skills', []),
                    "missing_skills": missing_skills
                },
                "skill_gap": {
                    "critical_count": critical_count,
                    "quick_learn_count": quick_learn_count,
                    "total_gap_count": len(gap_skills),
                    "gap_skills": gap_skills
                },
                "career_path": {
                    "recommended_dir": recommended_dir,
                    "promotion_steps": promotion_steps,
                    "lateral_count": lateral_count,
                    "promotion_chains": promotion_chains[:3],
                    "transfer_options": career_info[:5]
                },
                "save_commands": {
                    "match_result": f"POST {save_match_url}",
                    "skill_gap": f"POST {save_gap_url}",
                    "career_path": f"POST {save_path_url}"
                }
            }
        })

    except Exception as e:
        return jsonify(safe_error("完整匹配管线", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/match/list', methods=['GET'])
def list_match_results():
    """
    L3: 查询匹配结果列表

    Query:
        ?student_id=xxx&limit=10&offset=0
    """
    student_id = request.args.get('student_id', '')
    limit, offset = clamp_pagination(
        request.args.get('limit', type=int),
        request.args.get('offset', type=int),
        default_limit=10,
    )

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if student_id:
            sql = """
            SELECT id as match_id, student_id, job_portrait_id,
                   total_score,
                   d1_match as d1_score, d2_match as d2_score,
                   d3_match as d3_score, d4_match as d4_score,
                   is_matched, created_at
            FROM match_result
            WHERE student_id = :student_id
            ORDER BY total_score DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "student_id": student_id,
                "limit": limit,
                "offset": offset
            })
        else:
            sql = """
            SELECT id as match_id, student_id, job_portrait_id,
                   total_score,
                   d1_match as d1_score, d2_match as d2_score,
                   d3_match as d3_score, d4_match as d4_score,
                   is_matched, created_at
            FROM match_result
            ORDER BY total_score DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "limit": limit,
                "offset": offset
            })

        return jsonify({
            "success": True,
            "data": {
                "list": results,
                "count": len(results),
                "limit": limit,
                "offset": offset
            }
        })

    except Exception as e:
        return jsonify(safe_error("查询匹配结果列表", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/match/trend', methods=['GET'])
def get_match_trend():
    """
    分析学生匹配历史趋势

    Query:
        ?student_id=stu_001
    """
    student_id = request.args.get('student_id', '')

    if not student_id:
        return jsonify({"success": False, "error": "student_id 参数不能为空"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        sql = """
        SELECT mr.total_score,
               mr.d1_match as d1_score, mr.d2_match as d2_score,
               mr.d3_match as d3_score, mr.d4_match as d4_score,
               mr.skill_coverage, mr.is_matched, mr.match_level,
               mr.created_at,
               jp.position_name
        FROM match_result mr
        LEFT JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id
        WHERE mr.student_id = :student_id
        ORDER BY mr.created_at ASC
        LIMIT 50
        """
        records = mysql.fetch_all(sql, {"student_id": student_id})

        if not records:
            return jsonify({
                "success": True,
                "data": {
                    "student_id": student_id,
                    "record_count": 0,
                    "trend": "暂无匹配记录",
                    "suggestions": ["建议先进行人岗匹配测试"]
                }
            })

        total_scores = [r['total_score'] for r in records if r['total_score']]
        d1_scores = [r['d1_score'] for r in records if r['d1_score']]
        d2_scores = [r['d2_score'] for r in records if r['d2_score']]
        d3_scores = [r['d3_score'] for r in records if r['d3_score']]
        d4_scores = [r['d4_score'] for r in records if r['d4_score']]

        avg_total = round(sum(total_scores) / len(total_scores), 1) if total_scores else 0
        avg_d1 = round(sum(d1_scores) / len(d1_scores), 1) if d1_scores else 0
        avg_d2 = round(sum(d2_scores) / len(d2_scores), 1) if d2_scores else 0
        avg_d3 = round(sum(d3_scores) / len(d3_scores), 1) if d3_scores else 0
        avg_d4 = round(sum(d4_scores) / len(d4_scores), 1) if d4_scores else 0

        first_score = total_scores[0] if total_scores else 0
        last_score = total_scores[-1] if total_scores else 0
        score_change = round(last_score - first_score, 1) if total_scores else 0

        matched_count = sum(1 for r in records if r['is_matched'])
        match_rate = round(matched_count / len(records) * 100, 1) if records else 0

        suggestions = []
        if score_change > 5:
            suggestions.append("匹配分数持续上升，表现越来越出色")
        elif score_change < -5:
            suggestions.append("匹配分数有所下降，建议回顾近期简历更新")
        else:
            suggestions.append("匹配分数整体稳定")

        if avg_d1 < 60:
            suggestions.append("D1基础要求偏低，建议提升学历或专业匹配度")
        if avg_d2 < 70:
            suggestions.append("D2职业技能有待提升，建议加强技能学习")
        if avg_d3 < 65:
            suggestions.append("D3职业素养需要加强，注重软技能培养")
        if avg_d4 < 60:
            suggestions.append("D4发展潜力有提升空间，建议参与更多项目竞赛")
        if match_rate < 50:
            suggestions.append(f"历史匹配率仅{match_rate}%，建议拓宽岗位选择范围")
        else:
            suggestions.append(f"历史匹配率{match_rate}%，整体表现良好")

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "record_count": len(records),
                "avg_scores": {
                    "total": avg_total,
                    "d1": avg_d1,
                    "d2": avg_d2,
                    "d3": avg_d3,
                    "d4": avg_d4
                },
                "trend": {
                    "first_score": first_score,
                    "last_score": last_score,
                    "score_change": score_change,
                    "direction": "上升" if score_change > 2 else ("下降" if score_change < -2 else "平稳")
                },
                "match_rate": match_rate,
                "matched_count": matched_count,
                "history": [
                    {
                        "total_score": r['total_score'],
                        "position_name": r['position_name'] or '未知岗位',
                        "match_level": r['match_level'] or '',
                        "created_at": str(r['created_at'])[:19] if r['created_at'] else ''
                    }
                    for r in records[-10:]
                ],
                "suggestions": suggestions
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取匹配趋势", e, message="服务器内部错误")), 500
