"""
Experts Lead — DeepSeek/Kimi-aligned intent packet (not keyword routing).

Pattern borrowed:
- DeepSeek: restate goal → then decide tools/plan (think before act)
- Kimi: orchestrator compiles a self-contained brief; specialists don't see chat noise
- Research (IN3 / Ask-when-Needed): clarify missing *execution slots*, not open chitchat

Lead is ONE structured call. Deterministic slot gates can override run_plan → clarify.
Only structured model output selects plans; resource slots come from mounted state.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from ...utils.logger import get_logger
from .blackboard import Blackboard
from .budget import truncate_slot
from .plan import PLAN_TEMPLATES

logger = get_logger("cpa-agent.agent_runtime.experts_lead")

# Execution slots Lead may ask about (SAGE-style: tool/plan params, not freeform)
SLOT_IDS = frozenset({"resume", "target_role", "portrait", "match"})

SLOT_LABELS: Dict[str, str] = {
    "resume": "简历或学习/工作背景",
    "target_role": "目标岗位或方向",
    "portrait": "已有职业画像",
    "match": "岗位匹配结果",
}

# Soft prerequisites per template (first step can still create portrait)
TEMPLATE_SOFT_SLOTS: Dict[str, List[str]] = {
    "end_to_end": ["resume"],
    "profile_match": ["resume"],
    "match_report": ["portrait"],
    "match_planner": ["portrait"],
}

_EXPLAIN_DEFAULT = (
    "Experts 模式用于多专家协作完成端到端任务（例如：画像 → 匹配 → 报告）。\n\n"
    "它适合你已经有明确目标、需要多步执行的时候；"
    "若只是了解能力或闲聊概念，更适合 Ask。\n\n"
    "若要现在执行，请直接说明目标，例如「根据我的背景生成职业报告」。"
)

_USER_JOBS = frozenset({"meta", "inform", "execute", "revise"})
_VAGUENESS = frozenset({"clear", "partial", "vague"})


def experts_lead_decide(
    message: str,
    *,
    card: str = "",
    summary: str = "",
    memory_digest: str = "",
    plan_mode: bool = False,
    blackboard: Optional[Blackboard] = None,
) -> Dict[str, Any]:
    """
    Returns LeadIntentPacket:
      action, template, reply, confidence, source,
      user_job, goal_summary, vagueness,
      known_slots, missing_slots, clarify_options,
      plan_sketch, handoff_brief, risk_if_run
    """
    text = (message or "").strip()
    bb = blackboard or Blackboard()
    known = _infer_known_slots(text, bb)
    hint = None  # Never bias structured planning with keyword-derived hints.
    decision = _llm_lead(
        text,
        card=card,
        summary=summary,
        memory_digest=memory_digest,
        template_hint=hint,
        known_slots=known,
    )
    if decision:
        return _normalize(
            decision,
            text,
            plan_mode=plan_mode,
            hint=hint,
            known_slots=known,
            blackboard=bb,
        )

    # Failed structured planning is unavailable, not evidence of a meta intent.
    return _finalize({
        "action": "clarify", "template": None, "user_job": "inform",
        "goal_summary": "", "vagueness": "partial", "known_slots": known,
        "missing_slots": [], "plan_sketch": [], "clarify_options": [],
        "reply": "本轮任务规划服务暂不可用，尚未启动任何专家步骤，请重试。",
        "confidence": 0.0, "source": "lead_unavailable",
        "risk_if_run": "没有有效的结构化执行计划",
    })


def _llm_lead(
    message: str,
    *,
    card: str,
    summary: str,
    memory_digest: str,
    template_hint: Optional[str],
    known_slots: Sequence[str],
) -> Optional[Dict[str, Any]]:
    try:
        from ...utils.llm_client import get_llm_client
        from .modes import InteractionMode
        from .prompt_selector import PromptSelectionKey, get_prompt_selector
        from .model_router import route_model

        route = route_model("lead_classify")
        templates = ", ".join(sorted(PLAN_TEMPLATES.keys()))
        slots = ", ".join(sorted(SLOT_IDS))
        prompt = get_prompt_selector().select(
            PromptSelectionKey(
                engine="supervisor",
                interaction_mode=InteractionMode.EXPERTS,
                content_kind="lead",
            ),
            variables={
                "templates": templates,
                "slots": slots,
                "known_slots_json": json.dumps(list(known_slots) or [], ensure_ascii=False),
                "template_hint": template_hint or "none",
                "card": truncate_slot(card or "", 500),
                "summary": truncate_slot(summary or "", 400),
                "memory_digest": truncate_slot(memory_digest or "", 800),
                "message": message,
            },
        )
        raw = get_llm_client().chat(
            messages=[{"role": "user", "content": prompt.system}],
            temperature=route["temperature"],
            max_tokens=route["max_tokens"],
            model_override=route["model"],
            response_format={"type": "json_object"},
        )
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.info("experts_lead llm skip: %s", e)
        return None


def _normalize(
    data: Dict[str, Any],
    message: str,
    *,
    plan_mode: bool,
    hint: Optional[str],
    known_slots: Sequence[str],
    blackboard: Blackboard,
) -> Dict[str, Any]:
    del blackboard  # reserved for richer gates
    hint = None  # Compatibility parameter cannot revive keyword routing.
    user_job = str(data.get("user_job") or "").strip().lower()
    if user_job not in _USER_JOBS:
        user_job = "inform"

    action = str(data.get("action") or "clarify").strip().lower()
    if action not in ("explain", "clarify", "run_plan"):
        action = "clarify"

    # Meta always explains — deterministic override
    if user_job == "meta":
        action = "explain"

    template = data.get("template")
    if template is not None:
        template = str(template).strip()
        if template not in PLAN_TEMPLATES:
            template = None

    goal_summary = str(data.get("goal_summary") or "").strip()
    vagueness = str(data.get("vagueness") or "partial").strip().lower()
    if vagueness not in _VAGUENESS:
        vagueness = "partial"

    missing = _clean_slots(data.get("missing_slots"))
    # Merge deterministic soft gaps
    if action == "run_plan" or template:
        tid = template or hint
        if tid:
            for s in _missing_for_template(tid, known_slots):
                if s not in missing:
                    missing.append(s)

    options = _clean_str_list(data.get("clarify_options"), 4)
    sketch = _clean_str_list(data.get("plan_sketch"), 4)
    handoff = str(data.get("handoff_brief") or "").strip()
    reply = str(data.get("reply") or "").strip()
    risk = str(data.get("risk_if_run") or "").strip()
    try:
        confidence = float(data.get("confidence") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5

    if action == "run_plan":
        # Missing model template cannot be rescued by text matching.
        if not template:
            action = "clarify"
            reply = reply or (
                "还需要更明确的目标，我才能启动多专家计划。"
                "例如：从简历生成职业报告。"
            )
        elif vagueness == "vague":
            action = "clarify"
        elif missing:
            # Soft slots still block silent full-pipeline when resume missing on e2e
            if "resume" in missing and template in ("end_to_end", "profile_match"):
                action = "clarify"

    if action == "run_plan":
        if not sketch:
            sketch = _sketch_for(str(template))
        if not handoff:
            handoff = goal_summary or message
        if not goal_summary:
            goal_summary = f"执行多专家计划：{template}"
        reply = ""
        missing = []
    elif action == "explain":
        if not goal_summary:
            goal_summary = "用户在了解 Experts 模式用途或边界。"
        if not reply:
            reply = _EXPLAIN_DEFAULT
        if not options:
            options = [
                "根据我的背景生成职业报告",
                "先做画像再匹配岗位",
            ]
        template = None
        handoff = ""
    else:
        action = "clarify"
        if not goal_summary:
            goal_summary = "用户可能有任务意图，但执行条件未齐。"
        if not options:
            options = _options_for(template or hint, missing)
        if not sketch and (template or hint):
            sketch = _sketch_for(str(template or hint))
        reply = reply or _clarify_reply(goal_summary, missing, options)
        handoff = ""

    return _finalize(
        {
            "action": action,
            "template": template if action == "run_plan" else None,
            "user_job": user_job,
            "goal_summary": goal_summary,
            "vagueness": vagueness,
            "known_slots": list(known_slots),
            "missing_slots": missing if action == "clarify" else [],
            "clarify_options": options,
            "plan_sketch": sketch,
            "handoff_brief": handoff if action == "run_plan" else "",
            "reply": reply,
            "confidence": confidence,
            "risk_if_run": risk,
            "source": "llm",
        }
    )


def _finalize(packet: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": packet.get("action") or "clarify",
        "template": packet.get("template"),
        "user_job": packet.get("user_job") or "inform",
        "goal_summary": str(packet.get("goal_summary") or ""),
        "vagueness": packet.get("vagueness") or "partial",
        "known_slots": list(packet.get("known_slots") or []),
        "missing_slots": list(packet.get("missing_slots") or []),
        "clarify_options": list(packet.get("clarify_options") or []),
        "plan_sketch": list(packet.get("plan_sketch") or []),
        "handoff_brief": str(packet.get("handoff_brief") or ""),
        "reply": str(packet.get("reply") or ""),
        "confidence": float(packet.get("confidence", 0.5)),
        "risk_if_run": str(packet.get("risk_if_run") or ""),
        "source": str(packet.get("source") or "fallback"),
    }


def _infer_known_slots(message: str, bb: Blackboard) -> List[str]:
    known: List[str] = []
    attachments = bb.get("attachments.mounted") or {}
    profile = bb.get("student.profile_ref") or {}
    files = attachments.get("files") if isinstance(attachments, dict) else []
    if (any(isinstance(f, dict) and f.get("file_id") for f in (files or []))
            or (isinstance(profile, dict) and profile.get("verified") is True
                and profile.get("source_task_id"))):
        known.append("resume")
    for slot, key in (("portrait", "portrait.current"), ("match", "match.current")):
        ref = bb.get(key)
        if isinstance(ref, dict) and ref.get("verified") is True and ref.get("source_task_id"):
            known.append(slot)
    return known


def _missing_for_template(template: str, known: Sequence[str]) -> List[str]:
    need = TEMPLATE_SOFT_SLOTS.get(template) or []
    kn = set(known)
    return [s for s in need if s not in kn]


def _clean_slots(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip().lower()
        if s in SLOT_IDS and s not in out:
            out.append(s)
    return out


def _clean_str_list(raw: Any, limit: int) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _sketch_for(template: str) -> List[str]:
    steps = PLAN_TEMPLATES.get(template) or []
    labels = {
        "profile": "生成职业画像",
        "match": "岗位技能匹配",
        "report": "生成职业报告",
        "planner": "学习路径规划",
    }
    return [f"{i + 1}. {labels.get(s, s)}" for i, s in enumerate(steps)]


def _options_for(template: Optional[str], missing: Sequence[str]) -> List[str]:
    opts: List[str] = []
    if "resume" in missing:
        opts.append("我已有简历/背景，请开始生成职业报告")
        opts.append("我先补充一下专业和实习经历")
    if "portrait" in missing:
        opts.append("先帮我生成画像，再继续匹配")
    if not opts:
        opts = [
            "根据我的背景生成职业报告",
            "先做画像再匹配岗位",
        ]
        if template:
            opts.insert(0, f"确认，按「{template}」执行")
    return opts[:4]


def _clarify_reply(
    goal_summary: str,
    missing: Sequence[str],
    options: Sequence[str],
) -> str:
    parts = [goal_summary.strip() or "我需要再确认一下目标。"]
    if missing:
        labels = [SLOT_LABELS.get(s, s) for s in missing]
        parts.append("目前还缺：" + "、".join(labels) + "。")
    if options:
        parts.append("你可以这样回复：")
        for i, opt in enumerate(options[:3], 1):
            parts.append(f"{i}. {opt}")
    return "\n".join(parts)
