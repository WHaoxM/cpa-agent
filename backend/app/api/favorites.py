"""
Favorites API - 收藏相关 API
"""

import uuid
import json
from flask import request, jsonify

from . import favorites_bp
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
        logger.error(f"获取收藏岗位失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@favorites_bp.route('/saved-jobs', methods=['POST'])
def save_job():
    """收藏岗位"""
    try:
        data = request.get_json()
        mysql = get_mysql_client()

        job_id = f"saved_{uuid.uuid4().hex[:12]}"
        sql = """
        INSERT INTO saved_job
        (id, user_id, job_title, company, industry, salary, location, match_score, required_skills, saved_at)
        VALUES (:id, :user_id, :job_title, :company, :industry, :salary, :location, :match_score, :required_skills, NOW())
        """

        required_skills = data.get('required_skills', [])
        if isinstance(required_skills, list):
            required_skills = json.dumps(required_skills)

        mysql.execute_write(sql, {
            "id": job_id,
            "user_id": data.get('user_id'),
            "job_title": data.get('job_title'),
            "company": data.get('company'),
            "industry": data.get('industry'),
            "salary": data.get('salary'),
            "location": data.get('location'),
            "match_score": data.get('match_score', 0),
            "required_skills": required_skills
        })

        return jsonify({"success": True, "data": {"id": job_id, "message": "岗位已收藏"}})

    except Exception as e:
        logger.error(f"收藏岗位失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@favorites_bp.route('/saved-jobs/<job_id>', methods=['DELETE'])
def unsave_job(job_id: str):
    """取消收藏"""
    try:
        mysql = get_mysql_client()
        sql = "DELETE FROM saved_job WHERE id = :job_id"
        mysql.execute_write(sql, {"job_id": job_id})

        return jsonify({"success": True, "data": {"message": f"岗位 {job_id} 已取消收藏"}})

    except Exception as e:
        logger.error(f"取消收藏失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        logger.error(f"获取目标职业失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@favorites_bp.route('/target-roles', methods=['POST'])
def add_target_role():
    """添加目标职业方向"""
    try:
        data = request.get_json()
        mysql = get_mysql_client()

        sql = "INSERT INTO target_role (user_id, role, saved_at) VALUES (:user_id, :role, NOW())"
        mysql.execute_write(sql, {"user_id": data.get('user_id'), "role": data.get('role')})

        return jsonify({"success": True, "data": {"message": "目标职业已添加"}})

    except Exception as e:
        logger.error(f"添加目标职业失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@favorites_bp.route('/target-roles/<role>', methods=['DELETE'])
def remove_target_role(role: str):
    """移除目标职业方向"""
    try:
        mysql = get_mysql_client()
        user_id = request.args.get('user_id')

        sql = "DELETE FROM target_role WHERE role = :role AND user_id = :user_id"
        mysql.execute_write(sql, {"role": role, "user_id": user_id})

        return jsonify({"success": True, "data": {"message": f"目标职业 {role} 已移除"}})

    except Exception as e:
        logger.error(f"移除目标职业失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@favorites_bp.route('/target-role-market/<role>', methods=['GET'])
def get_role_market(role: str):
    """获取职业市场信息"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            role,
            AVG(salary_min) as avg_salary_min,
            AVG(salary_max) as avg_salary_max,
            COUNT(*) as demand_count
        FROM job_posting
        WHERE position_name LIKE :role
        GROUP BY role
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
        logger.error(f"获取职业市场信息失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
