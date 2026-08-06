"""
AI 分析 API — /api/ai/*
文案仅来自 LLM + Prompt 注册表；不可用则失败，不灌假数据。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import request, jsonify

from . import ai_bp, agent_bp
from ._helpers import safe_error
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


@agent_bp.route("/career-suggestions", methods=["POST"])
def career_suggestions():
    """POST /api/agent/career-suggestions — 返回 string[]。"""
    try:
        data = request.get_json() or {}
        user_id = data.get("userId") or data.get("user_id") or "student_001"
        target_roles = data.get("targetRoles") or data.get("target_roles") or []

        mysql = get_mysql_client()
        roles = mysql.execute(
            "SELECT * FROM target_role WHERE user_id = :uid ORDER BY priority DESC",
            {"uid": user_id},
        )
        enriched = []
        for r in roles or target_roles:
            role_name = r.get("role_name") or r.get("role") if isinstance(r, dict) else str(r)
            insight = mysql.execute(
                """
                SELECT role_name, demand_level, core_skills, description, avg_salary_low, avg_salary_high
                FROM career_insights
                WHERE role_name = :role OR role_name LIKE :like
                LIMIT 1
                """,
                {"role": role_name, "like": f"%{role_name}%"},
            )
            enriched.append({
                "role": role_name,
                "insight": insight[0] if insight else None,
                "target": r if isinstance(r, dict) else {"role": role_name},
            })

        context = {"user_id": user_id, "targets": enriched}
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
        return jsonify({"success": True, "data": suggestions})
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
        user_id = data.get("userId") or data.get("user_id") or data.get("student_id") or "student_001"
        report_ids = data.get("reportIds") or data.get("report_ids") or []
        snapshots = data.get("snapshots") or []

        mysql = get_mysql_client()
        reports = []
        if report_ids:
            for rid in report_ids:
                rows = mysql.execute(
                    "SELECT * FROM career_report WHERE report_id = :id LIMIT 1",
                    {"id": rid},
                )
                if rows:
                    reports.append(rows[0])
        else:
            reports = mysql.execute(
                """
                SELECT * FROM career_report
                WHERE student_id = :sid
                ORDER BY created_at DESC
                LIMIT 10
                """,
                {"sid": user_id},
            )

        portrait = mysql.execute(
            """
            SELECT student_id, d1_score, d2_score, d3_score, d4_score,
                   competitiveness, completeness, skill_coverage
            FROM student_portrait_4d
            WHERE student_id = :sid
            ORDER BY created_at DESC LIMIT 1
            """,
            {"sid": user_id},
        )
        targets = mysql.execute(
            "SELECT role_name, priority FROM target_role WHERE user_id = :uid",
            {"uid": user_id},
        )

        context = {
            "user_id": user_id,
            "reports": reports,
            "snapshots": snapshots,
            "portrait": portrait[0] if portrait else None,
            "target_roles": targets,
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
        return jsonify({"success": True, "data": parsed})
    except ValueError as e:
        logger.info("growth analysis unavailable: %s", e)
        return _public_llm_error("AI 服务未配置或暂不可用。"), 503
    except Exception as e:
        err = str(e)
        if "Insufficient Balance" in err or "402" in err or "LLM_API_KEY" in err:
            logger.info("growth analysis provider unavailable: %s", e)
            return _public_llm_error(), 503
        return jsonify(safe_error("growth analysis", e)), 500
