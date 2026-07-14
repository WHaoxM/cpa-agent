"""
Favorites API - 收藏相关 API
"""

import uuid
import json
from flask import request, jsonify

from . import favorites_bp
from ._helpers import safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.favorites')


@favorites_bp.route('/saved-jobs', methods=['GET'])
def list_saved_jobs():
    """获取收藏的岗位"""
    try:
        mysql = get_mysql_client()
        user_id = request.args.get('user_id')

        if user_id:
            sql = "SELECT * FROM saved_job WHERE user_id = :user_id ORDER BY saved_at DESC"
            results = mysql.execute(sql, {"user_id": user_id})
        else:
            sql = "SELECT * FROM saved_job ORDER BY saved_at DESC"
            results = mysql.execute(sql)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        return jsonify(safe_error("获取收藏岗位", e)), 500


@favorites_bp.route('/saved-jobs', methods=['POST'])
def save_job():
    """收藏岗位"""
    try:
        data = request.get_json() or {}
        mysql = get_mysql_client()

        job_id = data.get('job_id') or f"job_{uuid.uuid4().hex[:12]}"
        sql = """
        INSERT INTO saved_job
        (job_id, user_id, job_title, company, industry, salary, location, match_score, required_skills)
        VALUES (:job_id, :user_id, :job_title, :company, :industry, :salary, :location, :match_score, :required_skills)
        """

        required_skills = data.get('required_skills', [])
        if isinstance(required_skills, list):
            required_skills = json.dumps(required_skills, ensure_ascii=False)

        mysql.execute_write(sql, {
            "job_id": job_id,
            "user_id": data.get('user_id'),
            "job_title": data.get('job_title'),
            "company": data.get('company'),
            "industry": data.get('industry'),
            "salary": data.get('salary'),
            "location": data.get('location'),
            "match_score": data.get('match_score', 0),
            "required_skills": required_skills
        })

        return jsonify({"success": True, "data": {"job_id": job_id, "message": "岗位已收藏"}})

    except Exception as e:
        return jsonify(safe_error("收藏岗位", e)), 500


@favorites_bp.route('/saved-jobs/<job_id>', methods=['DELETE'])
def unsave_job(job_id: str):
    """取消收藏"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM saved_job WHERE job_id = :job_id"
        mysql.execute_write(sql, {"job_id": job_id})

        return jsonify({"success": True, "data": {"message": f"岗位 {job_id} 已取消收藏"}})

    except Exception as e:
        return jsonify(safe_error("取消收藏", e)), 500


@favorites_bp.route('/target-roles', methods=['GET'])
def list_target_roles():
    """获取目标职业方向"""
    try:
        mysql = get_mysql_client()
        user_id = request.args.get('user_id')

        if user_id:
            sql = "SELECT * FROM target_role WHERE user_id = :user_id"
            results = mysql.execute(sql, {"user_id": user_id})
        else:
            sql = "SELECT * FROM target_role"
            results = mysql.execute(sql)

        return jsonify({"success": True, "data": results})

    except Exception as e:
        return jsonify(safe_error("获取目标职业", e)), 500


@favorites_bp.route('/target-roles', methods=['POST'])
def add_target_role():
    """添加目标职业方向"""
    try:
        data = request.get_json() or {}
        mysql = get_mysql_client()

        sql = "INSERT INTO target_role (user_id, role_name, priority) VALUES (:user_id, :role_name, :priority)"
        mysql.execute_write(sql, {
            "user_id": data.get('user_id'),
            "role_name": data.get('role') or data.get('role_name'),
            "priority": data.get('priority', 1),
        })

        return jsonify({"success": True, "data": {"message": "目标职业已添加"}})

    except Exception as e:
        return jsonify(safe_error("添加目标职业", e)), 500


@favorites_bp.route('/target-roles/<role>', methods=['DELETE'])
def remove_target_role(role: str):
    """移除目标职业方向"""
    try:
        mysql = get_mysql_client()
        user_id = request.args.get('user_id')

        sql = "DELETE FROM target_role WHERE role_name = :role AND user_id = :user_id"
        mysql.execute_write(sql, {"role": role, "user_id": user_id})

        return jsonify({"success": True, "data": {"message": f"目标职业 {role} 已移除"}})

    except Exception as e:
        return jsonify(safe_error("移除目标职业", e)), 500


@favorites_bp.route('/target-role-market/<role>', methods=['GET'])
def get_role_market(role: str):
    """获取职业市场信息"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            position_name as role,
            AVG(salary_min) as avg_salary_min,
            AVG(salary_max) as avg_salary_max,
            COUNT(*) as demand_count
        FROM job_posting
        WHERE position_name LIKE :role AND is_it_position = 1
        GROUP BY position_name
        """

        results = mysql.execute(sql, {"role": f"%{role}%"})

        if results:
            market = results[0]
            return jsonify({
                "success": True,
                "data": {
                    "role": role,
                    "salaryRange": f"{int(market['avg_salary_min'])}K-{int(market['avg_salary_max'])}K",
                    "medianSalary": int((market['avg_salary_min'] + market['avg_salary_max']) / 2),
                    "demandLevel": "高" if market['demand_count'] > 100 else "中",
                    "demandCount": market['demand_count']
                }
            })

        return jsonify({
            "success": True,
            "data": {
                "role": role,
                "salaryRange": "15K-30K",
                "medianSalary": 22000,
                "demandLevel": "中",
                "demandCount": 50
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取职业市场信息", e)), 500
