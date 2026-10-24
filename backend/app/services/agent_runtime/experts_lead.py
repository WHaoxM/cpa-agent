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
from .plan import PLAN_TEMPLATES, validate_plan_steps
from .skill_registry import skill_cards
from .task_packet import SUBAGENTS

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
    interaction_mode: str = "experts",
    selected_capability: Optional[Dict[str, Any]] = None,
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
        interaction_mode=interaction_mode,
        selected_capability=selected_capability,
    )
    if decision:
        return _normalize(
            decision,
            text,
            plan_mode=plan_mode,
            hint=hint,
            known_slots=known,
            blackboard=bb,
            selected_capability=selected_capability,
        )

    # Failed structured planning is unavailable, not evidence of a meta intent.
    fallback = {
        "action": "clarify", "template": None, "user_job": "inform",
        "goal_summary": "", "vagueness": "partial", "known_slots": known,
        "missing_slots": [], "plan_sketch": [], "clarify_options": [],
        "reply": "本轮任务规划服务暂不可用，尚未启动任何专家步骤，请重试。",
        "confidence": 0.0, "source": "lead_unavailable",
        "risk_if_run": "没有有效的结构化执行计划",
    }
    if selected_capability is not None:
        fallback["capability_fit"] = "needs_clarification"
    return _finalize(fallback)


def task_lead_decide(message, context):
    """Use the existing structured Lead/client, with one bounded business enum."""
    raw = _llm_lead(message, card='', summary='', memory_digest='', template_hint=None,
                    known_slots=[], interaction_mode='experts', selected_capability={
                        'execution_pipeline': 'student_task_v1', 'capability_id': 'student-task',
                        'task_context': context})
    if not isinstance(raw, dict) or raw.get('agent') not in {
        'resume_preparation', 'career_exploration', 'practice_review', 'conversation_recall', 'clarify',
    }:
        raise ValueError('主 Agent 未形成有效的业务委派，尚未启动分析')
    roles = raw.get('compared_roles') or []
    if not isinstance(roles, list) or len(roles) > 2 or any(r not in context['catalogue'] for r in roles):
        raise ValueError('主 Agent 返回了目录之外的方向，未保存')
    action = raw.get('document_action', 'discuss')
    if action not in {'discuss', 'continue', 'restart'}:
        raise ValueError('主 Agent 未形成有效的材料读取意图')
    return {'agent': raw['agent'], 'goal_summary': str(raw.get('goal_summary') or '')[:300],
            'reply': str(raw.get('reply') or '')[:1000], 'compared_roles': roles, 'document_action': action}


