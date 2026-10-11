"""Domain adapters: existing scorer -> persisted match -> evidence-only report.

Never re-extract a resume or re-score while rendering a report. Missing planning
and recommendation evidence stays explicitly partial instead of invented text.
"""
import hashlib
import json
import math
import uuid
from dataclasses import asdict
from pathlib import Path

from .job_publication import canonical_json, content_sha256


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def score_policy():
    from config.loader import get_rules_config
    from app.pipeline.entity_extractor import matcher
    return {"rules": asdict(get_rules_config()),
            "implementation_sha256": hashlib.sha256(Path(matcher.__file__).read_bytes()).hexdigest()}


def persist_match(pipeline, profile, sources):
    from app.pipeline.entity_extractor.matcher import calculate_match_score
    from .pipeline import PipelineSourceUnavailable
    sid = pipeline.params["student_id"]
    publication = sources["publication"]
    jid = publication["job_portrait_id"]
    job = publication["snapshot_json"]
    skills = [{"name": s} if isinstance(s, str) else s for s in profile.get("skills", [])]
    if not skills:
        raise PipelineSourceUnavailable("confirmed_skills_unavailable", "confirm_resume",
                                        "已确认资料缺少技能，未计算匹配", sources["references"])
    policy = score_policy()
    # Confirmed resume schema stores a flat skill list; the legacy scorer's D3
    # adapter consumes a typed skill object. Preserve the original snapshot.
    scoring_profile = {**profile, "skills": {"hard_skills": skills, "soft_skills": profile.get("soft_skills", [])}}
    result = calculate_match_score(skills, job["required_skills"], scoring_profile, job["job_profile"])
    # The scorer now represents absent confirmed D3 evidence as unknown, not
    # as a default score. Legacy SQL score columns are NOT NULL DEFAULT 0, so
    # a partial result must remain only a pipeline task, never a match row.
    score_fields = ("total_score", "d1_score", "d2_score", "d3_score", "d4_score")
    if result.get("status") == "partial" or not all(
        isinstance(result.get(key), (int, float))
        and not isinstance(result.get(key), bool)
        and math.isfinite(result[key])
        for key in score_fields
    ):
        return {
            "status": "partial", "verified": False,
            "student_id": sid, "job_portrait_id": jid, "match_id": None,
            "resource_type": "pipeline_task", "resource_id": pipeline.task_id,
            "reason": "scoring_evidence_incomplete", "next_action": "confirm_semantic_facts",
            "missing_fields": result.get("missing_fields") or ["confirmed_quality_evidence"],
            "sources": sources["references"],
            "summary": "匹配所需的已确认事实不足，未写入分数或匹配成果",
        }
    # The scorer's own D1/R-08 decision is authoritative. Do not reclassify with
    # a second hardcoded threshold or with model-provided scores.
    result.update({"score_kind": "policy_estimate", "quality_status": "not_validated",
                   "sources": sources["references"], "source_match_task_id": pipeline.task_id,
                   "scoring_policy": policy, "scoring_policy_sha256": content_sha256(policy),
                   "profile_snapshot": profile, "portrait_snapshot": sources["portrait"],
                   "job_snapshot": job, "student_id": sid, "job_portrait_id": jid})
    payload = canonical_json(result)
    pipeline.mysql.execute(
        "INSERT INTO match_result (student_id, job_portrait_id, total_score, d1_match, d2_match, d3_match, d4_match, "
        "d1_pass, skill_coverage, is_matched, match_level, result_json, created_at) "
        "VALUES (:sid,:jid,:total,:d1,:d2,:d3,:d4,:d1_pass,:coverage,:matched,:level,:payload,NOW()) "
        "ON DUPLICATE KEY UPDATE total_score=:total,d1_match=:d1,d2_match=:d2,d3_match=:d3,d4_match=:d4,"
        "d1_pass=:d1_pass,skill_coverage=:coverage,is_matched=:matched,match_level=:level,result_json=:payload",
        {"sid": sid, "jid": jid, "total": result["total_score"], "d1": result["d1_score"],
         "d2": result["d2_score"], "d3": result["d3_score"], "d4": result["d4_score"],
         "d1_pass": bool(result["d1_pass"]), "coverage": result["skill_coverage"],
         "matched": bool(result["match_result"]), "level": result["match_level"], "payload": payload}, commit=True)
    rows = pipeline.mysql.fetch_all(
        "SELECT id, student_id, job_portrait_id, result_json FROM match_result "
        "WHERE student_id=:sid AND job_portrait_id=:jid LIMIT 1", {"sid": sid, "jid": jid})
    if (not rows or rows[0]["student_id"] != sid or rows[0]["job_portrait_id"] != jid
            or _json(rows[0]["result_json"]) != result):
        raise RuntimeError("匹配写入未通过 owner/内容回读验证")
    return {"status": "partial", "verified": True, "student_id": sid, "job_portrait_id": jid,
            "match_id": str(rows[0]["id"]), "match_sha256": content_sha256(result),
            "total_score": result["total_score"], "is_matched": result["match_result"],
            "resource_type": "match_result", "resource_id": str(rows[0]["id"]),
            "score_kind": "policy_estimate", "reason": "scoring_policy_not_validated",
            "next_action": "validate_scoring_policy", "sources": sources["references"],
            "summary": "规则估计已持久化并回读；D3 等旧推断策略尚未通过质量验收，分数不是录用概率"}


