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


_RECOMMENDATION_SOURCE = "reviewed_job_catalog"
_RECOMMENDATION_PAGE_SIZE = 50
_RECOMMENDATION_SCAN_LIMIT = 500


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

        if not isinstance(answers, list) or not answers:
            return jsonify({"success": False, "error": "answers 不能为空"}), 400
        if not isinstance(target_role, str) or len(target_role) > 200:
            return jsonify({"success": False, "error": "target_role 必须是 200 字以内的文本"}), 400
        if any(
            not isinstance(answer, dict)
            or not isinstance(answer.get('skill'), str)
            or not answer['skill'].strip()
            or isinstance(answer.get('level'), bool)
            or not isinstance(answer.get('level'), int)
            or not 0 <= answer['level'] <= 5
            for answer in answers
        ):
            return jsonify({"success": False, "error": "answers 每项必须包含非空 skill 和 0 至 5 的整数 level"}), 400

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
        recommendation = _recommend_roles_by_skills(assessed_skills, target_role, mysql)
        recommended_roles = [item["position_name"] for item in recommendation["items"]]
        recommendation_record = {
            "source": _RECOMMENDATION_SOURCE,
            "status": recommendation["status"],
            "reason": recommendation["reason"],
            "items": recommendation["items"],
        }

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
            # A bare title cannot be revalidated after a revoke or new version.
            "recommended_roles": json.dumps(recommendation_record, ensure_ascii=False)
        })
        persisted = mysql.execute(
            "SELECT assessment_id FROM student_self_assessment "
            "WHERE assessment_id = :assessment_id AND student_id = :student_id LIMIT 1",
            {"assessment_id": assessment_id, "student_id": student_id},
        )
        if not persisted or str(persisted[0].get("assessment_id")) != assessment_id:
            return jsonify({"success": False, "error": "自评结果写入后未能核验归属"}), 500
        if recommendation["items"]:
            recommended_roles, validation_status, validation_reason = _revalidate_saved_recommendations(
                mysql, recommendation_record,
            )
            if validation_status not in {"ready", "partial"}:
                recommendation = {"items": [], "status": "unavailable",
                                  "reason": validation_reason or validation_status}
                recommended_roles = []

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
                "recommendation_status": recommendation["status"],
                "recommendation_reason": recommendation["reason"],
                "recommendation_source": _RECOMMENDATION_SOURCE if recommended_roles else None,
                "weak_skills_count": len(weak_skills),
                "next_steps": [
                    "查看已发布岗位目录" if recommended_roles else "补充自评或稍后重试岗位推荐",
                    "浏览学习路径",
                    "上传简历获取深度分析"
                ]
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("技能自评", e)), 500


def _recommend_roles_by_skills(assessed_skills, target_role, mysql):
    """Match only current, digest-verified human publications, never L2 portraits."""
    from ..services.job_catalog_listing import list_published_job_candidates
    from ..services.job_publication import read_published_job

    target = target_role.strip().casefold()
    skill_names = {
        skill["skill"].strip().casefold()
        for skill in assessed_skills if skill["level"] >= 3
    }
    if not target and not skill_names:
        return {"items": [], "status": "not_requested", "reason": "no_target_or_mastered_skill"}

    target_matches = []
    skill_matches = []
    offset = 0
    marker = None
    try:
        while offset < _RECOMMENDATION_SCAN_LIMIT:
            page = list_published_job_candidates(
                mysql, limit=_RECOMMENDATION_PAGE_SIZE, offset=offset,
            )
            if page["status"] == "unavailable":
                return {"items": [], "status": "unavailable", "reason": page["reason"]}
            if marker is not None and page["catalog_version"] != marker:
                return {"items": [], "status": "unavailable", "reason": "catalog_changed_during_read"}
            marker = page["catalog_version"]
            for candidate in page["items"]:
                publication = read_published_job(mysql, candidate["job_portrait_id"])
                if (publication is None
                        or publication["publication_version"] != candidate["publication_version"]
                        or publication["content_sha256"] != candidate["content_sha256"]):
                    return {"items": [], "status": "unavailable", "reason": "catalog_changed_during_read"}
                snapshot = publication["snapshot_json"]
                name = snapshot["position_name"]
                entry = {
                    "publication_source": _RECOMMENDATION_SOURCE,
                    "job_portrait_id": candidate["job_portrait_id"],
                    "publication_version": candidate["publication_version"],
                    "content_sha256": candidate["content_sha256"],
                    "position_name": name,
                }
                if target and target in name.casefold():
                    target_matches.append(entry)
                if skill_names.intersection(
                    skill["name"].strip().casefold()
                    for skill in snapshot["required_skills"]
                ):
                    skill_matches.append(entry)
            offset += len(page["items"])
            if not page["items"] and page["has_more"]:
                return {"items": [], "status": "unavailable", "reason": "catalog_pagination_inconsistent"}
            if not page["has_more"] or len(target_matches if target else skill_matches) >= 5:
                break
        else:
            # Never imply a complete search when the bounded scan stopped early.
            return {"items": (target_matches or skill_matches)[:5],
                    "status": "partial", "reason": "catalog_scan_limit"}
        selected = (target_matches or skill_matches)[:5]
        current = list_published_job_candidates(mysql, limit=1, offset=0)
        if current["status"] == "unavailable" or current["catalog_version"] != marker:
            return {"items": [], "status": "unavailable", "reason": "catalog_changed_during_read"}
        return {"items": selected,
                "status": "ready" if selected else "no_published_match",
                "reason": None if selected else "no_matching_reviewed_publication"}
    except Exception:
        logger.exception("已发布岗位自评推荐不可用")
        return {"items": [], "status": "unavailable", "reason": "catalog_integrity_unavailable"}