def _llm_lead(
    message: str,
    *,
    card: str,
    summary: str,
    memory_digest: str,
    template_hint: Optional[str],
    known_slots: Sequence[str],
    interaction_mode: str,
    selected_capability: Optional[Dict[str, Any]] = None,
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
                interaction_mode=interaction_mode,
                content_kind=('task_lead' if selected_capability and selected_capability.get('execution_pipeline') == 'student_task_v1' else 'lead'),
            ),
            variables={
                "templates": templates,
                "roles_json": json.dumps(sorted(SUBAGENTS), ensure_ascii=False),
                "skill_cards_json": json.dumps(
                    skill_cards(
                        capability_id=(
                            str(selected_capability.get("capability_id") or "")
                            if isinstance(selected_capability, dict) else None
                        ),
                    ),
                    ensure_ascii=False,
                ),
                "slots": slots,
                "known_slots_json": json.dumps(list(known_slots) or [], ensure_ascii=False),
                "template_hint": template_hint or "none",
                "card": truncate_slot(card or "", 500),
                "summary": truncate_slot(summary or "", 400),
                "memory_digest": truncate_slot(memory_digest or "", 800),
                "selected_capability_json": json.dumps(
                    selected_capability, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ) if selected_capability is not None else "null",
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
    selected_capability: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    del blackboard  # reserved for richer gates
    hint = None  # Compatibility parameter cannot revive keyword routing.
    user_job = str(data.get("user_job") or "").strip().lower()
    if user_job not in _USER_JOBS:
        user_job = "inform"

    action = str(data.get("action") or "clarify").strip().lower()
    if action not in ("explain", "clarify", "run_plan"):
        action = "clarify"

    # Meta always explains in the legacy/unspecified Experts flow. An explicit
    # package remains the task contract; its Lead must emit a scoped QA plan or
    # explicitly say that the selected package does not fit.
    if user_job == "meta" and selected_capability is None:
        action = "explain"

    capability_fit = None
    if selected_capability is not None:
        capability_fit = str(data.get("capability_fit") or "").strip().lower()
        if capability_fit not in {"supported", "unsupported", "needs_clarification"}:
            capability_fit = "needs_clarification"

    template = data.get("template")
    if template is not None:
        template = str(template).strip()
        if template not in PLAN_TEMPLATES:
            template = None

    plan_error = ""
    steps: List[Dict[str, Any]] = []
    has_dynamic_steps = "steps" in data
    if has_dynamic_steps:
        try:
            steps = validate_plan_steps(
                data.get("steps"),
                capability_id=(
                    str(selected_capability.get("capability_id") or "")
                    if isinstance(selected_capability, dict) else None
                ),
            )
        except ValueError as exc:
            plan_error = str(exc)
    elif template:
        # Backward compatibility with template-only planner outputs.
        legacy_goal = str(
            data.get("handoff_brief") or data.get("goal_summary") or message or ""
        ).strip()
        if not legacy_goal:
            plan_error = "template-only plan requires a task goal"
        else:
            steps = [
                {"role": role, "goal": legacy_goal[:400], "skill_ids": []}
                for role in PLAN_TEMPLATES[template]
            ]

    goal_summary = str(data.get("goal_summary") or "").strip()
    vagueness = str(data.get("vagueness") or "partial").strip().lower()
    if vagueness not in _VAGUENESS:
        vagueness = "partial"

    model_missing = _clean_slots(data.get("missing_slots"))
    # A dynamic execution plan is gated by server-derived prerequisites for
    # its validated roles. Model-authored missing slots are not authoritative:
    # they can describe a broader workflow than a narrow read-only QA step.
    # Explicit clarify decisions and legacy template-only plans keep their
    # model-authored clarification context, with deterministic gaps added.
    if has_dynamic_steps:
        required = _missing_for_steps(steps, known_slots)
    elif template:
        required = _missing_for_template(template, known_slots)
    else:
        required = _missing_for_steps(steps, known_slots)
    missing = (
        required
        if action == "run_plan" and has_dynamic_steps
        else model_missing
    )
    for slot in required:
        if slot not in missing:
            missing.append(slot)

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
        if plan_error:
            action = "clarify"
            reply = "本轮计划未通过服务端角色、步骤或能力校验，尚未启动任务。请缩小目标后重试。"
            risk = plan_error
        elif not steps:
            action = "clarify"
            reply = reply or (
                "还需要更明确的目标，我才能启动 Agent 计划。"
                "例如：从简历生成职业报告。"
            )
        elif vagueness == "vague":
            action = "clarify"
        elif missing:
            action = "clarify"

        if selected_capability is not None and capability_fit != "supported":
            action = "clarify"
            if capability_fit == "unsupported":
                reply = reply or (
                    "当前请求超出已选能力包范围，请切换到普通 Agent 或适用的能力包。"
                )
            else:
                reply = reply or "请补充目标或背景，我再判断所选能力包能否安全承接。"

    if action == "run_plan":
        if not sketch:
            sketch = [_role_label(step["role"]) for step in steps]
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
        if not sketch and steps:
            sketch = [_role_label(step["role"]) for step in steps]
        reply = reply or _clarify_reply(goal_summary, missing, options)
        handoff = ""

    return _finalize(
        {
            "action": action,
            "template": template if action == "run_plan" else None,
            "steps": steps if action == "run_plan" else [],
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
            **({"capability_fit": capability_fit} if capability_fit is not None else {}),
        }
    )


def _finalize(packet: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "action": packet.get("action") or "clarify",
        "template": packet.get("template"),
        "steps": list(packet.get("steps") or []),
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
    if packet.get("capability_fit") in {
        "supported", "unsupported", "needs_clarification",
    }:
        result["capability_fit"] = packet["capability_fit"]
    return result


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


def _missing_for_steps(steps: Sequence[Dict[str, Any]], known: Sequence[str]) -> List[str]:
    kn = set(known)
    roles = {step.get("role") for step in steps}
    missing: List[str] = []
    if "profile" in roles and "resume" not in kn:
        missing.append("resume")
    if "match" in roles and not ({"portrait", "resume"} & kn):
        missing.append("portrait")
    if "report" in roles and "match" not in kn and "match" not in roles:
        missing.append("match")
    return missing


def _role_label(role: str) -> str:
    return {
        "profile": "生成或核对职业画像",
        "match": "执行岗位匹配",
        "report": "整理职业报告",
        "planner": "制定行动规划",
        "qa": "只读问答与解释",
    }.get(role, "处理任务")


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
