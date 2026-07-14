"""
Pipeline_ext Portrait 子模块

L2 画像存储与查询 API (4 路由):
- POST /save/student-portrait      保存学生画像到 MySQL
- GET  /portrait/job/list          查询岗位画像列表
- POST /portrait/compare           对比两个画像
- GET  /portrait/<portrait_id>/radar  获取雷达图数据 (ECharts 格式)

依赖: ..services.db (MySQL)
"""

import json

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/save/student-portrait', methods=['POST'])
def save_student_portrait():
    """
    L2: 保存学生画像到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "target_job_id": "portrait_python_backend_50",
            "d1_score": 80, "d2_score": 75, "d3_score": 70, "d4_score": 68,
            "skill_coverage": 0.8,
            "skill_count": 15,
            "competitiveness_lvl": "良好"
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    if not student_id:
        return jsonify({"success": False, "error": "student_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        d1_score = data.get('d1_score', 0)
        d2_score = data.get('d2_score', 0)
        d3_score = data.get('d3_score', 0)
        d4_score = data.get('d4_score', 0)

        competitiveness = (
            d1_score * 0.25 +
            d2_score * 0.35 +
            d3_score * 0.20 +
            d4_score * 0.20
        )

        portrait_json = {
            "d1_score": d1_score,
            "d2_score": d2_score,
            "d3_score": d3_score,
            "d4_score": d4_score,
            "competitiveness": competitiveness,
            "skill_coverage": data.get('skill_coverage', 0),
            "skill_count": data.get('skill_count', 0),
            "competitiveness_lvl": data.get('competitiveness_lvl', ''),
            "skill_categories": data.get('skill_categories', {})
        }

        sql = """
        INSERT INTO student_portrait_4d (
            student_id, target_job_id,
            d1_score, d2_score, d3_score, d4_score,
            competitiveness, competitiveness_lvl, completeness,
            skill_coverage, skill_count,
            portrait_json, created_at, updated_at
        ) VALUES (
            :student_id, :target_job_id,
            :d1_score, :d2_score, :d3_score, :d4_score,
            :competitiveness, :competitiveness_lvl, :completeness,
            :skill_coverage, :skill_count,
            :portrait_json, NOW(), NOW()
        )
        ON DUPLICATE KEY UPDATE
            d1_score = VALUES(d1_score),
            d2_score = VALUES(d2_score),
            d3_score = VALUES(d3_score),
            d4_score = VALUES(d4_score),
            competitiveness = VALUES(competitiveness),
            skill_coverage = VALUES(skill_coverage),
            portrait_json = VALUES(portrait_json),
            updated_at = NOW()
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "target_job_id": data.get('target_job_id', ''),
            "d1_score": d1_score,
            "d2_score": d2_score,
            "d3_score": d3_score,
            "d4_score": d4_score,
            "competitiveness": round(competitiveness, 1),
            "competitiveness_lvl": data.get('competitiveness_lvl', ''),
            "completeness": data.get('completeness', 0.8),
            "skill_coverage": data.get('skill_coverage', 0),
            "skill_count": data.get('skill_count', 0),
            "portrait_json": json.dumps(portrait_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "competitiveness": round(competitiveness, 1),
                "saved": True
            }
        })

    except Exception as e:
        return jsonify(safe_error("保存学生画像", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/portrait/job/list', methods=['GET'])
def list_job_portraits():
    """
    L2: 查询岗位画像列表

    Query:
        ?limit=10&offset=0&category=Python
    """
    limit = request.args.get('limit', 10, type=int)
    offset = request.args.get('offset', 0, type=int)
    category = request.args.get('category', '')

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if category:
            sql = """
            SELECT portrait_id, position_name, jd_count,
                   required_degree, required_skill_cnt,
                   d1_weight, d2_weight, d3_weight, d4_weight,
                   created_at
            FROM job_portrait_4d
            WHERE position_name LIKE :category
            ORDER BY jd_count DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "category": f"%{category}%",
                "limit": limit,
                "offset": offset
            })
        else:
            sql = """
            SELECT portrait_id, position_name, jd_count,
                   required_degree, required_skill_cnt,
                   d1_weight, d2_weight, d3_weight, d4_weight,
                   created_at
            FROM job_portrait_4d
            ORDER BY jd_count DESC
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
        return jsonify(safe_error("查询岗位画像列表", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/portrait/compare', methods=['POST'])
def compare_portraits():
    """
    L2: 对比两个画像

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50"
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

        student_sql = """
        SELECT d1_score, d2_score, d3_score, d4_score, competitiveness
        FROM student_portrait_4d
        WHERE student_id = :student_id
        ORDER BY created_at DESC LIMIT 1
        """
        student_row = mysql.execute_single(student_sql, {"student_id": student_id})

        job_sql = """
        SELECT position_name, d1_weight, d2_weight, d3_weight, d4_weight
        FROM job_portrait_4d
        WHERE portrait_id = :portrait_id
        """
        job_row = mysql.execute_single(job_sql, {"portrait_id": job_portrait_id})

        if not student_row or not job_row:
            return jsonify({
                "success": False,
                "error": "学生画像或岗位画像不存在"
            }), 404

        student_scores = {
            "D1": student_row.get('d1_score', 0),
            "D2": student_row.get('d2_score', 0),
            "D3": student_row.get('d3_score', 0),
            "D4": student_row.get('d4_score', 0)
        }

        weights = {
            "D1": job_row.get('d1_weight', 0.25),
            "D2": job_row.get('d2_weight', 0.35),
            "D3": job_row.get('d3_weight', 0.20),
            "D4": job_row.get('d4_weight', 0.20)
        }

        comparison = {}
        suggestions = []

        for dim in ["D1", "D2", "D3", "D4"]:
            student_score = student_scores[dim]
            weight = weights[dim]

            comparison[dim] = {
                "student_score": student_score,
                "weight": weight,
                "weighted_score": round(student_score * weight, 1)
            }

            if dim == "D1" and student_score < 60:
                suggestions.append("D1 基础要求偏低，建议提升学历或专业匹配度")
            elif dim == "D2" and student_score < 70:
                suggestions.append("D2 职业技能有待提升，建议加强技能学习")
            elif dim == "D3" and student_score < 65:
                suggestions.append("D3 职业素养需要加强，注重软技能培养")
            elif dim == "D4" and student_score < 60:
                suggestions.append("D4 发展潜力有提升空间，建议参与更多项目竞赛")

        total_weighted = sum(c["weighted_score"] for c in comparison.values())

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "position_name": job_row.get('position_name', ''),
                "student_scores": student_scores,
                "weights": weights,
                "comparison": comparison,
                "total_weighted_score": round(total_weighted, 1),
                "suggestions": suggestions
            }
        })

    except Exception as e:
        return jsonify(safe_error("画像对比", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/portrait/<portrait_id>/radar', methods=['GET'])
def get_portrait_radar(portrait_id: str):
    """
    L2: 获取雷达图数据 (ECharts 格式)

    Path:
        portrait_id: 学生或岗位画像 ID
    """
    portrait_type = request.args.get('type', 'student')

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if portrait_type == 'student':
            sql = """
            SELECT d1_score, d2_score, d3_score, d4_score, skill_coverage
            FROM student_portrait_4d
            WHERE student_id = :portrait_id
            ORDER BY created_at DESC LIMIT 1
            """
            row = mysql.execute_single(sql, {"portrait_id": portrait_id})
        else:
            sql = """
            SELECT
                required_degree,
                required_skill_cnt,
                d1_weight * 100 as d1_score,
                d2_weight * 100 as d2_score,
                d3_weight * 100 as d3_score,
                d4_weight * 100 as d4_score,
                jd_count as skill_coverage
            FROM job_portrait_4d
            WHERE portrait_id = :portrait_id
            """
            row = mysql.execute_single(sql, {"portrait_id": portrait_id})

        if not row:
            return jsonify({
                "success": False,
                "error": "画像不存在"
            }), 404

        radar_data = {
            "indicators": [
                {"name": "D1 基础要求", "value": round(row.get('d1_score', 0), 1)},
                {"name": "D2 职业技能", "value": round(row.get('d2_score', 0), 1)},
                {"name": "D3 职业素养", "value": round(row.get('d3_score', 0), 1)},
                {"name": "D4 发展潜力", "value": round(row.get('d4_score', 0), 1)}
            ],
            "max_value": 100,
            "type": portrait_type
        }

        return jsonify({
            "success": True,
            "data": radar_data
        })

    except Exception as e:
        return jsonify(safe_error("获取雷达图数据", e, message="服务器内部错误")), 500