def _revalidate_saved_recommendations(mysql, raw):
    """Old bare titles and revoked/superseded references are never displayed."""
    from ..services.job_publication import read_published_job

    if isinstance(raw, dict) and raw.get("source") == _RECOMMENDATION_SOURCE:
        items = raw.get("items")
        original_status = raw.get("status")
        original_reason = raw.get("reason")
        if (not isinstance(items, list)
                or original_status not in {"ready", "no_published_match", "not_requested",
                                           "partial", "unavailable"}
                or original_reason is not None and not isinstance(original_reason, str)):
            return [], "legacy_unverified_removed", "invalid_saved_recommendation"
        if not items:
            return [], original_status, original_reason
    elif isinstance(raw, list):
        if not raw:
            return [], "not_recorded", None
        items = raw
        original_status = "ready"
        original_reason = None
    else:
        return [], "legacy_unverified_removed", "missing_publication_provenance"
    verified = []
    removed = False
    try:
        for item in items:
            if (not isinstance(item, dict)
                    or item.get("publication_source") != _RECOMMENDATION_SOURCE
                    or not all(isinstance(item.get(field), str) and item[field]
                               for field in ("job_portrait_id", "publication_version",
                                             "content_sha256", "position_name"))):
                removed = True
                continue
            current = read_published_job(mysql, item["job_portrait_id"])
            if (current is None
                    or current["publication_version"] != item["publication_version"]
                    or current["content_sha256"] != item["content_sha256"]
                    or current["snapshot_json"]["position_name"] != item["position_name"]):
                removed = True
                continue
            verified.append(current["snapshot_json"]["position_name"])
    except Exception:
        logger.exception("已保存岗位推荐回读失败")
        return [], "unavailable", "catalog_integrity_unavailable"
    if removed:
        return verified, "stale_or_unverified_removed", "publication_no_longer_current"
    return verified, original_status, original_reason


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
        stored_roles = parse_json_field(record.get("recommended_roles"), default=[])
        recommended_roles, recommendation_status, recommendation_reason = _revalidate_saved_recommendations(
            mysql, stored_roles,
        )
        return jsonify({
            "success": True,
            "data": {
                "assessment_id": record["assessment_id"],
                "student_id": record["student_id"],
                "assessed_skills": parse_json_field(record.get("assessed_skills"), default=[]),
                "weak_skills": parse_json_field(record.get("weak_skills"), default=[]),
                "recommended_roles": recommended_roles,
                "recommendation_status": recommendation_status,
                "recommendation_reason": recommendation_reason,
                "recommendation_source": _RECOMMENDATION_SOURCE if recommended_roles else None,
                "created_at": record["created_at"].isoformat() if record["created_at"] else None
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("获取自评结果", e)), 500
