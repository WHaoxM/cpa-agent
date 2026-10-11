"""
Favorites API - 收藏相关 API
HTTP 层: 参数解析 + 调用 repository + 包装响应
数据访问见 repositories/favorites_repo.py

字段兼容: 同时接受 snake_case（权威）与 camelCase / role 别名，消除 FE field-mismatch。
"""

import json
from flask import request, jsonify

from . import favorites_bp
from ._helpers import ScopeError, parse_json_field, resolve_request_scope, safe_error, scope_error_response
from ..repositories.favorites_repo import favorites_repo
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.favorites')


def _pick(data: dict, *keys, default=None):
    """按优先级取第一个非空键。"""
    for key in keys:
        if key in data and data[key] is not None and data[key] != '':
            return data[key]
    return default


@favorites_bp.route('/saved-jobs', methods=['GET'])
def list_saved_jobs():
    """获取收藏的岗位"""
    try:
        user_id = resolve_request_scope().student_id
        results = favorites_repo.list_saved_jobs(user_id=user_id)
        return jsonify({"success": True, "data": results})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取收藏岗位", e)), 500


@favorites_bp.route('/saved-jobs', methods=['POST'])
def save_job():
    """收藏岗位（兼容 job_title / jobTitle、match_score / matchScore 等）"""
    try:
        scope = resolve_request_scope()
        data = request.get_json() or {}
        job_id = _pick(data, 'job_id', 'jobId')
        if not isinstance(job_id, str) or not job_id.strip():
            return jsonify({"success": False, "error": "job_id 必填，不能为收藏生成虚构岗位 ID"}), 400

        required_skills = _pick(data, 'required_skills', 'requiredSkills', default=[])
        if isinstance(required_skills, list):
            required_skills = json.dumps(required_skills, ensure_ascii=False)

        record_id = favorites_repo.save_job(
            job_id=job_id,
            user_id=scope.student_id,
            job_title=_pick(data, 'job_title', 'jobTitle'),
            company=data.get('company'),
            industry=data.get('industry'),
            salary=data.get('salary'),
            location=data.get('location'),
            match_score=_pick(data, 'match_score', 'matchScore', default=0),
            required_skills=required_skills,
        )

        saved = favorites_repo.get_saved_job(job_id, user_id=scope.student_id, record_id=record_id) if record_id else None
        if not saved or saved.get("id") != record_id:
            return jsonify({"success": False, "status": "unverified", "error": "收藏写入未通过回读核验"}), 503
        return jsonify({"success": True, "data": {"job_id": job_id, "saved_job_id": saved["id"],
                        "source_verified": False, "message": "岗位已收藏（用户提供的收藏信息，不代表岗位来源已认证）"}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("收藏岗位", e)), 500


@favorites_bp.route('/saved-jobs/<job_id>', methods=['DELETE'])
def unsave_job(job_id: str):
    """取消收藏"""
    try:
        user_id = resolve_request_scope().student_id
        if not favorites_repo.get_saved_job(job_id, user_id=user_id):
            return jsonify({"success": False, "error": "收藏不存在"}), 404
        favorites_repo.delete_saved_job(job_id, user_id=user_id)
        if favorites_repo.get_saved_job(job_id, user_id=user_id):
            return jsonify({"success": False, "error": "取消收藏未通过回读核验"}), 503
        return jsonify({"success": True, "data": {"message": f"岗位 {job_id} 已取消收藏"}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("取消收藏", e)), 500


@favorites_bp.route('/target-roles', methods=['GET'])
def list_target_roles():
    """获取目标职业方向"""
    try:
        user_id = resolve_request_scope().student_id
        results = favorites_repo.list_target_roles(user_id=user_id)
        return jsonify({"success": True, "data": results})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取目标职业", e)), 500


@favorites_bp.route('/target-roles', methods=['POST'])
def add_target_role():
    """添加目标职业方向（兼容 role_name / role）"""
    try:
        scope = resolve_request_scope()
        data = request.get_json() or {}
        role_name = _pick(data, 'role_name', 'role')
        if not role_name:
            return jsonify({"success": False, "error": "role_name 必填"}), 400

        record_id = favorites_repo.add_target_role(
            user_id=scope.student_id,
            role_name=role_name,
            priority=data.get('priority', 1),
        )
        saved = favorites_repo.get_target_role(role_name, user_id=scope.student_id, record_id=record_id) if record_id else None
        if not saved or saved.get("id") != record_id:
            return jsonify({"success": False, "status": "unverified", "error": "目标职业写入未通过回读核验"}), 503
        return jsonify({"success": True, "data": {"target_role_id": saved["id"], "message": "目标职业已添加"}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("添加目标职业", e)), 500


@favorites_bp.route('/target-roles/<role>', methods=['DELETE'])
def remove_target_role(role: str):
    """移除目标职业方向"""
    try:
        user_id = resolve_request_scope().student_id
        if not favorites_repo.get_target_role(role, user_id=user_id):
            return jsonify({"success": False, "error": "目标职业不存在"}), 404
        favorites_repo.delete_target_role(role_name=role, user_id=user_id)
        if favorites_repo.get_target_role(role, user_id=user_id):
            return jsonify({"success": False, "error": "移除目标职业未通过回读核验"}), 503
        return jsonify({"success": True, "data": {"message": f"目标职业 {role} 已移除"}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("移除目标职业", e)), 500


def _enrich_role_market(role: str, market: dict | None) -> dict:
    """拼出 FE TargetRoleMarket 全字段；薪资/需求来自 job_posting，技能/城市来自 career_insights。"""
    insight = favorites_repo.get_role_insight(role) or {}
    hot_cities = parse_json_field(insight.get('hot_cities')) or []
    industries = parse_json_field(insight.get('hot_industries')) or []
    skill_tags = parse_json_field(insight.get('core_skills')) or []
    sample_jobs = []
    if insight.get('role_name'):
        sample_jobs = [insight['role_name']]

    if market:
        demand_count = int(market.get('demand_count') or 0)
        if demand_count > 100:
            demand_level = "高"
        elif demand_count > 30:
            demand_level = "中"
        else:
            demand_level = "低"
        salary_min = float(market.get('avg_salary_min') or 0)
        salary_max = float(market.get('avg_salary_max') or 0)
        median = int((salary_min + salary_max) / 2) if (salary_min or salary_max) else 0
        salary_range = f"{int(salary_min)}K-{int(salary_max)}K"
        trend_note = insight.get('description') or f"相关岗位约 {demand_count} 条"
        reference = min(99, max(0, demand_count))
    else:
        demand_count = 0
        demand_level = insight.get('demand_level') or "暂无数据"
        salary_range = "—"
        median = 0
        if insight.get('avg_salary_low') is not None and insight.get('avg_salary_high') is not None:
            low = int(insight['avg_salary_low'])
            high = int(insight['avg_salary_high'])
            salary_range = f"{low}K-{high}K"
            median = int((low + high) / 2)
        trend_note = insight.get('description') or f"暂无岗位 {role} 的市场统计"
        reference = 0

    return {
        "role": role,
        "salaryRange": salary_range,
        "medianSalary": median,
        "demandLevel": demand_level,
        "demandCount": demand_count,
        "hotCities": hot_cities if isinstance(hot_cities, list) else [],
        "industries": industries if isinstance(industries, list) else [],
        "skillTags": skill_tags if isinstance(skill_tags, list) else [],
        "sampleJobs": sample_jobs,
        "trendNote": trend_note or "",
        "referenceMatch": reference,
    }


@favorites_bp.route('/target-role-market/<role>', methods=['GET'])
def get_role_market(role: str):
    """获取职业市场信息（FE 卡片全字段）"""
    try:
        market = favorites_repo.get_role_market_stats(role)
        insight = favorites_repo.get_role_insight(role)
        if not market and not insight:
            return jsonify({
                "success": True,
                "data": None,
                "message": f"暂无岗位 {role} 的市场数据"
            })

        payload = _enrich_role_market(role, market)
        return jsonify({"success": True, "data": payload})

    except Exception as e:
        return jsonify(safe_error("获取职业市场信息", e)), 500
