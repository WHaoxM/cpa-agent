"""
AI 分析 API — /api/ai/*
文案仅来自 LLM + Prompt 注册表；不可用则失败，不灌假数据。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from flask import request, jsonify

from . import ai_bp, agent_bp
from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from ..services.db import get_mysql_client
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from config.prompt_loader import load_prompt

logger = get_logger("cpa-agent.api.ai")


def _require_llm() -> LLMClient:
    from config.integration_loader import get_failover_order, resolve_provider_api_key

    if not any(resolve_provider_api_key(p) for p in get_failover_order()):
        raise ValueError("LLM_API_KEY not configured")
    from ..utils.llm_client import get_llm_client

    return get_llm_client()


def _parse_json_content(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def _public_llm_error(message: str = "AI 服务暂不可用，请稍后重试。"):
    return jsonify({"success": False, "error": message, "data": None})


def _verified_growth_sources(mysql, student_id: str, report_ids: list) -> tuple[dict | None, dict]:
    """Read current, owner-scoped domain projections; never trust browser snapshots.

    A saved row alone is not evidence that its source publication, task and
    profile version still agree.  Reuse the same reader exposed to the Agent
    instead of implementing a second report/portrait verification path here.
    """
    from ..services.agent_runtime.resource_catalog import read_platform_resource

    coverage = {"portrait": "not_available", "reports": 0, "reason": "authoritative_sources_unavailable"}
    try:
        portrait = read_platform_resource(
            mysql, student_id=student_id, uri="career://me/portrait", section="overview",
        )
    except (LookupError, ValueError, PermissionError):
        portrait = None
    if not isinstance(portrait, dict) or portrait.get("status") != "completed" or portrait.get("published") is not True:
        if isinstance(portrait, dict):
            coverage["portrait"] = str(portrait.get("status") or "not_available")
        return None, coverage
    coverage["portrait"] = "completed"
    portrait_data = portrait.get("data") if isinstance(portrait.get("data"), dict) else {}
    published_score = portrait_data.get("total_score")
    if (isinstance(published_score, bool) or not isinstance(published_score, (int, float))
            or not math.isfinite(published_score) or not 0 <= published_score <= 100):
        coverage["reason"] = "verified_portrait_score_missing"
        return None, coverage

    reports: list[dict] = []
    if report_ids:
        for report_id in report_ids:
            try:
                record = read_platform_resource(
                    mysql, student_id=student_id,
                    uri=f"career://me/reports/{report_id}", section="overview",
                )
            except (LookupError, ValueError, PermissionError):
                record = None
            if not isinstance(record, dict) or record.get("status") != "completed" or record.get("business_artifact_verified") is not True:
                coverage["reason"] = "requested_report_unavailable"
                return None, coverage
            reports.append(record)
    else:
        # Same bounded latest-ten window as the previous endpoint, but every
        # item is projected through the authoritative source/version gate.
        for offset in range(0, 10, 3):
            page = read_platform_resource(
                mysql, student_id=student_id, uri="career://me/reports",
                section="items", offset=offset, limit=min(3, 10 - offset),
            )
            if not isinstance(page, dict) or page.get("success") is not True:
                break
            for record in page.get("data") or []:
                if (isinstance(record, dict) and record.get("status") == "completed"
                        and record.get("business_artifact_verified") is True):
                    reports.append(record)
            if not isinstance(page.get("coverage"), dict) or not page["coverage"].get("has_more"):
                break

    coverage["reports"] = len(reports)
    if not reports:
        coverage["reason"] = "verified_report_missing"
        return None, coverage
    coverage["reason"] = "complete"
    source_refs = {
        "portrait": {"uri": portrait.get("uri"), "version": portrait.get("version"),
                     "source_resume_version": portrait.get("source_resume_version")},
        "reports": [{"uri": report.get("uri"), "version": report.get("version")}
                    for report in reports],
    }
    return {
        "portrait": portrait_data,
        "reports": [report.get("data") for report in reports],
        "source_refs": source_refs,
        "published_score": published_score,
    }, coverage


@agent_bp.route("/career-suggestions", methods=["POST"])
def career_suggestions():
    """POST /api/agent/career-suggestions — 返回 string[]。"""
    try:
        user_id = resolve_request_scope().student_id
        mysql = get_mysql_client()
        roles = mysql.execute(
            "SELECT role_name, priority FROM target_role WHERE user_id = :uid ORDER BY priority DESC",
            {"uid": user_id},
        )
        # Client targetRoles are UI hints, not persisted user choices.  This
        # endpoint must not turn arbitrary request JSON into personalized
        # recommendations or treat cold-start career_insights as live jobs.
        saved_roles = [
            {"role_name": row["role_name"], "priority": row.get("priority")}
            for row in roles or [] if isinstance(row, dict) and row.get("role_name")
        ]
        if not saved_roles:
            return jsonify({"success": False, "status": "needs_input", "data": None,
                            "error": "请先保存目标职业方向，再生成个人建议。",
                            "next_action": "save_target_role"}), 409

        from ..services.agent_runtime.evidence_retrieval import build_evidence_pack
        evidence = build_evidence_pack(
            mysql, student_id=user_id, question="根据已确认技能给已保存目标方向提出建议",
            resource_refs=[{"uri": "career://me/resume", "section": "skills"}],
            max_items=10, max_chars=4500,
        )
        if not evidence.get("items"):
            return jsonify({"success": False, "status": "partial", "data": None,
                            "error": "当前没有可读取的已确认简历技能依据，不能生成个性化建议。",
                            "source_coverage": evidence.get("coverage"),
                            "next_action": "confirm_resume_or_retry_evidence"}), 409
        context = {
            "targets": saved_roles,
            "resume_evidence": evidence,
            "market_source_status": "not_read; no current-job or salary claim authorized",
        }
        prompt = load_prompt(
            "agent.career_suggestions.user",
            context_json=json.dumps(context, ensure_ascii=False, default=str),
        )
        llm = _require_llm()
        raw = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        parsed = _parse_json_content(raw)
        suggestions = parsed if isinstance(parsed, list) else parsed.get("suggestions")
        if not isinstance(suggestions, list) or not suggestions:
            return jsonify({"success": False, "error": "LLM returned empty suggestions", "data": None}), 502
        suggestions = [str(s) for s in suggestions if s]
        return jsonify({"success": True, "data": suggestions,
                        "status": "partial" if evidence.get("has_more") else "completed",
                        "completion_scope": "draft_advice_only",
                        "source_coverage": evidence.get("coverage"),
                        "source_refs": evidence.get("sources")})
    except ScopeError as e:
        return scope_error_response(e)
    except ValueError as e:
        logger.info("career suggestions unavailable: %s", e)
        return _public_llm_error("AI 服务未配置或暂不可用。"), 503
    except Exception as e:
        err = str(e)
        if "Insufficient Balance" in err or "402" in err or "LLM_API_KEY" in err:
            logger.info("career suggestions provider unavailable: %s", e)
            return _public_llm_error(), 503
        return jsonify(safe_error("career suggestions", e)), 500


@ai_bp.route("/growth-analysis", methods=["POST"])
def growth_analysis():
    """POST /api/ai/growth-analysis — AIAnalysisResult。"""
    try:
        data = request.get_json() or {}
        user_id = resolve_request_scope().student_id
        report_ids = data.get("reportIds") or data.get("report_ids") or []
        if (not isinstance(report_ids, list) or len(report_ids) > 10
                or any(not isinstance(rid, str) or not rid or len(rid) > 128 for rid in report_ids)):
            return jsonify({"success": False, "error": "reportIds 必须是至多 10 个报告 ID", "data": None}), 400

        mysql = get_mysql_client()
        verified, coverage = _verified_growth_sources(mysql, user_id, report_ids)
        if verified is None:
            return jsonify({
                "success": False, "status": "partial", "data": None,
                "error": "当前缺少同版本、已发布的画像和可核验职业报告；不会使用旧分数或页面快照生成成长分析。",
                "reason": coverage["reason"], "source_coverage": coverage,
                "next_action": "complete_verified_portrait_and_report",
            }), 409
        targets = mysql.execute(
            "SELECT role_name, priority FROM target_role WHERE user_id = :uid",
            {"uid": user_id},
        )

        context = {
            "user_id": user_id,
            "reports": verified["reports"],
            "portrait": verified["portrait"],
            "target_roles": targets,
            "source_refs": verified["source_refs"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        prompt = load_prompt(
            "ai.growth_analysis.user",
            context_json=json.dumps(context, ensure_ascii=False, default=str),
        )
        llm = _require_llm()
        raw = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        parsed = _parse_json_content(raw)
        if not isinstance(parsed, dict):
            return jsonify({"success": False, "error": "LLM returned non-object", "data": None}), 502

        required = [
            "summary", "growthScore", "scoreTooltip", "topAbilities",
            "topAbilityDetails", "improvementAreas", "improvementDetails",
            "careerSuggestions", "timeline", "nextSteps",
        ]
        missing = [k for k in required if k not in parsed]
        if missing:
            return jsonify({
                "success": False,
                "error": f"LLM missing keys: {missing}",
                "data": None,
            }), 502
        if "generatedAt" not in parsed:
            parsed["generatedAt"] = context["generated_at"]
        # This UI field denotes the published portrait's competitiveness,
        # not a new score authored by a language model.  The model may write
        # prose, but it cannot replace the authoritative numeric result.
        parsed["growthScore"] = verified["published_score"]
        parsed["scoreTooltip"] = (
            "综合竞争力来自已发布四维画像（版本 "
            + str(verified["source_refs"]["portrait"]["version"])
            + "）；AI 只解释证据，不重新评分。"
        )
        return jsonify({"success": True, "data": parsed,
                        "source_refs": verified["source_refs"], "source_coverage": coverage})
    except ScopeError as e:
        return scope_error_response(e)
    except ValueError as e:
        logger.info("growth analysis unavailable: %s", e)
        return _public_llm_error("AI 服务未配置或暂不可用。"), 503
    except Exception as e:
        err = str(e)
        if "Insufficient Balance" in err or "402" in err or "LLM_API_KEY" in err:
            logger.info("growth analysis provider unavailable: %s", e)
            return _public_llm_error(), 503
        return jsonify(safe_error("growth analysis", e)), 500
