"""
Pipeline_ext Assessment 子模块

技能自评 API (2 路由 + 1 私有 helper):
- POST /exams                  提交技能自评结果，生成画像和推荐
- GET  /exams/<assessment_id>  获取自评结果详情

私有 helper:
- _recommend_roles_by_skills   基于自评技能推荐岗位

依赖: ..services.db (MySQL)
"""

import json
import uuid

from flask import request, jsonify

from ._helpers import (
    ScopeError, parse_json_field, resolve_request_scope, safe_error,
    scope_error_response,
)
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/exams', methods=['POST'])
def submit_self_assessment():
    """
    提交技能自评结果；只保存自评，不生成权威画像

    Body:
        {
            "student_id": "可选，无则新建",
            "answers": [
                {"question_id": "q1", "skill": "Python", "level": 3},
                {"question_id": "q2", "skill": "JavaScript", "level": 2}
            ],
            "target_role": "前端开发"
        }

    流程:
        1. 保存自评答案 → student_self_assessment 表
        2. 分析弱技能 → weak_skills
        3. 推荐岗位 → recommended_roles
        4. 返回待画像生成状态；此接口不写 student_portrait_4d
    """
    try:
        data = request.get_json() or {}
        answers = data.get('answers', [])
        target_role = data.get('target_role', '')

        if not answers:
            return jsonify({"success": False, "error": "answers 不能为空"}), 400

        student_id = resolve_request_scope().student_id
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        # 分析自评结果
        assessed_skills = []
        weak_skills = []

        for ans in answers:
            skill = ans.get('skill', '')
            level = ans.get('level', 0)  # 0-5
            assessed_skills.append({
                "skill": skill,
                "level": level,
                "confidence": min(level / 5.0, 1.0)
            })
            if level < 3:  # 3分以下为弱技能
                weak_skills.append(skill)

        # 基于弱技能推荐学习方向
        recommended_roles = _recommend_roles_by_skills(assessed_skills, weak_skills, target_role, mysql)

        # 保存自评结果
        assessment_id = f"exam_{uuid.uuid4().hex[:12]}"
        sql = """
        INSERT INTO student_self_assessment
        (assessment_id, student_id, assessed_skills, weak_skills, recommended_roles, created_at)
        VALUES (:assessment_id, :student_id, :assessed_skills, :weak_skills, :recommended_roles, NOW())
        """
        mysql.execute_write(sql, {
            "assessment_id": assessment_id,
            "student_id": student_id,
            "assessed_skills": json.dumps(assessed_skills),
            "weak_skills": json.dumps(weak_skills),
            "recommended_roles": json.dumps(recommended_roles)
        })
        persisted = mysql.execute(
            "SELECT assessment_id FROM student_self_assessment "
            "WHERE assessment_id = :assessment_id AND student_id = :student_id LIMIT 1",
            {"assessment_id": assessment_id, "student_id": student_id},
        )
        if not persisted or str(persisted[0].get("assessment_id")) != assessment_id:
            return jsonify({"success": False, "error": "自评结果写入后未能核验归属"}), 500

        return jsonify({
            "success": True,
            "data": {
                "assessment_id": assessment_id,
                "student_id": student_id,
                "status": "partial",
                "portrait": None,
                "portrait_ready": False,
                "portrait_verified": False,
                "recommended_roles": recommended_roles,
                "weak_skills_count": len(weak_skills),
                "next_steps": [
                    "查看推荐岗位",
                    "浏览学习路径",
                    "上传简历获取深度分析"
                ]
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("技能自评", e)), 500


def _recommend_roles_by_skills(assessed_skills, weak_skills, target_role, mysql):
    """基于自评技能推荐岗位"""
    try:
        # 如果指定了目标方向，优先返回相关岗位
        if target_role:
            sql = """
            SELECT position_name FROM job_portrait_4d
            WHERE position_name LIKE :role OR portrait_json LIKE :role_pattern
            LIMIT 5
            """
            results = mysql.execute(sql, {
                "role": f"%{target_role}%",
                "role_pattern": f'%"{target_role}"%'
            })
            if results:
                return [r["position_name"] for r in results]

        # 否则基于技能匹配推荐
        skill_names = [s["skill"] for s in assessed_skills if s["level"] >= 3]
        if not skill_names:
            return []

        # 查询包含这些技能的岗位
        conditions = " OR ".join([f"portrait_json LIKE :skill{i}" for i in range(len(skill_names))])
        sql = f"""
        SELECT position_name, portrait_json
        FROM job_portrait_4d
        WHERE {conditions}
        LIMIT 5
        """
        params = {f"skill{i}": f'%"{s}"%' for i, s in enumerate(skill_names)}
        results = mysql.execute(sql, params)

        if results:
            return [r["position_name"] for r in results]
        return []

    except Exception as e:
        logger.warning(f"推荐岗位失败: {e}")
        return []


@pipeline_ext_bp.route('/exams/<assessment_id>', methods=['GET'])
def get_assessment_result(assessment_id: str):
    """获取自评结果详情"""
    try:
        scope = resolve_request_scope()
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()
        sql = ("SELECT * FROM student_self_assessment "
               "WHERE assessment_id = :assessment_id AND student_id = :student_id")
        results = mysql.execute(sql, {"assessment_id": assessment_id, "student_id": scope.student_id})

        if not results:
            return jsonify({"success": False, "error": "自评记录不存在"}), 404

        record = results[0]
        return jsonify({
            "success": True,
            "data": {
                "assessment_id": record["assessment_id"],
                "student_id": record["student_id"],
                "assessed_skills": parse_json_field(record.get("assessed_skills"), default=[]),
                "weak_skills": parse_json_field(record.get("weak_skills"), default=[]),
                "recommended_roles": parse_json_field(record.get("recommended_roles"), default=[]),
                "created_at": record["created_at"].isoformat() if record["created_at"] else None
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("获取自评结果", e)), 500