def persist_report(pipeline, sources):
    from .pipeline import PipelineSourceUnavailable
    sid = pipeline.params["student_id"]
    jid = sources["publication"]["job_portrait_id"]
    rows = pipeline.mysql.fetch_all(
        "SELECT id, student_id, job_portrait_id, result_json FROM match_result "
        "WHERE student_id=:sid AND job_portrait_id=:jid LIMIT 1", {"sid": sid, "jid": jid})
    row = rows[0] if rows else {}
    match = _json(row.get("result_json") or {})
    tasks = pipeline.mysql.fetch_all(
        "SELECT student_id, pipeline, status, result_json FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
        {"task_id": match.get("source_match_task_id")}) if isinstance(match, dict) and match.get("source_match_task_id") else []
    task = tasks[0] if tasks else {}
    outcome = _json(task.get("result_json") or {})
    valid = (row.get("id") and row.get("student_id") == sid and row.get("job_portrait_id") == jid
             and isinstance(match, dict) and match.get("sources") == sources["references"]
             and task.get("student_id") == sid and task.get("pipeline") == "L3_run_match"
             and task.get("status") in {"completed", "partial"} and outcome.get("verified") is True
             and str(outcome.get("match_id")) == str(row.get("id"))
             and outcome.get("match_sha256") == content_sha256(match))
    if not valid:
        raise PipelineSourceUnavailable("verified_match_unavailable", "run_match",
                                        "匹配缺失、版本已变化或回读不一致，请重新匹配", sources["references"])
    rid = "report_" + uuid.uuid4().hex
    report = {"schema_version": "evidence-snapshot-v1", "report_id": rid, "student_id": sid,
              "status": "partial", "sources": {**sources["references"], "match_id": str(row["id"]),
              "match_sha256": content_sha256(match)}, "match_snapshot": match,
              "missing_sections": ["validated_scoring_policy", "evidence_based_action_plan", "verified_resource_recommendations"],
              "summary": "画像、岗位与匹配证据已固化；行动计划和资源推荐尚无来源，不填充模板建议"}
    pipeline.mysql.execute(
        "INSERT INTO career_report (report_id,student_id,job_portrait_id,title,status,report_json,created_at,updated_at) "
        "VALUES (:rid,:sid,:jid,:title,'partial',:payload,NOW(),NOW())",
        {"rid": rid, "sid": sid, "jid": jid, "title": "职业规划证据报告", "payload": canonical_json(report)}, commit=True)
    saved = pipeline.mysql.fetch_all(
        "SELECT report_id, student_id, status, report_json FROM career_report "
        "WHERE report_id=:rid AND student_id=:sid LIMIT 1", {"rid": rid, "sid": sid})
    if (not saved or saved[0].get("report_id") != rid or saved[0].get("student_id") != sid
            or saved[0].get("status") != "partial" or _json(saved[0].get("report_json")) != report):
        raise RuntimeError("报告写入未通过 owner/内容回读验证")
    return {"status": "partial", "verified": True, "report_id": rid, "student_id": sid,
            "job_portrait_id": jid, "resource_type": "career_report", "resource_id": rid,
            "reason": "recommendation_evidence_unavailable", "next_action": "provide_recommendation_sources",
            "sources": report["sources"], "summary": report["summary"]}
