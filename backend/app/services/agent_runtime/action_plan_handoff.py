"""Narrow report-follow-up handoff into a one-week, owner-bound action plan."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from .business_resources import RESUME_URI
from .reply_egress import sanitize_user_reply


_GAP_TERMS = re.compile(r"差距|缺口|不足|欠缺|待提升|需要补|还需要|建议补|尚缺")
_INTERNAL_REF = re.compile(r"career://[^\s，。；、]+|(?:report|resume|plan|step)_[a-f0-9]{8,}|[a-f0-9]{32,}", re.I)


def build_action_plan_offer(reply: str, evidence_pack: Optional[dict]) -> Optional[dict]:
    """Offer follow-through only when both authoritative source versions were read."""
    if not isinstance(evidence_pack, dict) or not _GAP_TERMS.search(str(reply or "")):
        return None
    sources = [s for s in evidence_pack.get("sources", []) if isinstance(s, dict)]
    report = next((s for s in sources if str(s.get("uri") or "").startswith("career://me/reports/")), None)
    resume = next((s for s in sources if s.get("uri") == RESUME_URI), None)
    coverage = evidence_pack.get("coverage") if isinstance(evidence_pack.get("coverage"), dict) else {}
    if (not report or not resume or report.get("status") not in {"completed", "partial"}
            or resume.get("status") != "completed" or not report.get("version")
            or not resume.get("version") or not coverage.get("complete")
            or evidence_pack.get("status") == "unavailable"):
        return None
    return {
        "handoff_id": f"aph_{uuid.uuid4().hex}",
        "phase": "offered",
        "report_uri": str(report["uri"]),
        "report_version": str(report["version"]),
        "resume_version": str(resume["version"]),
        "gap_summary": sanitize_user_reply(str(reply or ""), max_len=1200),
    }


def verify_action_plan_sources(mysql, *, student_id: str, context: dict) -> tuple[bool, str]:
    """Re-read report and current resume; never mix a report with another version."""
    report_uri = str(context.get("report_uri") or "")
    report_version = str(context.get("report_version") or "")
    resume_version = str(context.get("resume_version") or "")
    if (not student_id or not report_uri.startswith("career://me/reports/")
            or not report_version or not resume_version):
        return False, "invalid"
    try:
        from .resource_catalog import read_platform_resource

        report = read_platform_resource(
            mysql, student_id=student_id, uri=report_uri, section="overview",
        )
        resume = read_platform_resource(
            mysql, student_id=student_id, uri=RESUME_URI, section="overview",
        )
    except Exception:
        return False, "unavailable"
    if (not isinstance(report, dict) or report.get("success") is not True
            or report.get("business_artifact_verified") is not True
            or report.get("status") not in {"completed", "partial"}
            or str(report.get("version") or "") != report_version):
        return False, "expired"
    if (not isinstance(resume, dict) or resume.get("success") is not True
            or resume.get("status") != "completed"
            or str(resume.get("version") or "") != resume_version):
        return False, "expired"
    data = report.get("data") if isinstance(report.get("data"), dict) else {}
    source = data.get("sources") if isinstance(data.get("sources"), dict) else {}
    if str(source.get("source_confirmation_task_id") or "") != resume_version:
        return False, "expired"
    return True, "current"


def build_current_action_evidence(mysql, *, student_id: str, context: dict) -> Optional[dict]:
    """Load fresh, bounded evidence for plan generation after checking source binding."""
    valid, state = verify_action_plan_sources(mysql, student_id=student_id, context=context)
    if not valid:
        return None
    try:
        from .evidence_retrieval import build_evidence_pack

        pack = build_evidence_pack(
            mysql,
            student_id=student_id,
            question="请按报告指出的差距，并结合简历制定可在一周内完成的行动步骤。",
            resource_refs=[{
                "uri": context["report_uri"],
                "version": context["report_version"],
            }],
            max_items=10,
            max_chars=4500,
        )
    except Exception:
        return None
    sources = [s for s in pack.get("sources", []) if isinstance(s, dict)]
    report = next((s for s in sources if s.get("uri") == context["report_uri"]), None)
    resume = next((s for s in sources if s.get("uri") == RESUME_URI), None)
    if (state != "current" or not report or not resume
            or report.get("status") not in {"completed", "partial"} or resume.get("status") != "completed"
            or str(report.get("version") or "") != str(context["report_version"])
            or str(resume.get("version") or "") != str(context["resume_version"])
            or pack.get("status") == "unavailable"
            or not (pack.get("coverage") or {}).get("complete")):
        return None
    return pack


def generate_weekly_steps(*, gap_summary: str, weekly_hours: str, evidence_pack: dict) -> list[dict[str, str]]:
    """Ask the configured planner model for a small JSON list; persistence validates shape."""
    from ...utils.llm_client import get_llm_client

    evidence = json.dumps(evidence_pack.get("items") or [], ensure_ascii=False, separators=(",", ":"))[:9000]
    messages = [
        {
            "role": "system",
            "content": (
                "你为学生把已核验的岗位报告差距转成一周可执行计划。"
                "只可依据给定报告与简历证据，不能补造学生经历、课程、链接或外部事实。"
                "按学生时间投入安排3到5步；每步要有清晰动作和可检查产出。"
                "只输出JSON对象，格式：{\"steps\":[{\"title\":\"...\",\"detail\":\"...\"}]}。"
                "标题和详情不得包含内部URI、记录ID、版本或哈希。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "gap_summary": sanitize_user_reply(gap_summary, max_len=1200),
                "weekly_time_commitment": sanitize_user_reply(weekly_hours, max_len=120),
                "verified_evidence": evidence,
            }, ensure_ascii=False),
        },
    ]
    raw = get_llm_client().chat(messages=messages, temperature=0.2, max_tokens=1200) or ""
    text = str(raw).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise ValueError("计划未能形成可核对的步骤，请稍后重试") from exc
    steps = parsed.get("steps") if isinstance(parsed, dict) else None
    if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
        raise ValueError("计划未能形成 3 至 5 个具体步骤，请稍后重试")
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        title = _public_step_text(step.get("title"), 160)
        detail = _public_step_text(step.get("detail"), 600)
        if title and detail:
            out.append({"title": title, "detail": detail})
    if not 3 <= len(out) <= 5:
        raise ValueError("计划步骤不完整，请稍后重试")
    return out


def _public_step_text(value: Any, limit: int) -> str:
    text = sanitize_user_reply(str(value or ""), max_len=limit).strip()
    text = _INTERNAL_REF.sub("", text)
    return text.strip(" ：:，,；;。")[:limit]
