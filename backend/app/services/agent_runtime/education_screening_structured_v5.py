"""Opt-in v5 education screening with server-owned user-visible wording.

v5 reuses the v4 extractor and source projection, but replaces its free-text
finalizer with a typed selection-only plan. Every published sentence is
rendered by this server module from an allowlisted conclusion status/action;
the model cannot add a condition, exception, employer rule, or factual claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import education_screening as v1
from . import education_screening_structured_v4 as v4
from .budget import current_usage_ledger


CAPABILITY_ID = "education-screening-structured-v5"
PIPELINE_ID = "education_screening_structured_v5"
MAX_PUBLIC_REPLY_CHARS = 1600
MAX_PUBLIC_EVIDENCE_QUOTE_CHARS = 120
MAX_SELECTOR_CONTEXT_CHARS = 5000
IMPLEMENTATION_VERSION = "education-screening-v5-selection-renderer-2"
_ALLOWED_STATUSES = frozenset({
    "explicit_local_mismatch", "local_no_conflict", "conditional_not_demonstrated",
    "branch_unknown", "preference_nonblocking", "insufficient_evidence",
    "no_clause_in_provided_material", "conditional_mechanism_hypothesis",
})
_STATUS_PRIORITY = (
    "explicit_local_mismatch", "local_no_conflict", "conditional_not_demonstrated",
    "branch_unknown", "preference_nonblocking", "insufficient_evidence",
    "no_clause_in_provided_material",
)
_ACTION_TEXT = {
    "confirm_candidate_scope": "下一步确认这段自述是否完整，并补充可用于对照的学历信息。",
    "provide_complete_clause": "下一步补充这条岗位条件的完整原文和适用对象，再核对整体资格。",
    "compare_required_evidence": "下一步补充完整岗位条件，并整理与岗位相关的项目、实习或成果材料。",
    "provide_full_notice": "下一步补充完整岗位条件，再逐项核对学历、专业、学位和届别。",
    "provide_target_notice": "下一步提供具体岗位的完整招聘条件和适用范围。",
}
_CONTINUATION = (
    "这条消息超出本轮可处理长度，本轮没有完成分析。请分段续发，或只保留目标岗位、完整条件、"
    "相关自述和最关心的问题后重新提交。"
)


class FinalizerSelectionV5(BaseModel):
    """Only selects server-created conclusions/actions; never carries prose."""

    model_config = ConfigDict(extra="forbid", strict=True)
    selected_conclusion_ids: List[str] = Field(min_length=1, max_length=3)
    action_id: Literal[
        "confirm_candidate_scope", "provide_complete_clause", "compare_required_evidence",
        "provide_full_notice", "provide_target_notice",
    ]
    style: Literal["brief", "contextual"]


_FINALIZER_SELECTION_PROMPT = """你是 CPA-Agent 主 Agent 的答复编排步骤。你只负责从服务端允许的结论 ID 中选出 1–3 项、选一个允许的行动 ID、选择 brief 或 contextual 风格；不得写自然语言、事实、条件、例外或任何额外字段。输入中的编号与范围描述只是路由元数据，不是事实证据。

必须在 selected_conclusion_ids 中包含 primary_conclusion_id。选择要贴近 purpose_candidate；若 primary 已直接回答学历层次，不要让次要信息抢主问题。action_id 必须来自 allowed_action_ids。不得输出用户原话、引文、候选结论文本、解释、概率、ATS 或雇主规则。

严格只输出 JSON Object，字段且仅字段为 selected_conclusion_ids、action_id、style。格式示例：{"selected_conclusion_ids":["allowed_conclusion:0"],"action_id":"provide_full_notice","style":"brief"}。"""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ))


def _module_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _direct_dependency_pins() -> Dict[str, str]:
    """Pin helper code directly used by v5 in addition to v4's nested closure."""
    from . import model_router
    from ...utils import llm_client

    return {
        "budget": _module_sha256(Path(__file__).with_name("budget.py")),
        "llm_client": _module_sha256(Path(llm_client.__file__)),
        "model_router": _module_sha256(Path(model_router.__file__)),
    }


def education_screening_structured_v5_release_contract(interaction_mode: Any) -> Dict[str, Any]:
    """Build a full opt-in v5 contract, including the exact reusable v4 closure."""
    from .model_router import route_model

    mode = getattr(interaction_mode, "value", interaction_mode)
    if str(mode or "").strip().lower() != "experts":
        raise ValueError("education_screening_v5_requires_experts_mode")
    route = route_model("ask_answer")
    v4_contract = v4.education_screening_structured_release_contract("experts")
    return {
        "contract_version": 5,
        "capability_id": CAPABILITY_ID,
        "interaction_mode": "experts",
        "extractor_release": v4_contract,
        "extractor_release_sha256": _canonical_sha256(v4_contract),
        "selector": {
            "prompt_sha256": _sha256_text(_FINALIZER_SELECTION_PROMPT),
            "schema_sha256": _canonical_sha256(FinalizerSelectionV5.model_json_schema()),
            "message_roles": ["system", "user"],
            "route_task": "ask_answer",
            "route": dict(route),
            "temperature": 0.0,
            "max_tokens": 320,
            "response_format_kind": "json_object",
        },
        "renderer": {
            "version": IMPLEMENTATION_VERSION,
            "module": "app.services.agent_runtime.education_screening_structured_v5",
            "source_sha256": _module_sha256(Path(__file__)),
            "direct_dependency_source_sha256": _direct_dependency_pins(),
            "nested_v4_contract_sha256": _canonical_sha256(v4_contract),
        },
    }


def _contract_matches(expected: Any) -> bool:
    try:
        return isinstance(expected, dict) and expected == education_screening_structured_v5_release_contract(
            "experts",
        )
    except Exception:
        return False


def _safe_source_refs(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs = []
    for ref in value[:4]:
        if not isinstance(ref, Mapping):
            continue
        quote = ref.get("source_quote")
        if (not isinstance(quote, str) or not 0 < len(quote) <= 300
                or ref.get("quote_integrity") != "exact_substring_only"):
            continue
        source_id = ref.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 96:
            continue
        refs.append({key: ref[key] for key in (
            "id", "source_id", "source_kind", "source_version", "source_quote",
            "quote_integrity", "interpretation_status",
        ) if key in ref})
    return refs


def _public_evidence_quote(ref: Mapping[str, Any]) -> Optional[str]:
    """Return a short, single-line exact quote with an allowlisted provenance kind."""
    quote = ref.get("source_quote")
    if (not isinstance(quote, str) or not 0 < len(quote) <= MAX_PUBLIC_EVIDENCE_QUOTE_CHARS
            or ref.get("quote_integrity") != "exact_substring_only"
            or ref.get("source_kind") not in {"user_provided_unverified", "server_readonly_receipt"}):
        return None
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in quote):
        return None
    return quote


def _ref_for_source(row: Mapping[str, Any], source_ref_id: str) -> Optional[Mapping[str, Any]]:
    for ref in row.get("source_refs", []) if isinstance(row.get("source_refs"), list) else []:
        if (isinstance(ref, Mapping) and ref.get("id") == source_ref_id
                and _public_evidence_quote(ref) is not None):
            return ref
    return None


def _source_text_for(row: Mapping[str, Any], source_ref_id: str, source_label: str) -> Optional[str]:
    ref = _ref_for_source(row, source_ref_id)
    if ref is None:
        return None
    quote = _public_evidence_quote(ref)
    if ref.get("source_kind") == "user_provided_unverified":
        prefix = f"你提供且未核验的{source_label}"
    elif ref.get("source_kind") == "server_readonly_receipt":
        prefix = f"已读取来源中的{source_label}"
    else:
        return None
    return f"{prefix}「{quote}」" if quote else None


def _source_pair_text_for(
    row: Mapping[str, Any], *, job_label: str, candidate_label: str,
) -> Optional[str]:
    job_ref = _ref_for_source(row, "job_condition_source")
    candidate_ref = _ref_for_source(row, "candidate_statement_source")
    if job_ref is None or candidate_ref is None:
        return None
    job_quote = _public_evidence_quote(job_ref)
    candidate_quote = _public_evidence_quote(candidate_ref)
    if not job_quote or not candidate_quote:
        return None
    if job_ref.get("source_kind") == candidate_ref.get("source_kind") == "user_provided_unverified":
        return (
            f"你提供且未核验的{job_label}「{job_quote}」和"
            f"{candidate_label}「{candidate_quote}」"
        )
    if job_ref.get("source_kind") == candidate_ref.get("source_kind") == "server_readonly_receipt":
        return f"已读取来源中的{job_label}「{job_quote}」和{candidate_label}「{candidate_quote}」"
    job_source = _source_text_for(row, "job_condition_source", job_label)
    candidate_source = _source_text_for(row, "candidate_statement_source", candidate_label)
    if not job_source or not candidate_source:
        return None
    return f"{job_source}和{candidate_source}"


def _primary_id(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    for status in _STATUS_PRIORITY:
        row = next((item for item in rows if item.get("status") == status), None)
        if row is not None:
            return str(row.get("id") or "") or None
    return str(rows[0].get("id") or "") if rows else None


def project_v5_conclusions(projection_v4: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep provenance/status, discard v4's prose statements from publication."""
    data = projection_v4 if isinstance(projection_v4, Mapping) else {}
    analysis_status = str(data.get("analysis_status") or "unavailable")[:48]
    rows: List[Dict[str, Any]] = []
    for row in (data.get("allowed_conclusions") or [])[:8]:
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        row_id = row.get("id")
        if (status not in _ALLOWED_STATUSES - {"conditional_mechanism_hypothesis"}
                or not isinstance(row_id, str) or not re.fullmatch(r"allowed_conclusion:\d+", row_id)):
            continue
        refs = _safe_source_refs(row.get("source_refs"))
        ref_ids = {ref.get("id") for ref in refs}
        renderable_ref_ids = {
            ref.get("id") for ref in refs if _public_evidence_quote(ref) is not None
        }
        if status in {
            "explicit_local_mismatch", "local_no_conflict", "conditional_not_demonstrated",
        }:
            required_ref_ids = {"job_condition_source", "candidate_statement_source"}
        elif status in {"branch_unknown", "preference_nonblocking"}:
            required_ref_ids = {"job_condition_source"}
        else:
            required_ref_ids = set()
        missing_required_refs = bool(required_ref_ids - ref_ids) or bool(
            required_ref_ids - renderable_ref_ids
        )
        if missing_required_refs:
            status = "insufficient_evidence"
        rows.append({
            "id": row_id,
            "status": status,
            "scope": ("required_source_references_missing" if status == "insufficient_evidence"
                      and missing_required_refs else str(row.get("scope") or "")[:120]),
            "source_refs": refs,
        })
    incomplete_reason = None
    if analysis_status not in {"model_candidate_unverified", "partial"}:
        incomplete_reason = "structured_extraction_not_completed"
    elif (analysis_status == "partial" and rows and all(
        row.get("status") in {"no_clause_in_provided_material", "insufficient_evidence"}
        for row in rows
    )):
        incomplete_reason = "structured_extraction_partial_without_usable_clause"
    if incomplete_reason:
        rows = [{
            "id": "allowed_conclusion:0", "status": "insufficient_evidence",
            "scope": incomplete_reason, "source_refs": [],
        }]
    if not rows:
        rows = [{
            "id": "allowed_conclusion:0", "status": "insufficient_evidence",
            "scope": "no_valid_server_projection", "source_refs": [],
        }]
    primary_id = _primary_id(rows)
    primary = next(row for row in rows if row["id"] == primary_id)

    # The mechanism note is a pinned non-statistical Skill hypothesis, not a
    # JD/profile fact. Keep it secondary and only when no local degree result
    # can answer the user's stated job-condition question.
    if (data.get("market_context") == "conditional_non_statistical_method_hypothesis"
            and primary["status"] in {"no_clause_in_provided_material", "insufficient_evidence"}
            and isinstance(data.get("target_role"), Mapping)):
        rows.append({
            "id": "allowed_conclusion:market_mechanism",
            "status": "conditional_mechanism_hypothesis",
            "scope": "general_non_statistical_method_hypothesis_only",
            "source_refs": [{
                "id": "skill_reference:evidence-scoping",
                "source_id": "education-screening-context:evidence-scoping",
                "source_kind": "skill_method_not_job_or_profile_fact",
                "source_version": "pinned_in_extractor_release",
                "source_quote": "在投递量大、面试名额有限时，某些招聘流程可能把教育背景当成低成本的初筛代理；若岗位稀缺、研究/数学训练要求高、竞争强，这种摩擦可能更明显。",
                "quote_integrity": "pinned_skill_reference_excerpt",
                "interpretation_status": "conditional_non_statistical_hypothesis",
            }],
        })

    action = data.get("next_action") if isinstance(data.get("next_action"), Mapping) else {}
    action_id = str(action.get("id") or "")
    if action_id not in _ACTION_TEXT:
        action_id = "provide_full_notice"
    return {
        "schema_version": 5,
        "analysis_status": analysis_status,
        "analysis_incomplete_reason": incomplete_reason,
        "purpose_candidate": str(data.get("purpose_candidate") or "unclear")[:48],
        "primary_conclusion_id": primary_id,
        "allowed_conclusions": rows[:9],
        "allowed_action_ids": [action_id],
        "overall_qualification": "not_determined",
    }


def _selector_context(projection: Mapping[str, Any]) -> Dict[str, Any]:
    """No user message, quote, condition, or free-text conclusion enters selector."""
    return {
        "purpose_candidate": projection.get("purpose_candidate"),
        "primary_conclusion_id": projection.get("primary_conclusion_id"),
        "allowed_conclusions": [{
            "id": row.get("id"), "status": row.get("status"), "scope": row.get("scope"),
        } for row in (projection.get("allowed_conclusions") or [])[:9]
          if isinstance(row, Mapping)],
        "allowed_action_ids": list(projection.get("allowed_action_ids") or [])[:4],
        "style_options": ["brief", "contextual"],
    }


def _validate_selection(selection: FinalizerSelectionV5, projection: Mapping[str, Any]) -> bool:
    rows = [row for row in (projection.get("allowed_conclusions") or []) if isinstance(row, Mapping)]
    allowed_ids = {str(row.get("id")) for row in rows}
    selected = selection.selected_conclusion_ids
    primary = projection.get("primary_conclusion_id")
    return bool(
        1 <= len(selected) <= 3
        and len(set(selected)) == len(selected)
        and all(item in allowed_ids for item in selected)
        and isinstance(primary, str) and primary in selected
        and selection.action_id in (projection.get("allowed_action_ids") or [])
        and selection.style in {"brief", "contextual"}
    )


def _render_conclusion(row: Mapping[str, Any], style: str) -> str:
    status = row.get("status")
    if status == "explicit_local_mismatch":
        evidence = _source_pair_text_for(
            row, job_label="岗位条款", candidate_label="学历自述",
        )
        if not evidence:
            return "目前提供的材料不足以完成该项局部学历比较，整体资格仍未判定。"
        return (
            f"按{evidence}，"
            "学历层次这一项暂不满足其中的明示最低条件；"
            "这只是局部比较，不是整体资格结论。"
        )
    if status == "conditional_not_demonstrated":
        evidence = _source_pair_text_for(
            row, job_label="岗位条款", candidate_label="学历自述",
        )
        if not evidence:
            return "目前提供的材料不足以完成该项局部学历比较，整体资格仍未判定。"
        return (
            f"按{evidence}，"
            "当前材料尚未显示达到该学历层次；"
            "这不等于确认最高学历或整体资格不符合。"
        )
    if status == "local_no_conflict":
        evidence = _source_pair_text_for(
            row, job_label="岗位条件", candidate_label="学历自述",
        )
        if not evidence:
            return "目前提供的材料不足以完成该项局部学历比较，整体资格仍未判定。"
        return (
            f"按{evidence}，"
            "学历层次这一项暂未显示冲突；"
            "这不代表专业、学位、届别或整体资格已符合。"
        )
    if status == "branch_unknown":
        job_source = _source_text_for(row, "job_condition_source", "岗位片段")
        if not job_source:
            return "目前提供的材料不足以确认岗位条件关系或适用范围，不能据此判断整体资格。"
        return (
            f"按{job_source}，条件关系或适用范围尚不能完整确认，"
            "目前不能据此判断整体资格。"
        )
    if status == "preference_nonblocking":
        job_source = _source_text_for(row, "job_condition_source", "岗位片段")
        if not job_source:
            return "目前材料不足以确认这条内容是否为优先项，不能据此排除投递。"
        return (
            f"按{job_source}，这里是优先项，"
            "不是可据以排除投递的硬门槛。"
        )
    if status == "no_clause_in_provided_material":
        return (
            "本轮没有得到可核对的明确学历条款，不足以判断具体投递价值或量化录用机会；"
            "这不代表完整公告没有相关条件。"
        )
    if status == "conditional_mechanism_hypothesis":
        prefix = "一般招聘语境（非该岗位事实）："
        mechanism = (
            "在投递量大、面试名额有限时，部分筛选流程可能把教育背景作为低成本初筛代理；"
            "岗位稀缺或竞争较强时，这种摩擦可能更明显。"
        )
        boundary = "这只是非统计的机制假设，不代表具体雇主规则，也不预测个人录用概率。"
        if style == "contextual":
            return prefix + mechanism + " " + boundary
        return prefix + mechanism + " 这是非统计假设，不代表具体雇主规则。"
    return "目前提供的材料不足以完成该项局部学历比较，整体资格仍未判定。"


def render_v5_reply(
    projection: Mapping[str, Any], *, selected_conclusion_ids: Sequence[str],
    action_id: str, style: str,
) -> str:
    """Render only server-authored text for checked conclusion/action IDs."""
    rows = [row for row in (projection.get("allowed_conclusions") or [])
            if isinstance(row, Mapping)]
    by_id = {str(row.get("id")): row for row in rows}
    primary = projection.get("primary_conclusion_id")
    selected = list(selected_conclusion_ids)
    if (not isinstance(primary, str) or primary not in by_id or primary not in selected
            or len(selected) > 3 or len(set(selected)) != len(selected)
            or any(item not in by_id for item in selected)):
        selected = [primary] if isinstance(primary, str) and primary in by_id else []
    # The primary conclusion is rendered first even if the selector returned it
    # later. It therefore cannot be hidden behind a secondary preference/risk.
    selected = [primary] + [item for item in selected if item != primary]
    allowed_actions = projection.get("allowed_action_ids") or []
    if action_id not in allowed_actions:
        action_id = allowed_actions[0] if allowed_actions else "provide_full_notice"
    action = _ACTION_TEXT.get(action_id) or _ACTION_TEXT["provide_full_notice"]
    if style not in {"brief", "contextual"}:
        style = "brief"
    points = [_render_conclusion(by_id[item], style) for item in selected[:3]]
    points.append(action)
    return "\n\n".join(points[:4])[:MAX_PUBLIC_REPLY_CHARS]


def _fallback(projection: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    primary = projection.get("primary_conclusion_id")
    action_ids = projection.get("allowed_action_ids") or []
    action_id = action_ids[0] if action_ids else "provide_full_notice"
    reply = render_v5_reply(
        projection,
        selected_conclusion_ids=[primary] if isinstance(primary, str) else [],
        action_id=str(action_id), style="brief",
    )
    return {
        "reply": reply,
        "decision_status": "server_fallback",
        "decision_reason": reason[:64],
        "selector": {"usage_status": "not_observed", "selected_conclusion_ids": [],
                     "action_id": None, "style": None},
    }


def _incomplete_analysis(reason: str) -> Dict[str, Any]:
    """Do not turn extractor/provider failure into a claim that no clause exists."""
    return {
        "reply": (
            "本轮没有完成岗位条件分析，因此未据此判断资格或投递价值。请稍后重试；"
            "若原消息较长，可只按目标岗位、相关条款和自述分段补充，不必重发无关背景。"
        ),
        "decision_status": "server_fallback",
        "decision_reason": reason[:64],
        "selector": {"usage_status": "not_called", "selected_conclusion_ids": [],
                     "action_id": None, "style": None},
    }


def select_finalizer_plan(
    *, projection: Mapping[str, Any], expected_runtime_contract: Dict[str, Any],
    llm: Any = None,
) -> Dict[str, Any]:
    """Use one JSON Object call to select IDs; public copy remains server-owned."""
    if not _contract_matches(expected_runtime_contract):
        return _fallback(projection, "runtime_release_changed")
    selector_contract = expected_runtime_contract.get("selector")
    if not isinstance(selector_contract, Mapping):
        return _fallback(projection, "selector_contract_unavailable")
    if (selector_contract.get("prompt_sha256") != _sha256_text(_FINALIZER_SELECTION_PROMPT)
            or selector_contract.get("schema_sha256") != _canonical_sha256(
                FinalizerSelectionV5.model_json_schema())
            or selector_contract.get("message_roles") != ["system", "user"]
            or selector_contract.get("response_format_kind") != "json_object"
            or selector_contract.get("max_tokens") != 320
            or selector_contract.get("temperature") != 0.0):
        return _fallback(projection, "selector_contract_changed")
    ledger = current_usage_ledger()
    calls = ledger.get("calls") if isinstance(ledger, dict) else None
    route = selector_contract.get("route")
    if (not isinstance(calls, list) or not isinstance(route, Mapping)
            or not isinstance(route.get("model"), str)):
        return _fallback(projection, "step_usage_or_route_unavailable")
    try:
        context = _selector_context(projection)
        context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(context_json) > MAX_SELECTOR_CONTEXT_CHARS:
            return _fallback(projection, "selector_context_over_limit")
    except Exception:
        return _fallback(projection, "selector_context_invalid")
    if llm is None:
        from ...utils.llm_client import get_llm_client
        llm = get_llm_client()
    before = len(calls)
    messages = [
        {"role": "system", "content": _FINALIZER_SELECTION_PROMPT},
        {"role": "user", "content": context_json},
    ]
    try:
        response = llm.chat_with_metadata(
            messages=messages, temperature=selector_contract["temperature"],
            max_tokens=selector_contract["max_tokens"],
            model_override=route["model"], response_format={"type": "json_object"},
        )
    except Exception:
        return _fallback(projection, "selector_call_unavailable")
    observed = v4.v3._last_call_metadata(ledger, after_index=before)
    provider = str(getattr(response, "provider", "") or "")
    model = str(getattr(response, "model", "") or "")
    finish_reason = str(getattr(response, "finish_reason", "") or "")
    usage = v4._valid_usage(getattr(response, "usage", None))
    metadata = {
        "provider": provider[:48] or None, "model": model[:128] or None,
        "finish_reason": finish_reason[:32] or None, "usage": usage,
        "usage_status": "observed" if usage else "unverified",
        "prompt_sha256": selector_contract["prompt_sha256"],
        "schema_sha256": selector_contract["schema_sha256"],
        "context_sha256": _sha256_text(context_json),
        "route_model": str(route["model"])[:128],
    }
    if (len(calls) != before + 1 or usage is None or observed.get("provider") != provider
            or observed.get("model") != model or observed.get("finish_reason") != finish_reason
            or v4._valid_usage(observed.get("usage")) != usage):
        result = _fallback(projection, "selector_usage_unverified")
        result["selector"] = metadata
        return result
    if finish_reason not in {"stop", "end_turn"}:
        result = _fallback(projection, "selector_response_incomplete")
        result["selector"] = metadata
        return result
    raw = getattr(response, "text", None)
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 2000:
        result = _fallback(projection, "selector_response_invalid")
        result["selector"] = metadata
        return result
    try:
        selection = FinalizerSelectionV5.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError, TypeError):
        result = _fallback(projection, "selector_schema_invalid")
        result["selector"] = metadata
        return result
    if not _validate_selection(selection, projection):
        result = _fallback(projection, "selector_selection_invalid")
        result["selector"] = metadata
        return result
    metadata.update({
        "selected_conclusion_ids": list(selection.selected_conclusion_ids),
        "action_id": selection.action_id,
        "style": selection.style,
    })
    return {
        "reply": render_v5_reply(
            projection, selected_conclusion_ids=selection.selected_conclusion_ids,
            action_id=selection.action_id, style=selection.style,
        ),
        "decision_status": "server_rendered_from_selection",
        "decision_reason": None,
        "selector": metadata,
    }


def run_structured_education_step_v5(
    *, user_message: str, owner_id: str, run_id: str,
    expected_runtime_contract: Dict[str, Any],
    verified_school_facts: Sequence[Mapping[str, Any]] = (),
    receipts: Sequence[v1.ReadOnlySourceReceipt] = (), llm: Any = None,
) -> Dict[str, Any]:
    """Run v4 typed extraction plus v5 ID-only selection within one step."""
    message = user_message if isinstance(user_message, str) else ""
    if not _contract_matches(expected_runtime_contract):
        analysis = v4._empty_analysis(
            user_message=message, status="unavailable", error_code="runtime_release_changed",
        )
        v4_projection = v4.project_structured_education_evidence_v4(
            message, analysis, verified_school_facts,
        )
        projection = project_v5_conclusions(v4_projection)
        finalizer = _fallback(projection, "runtime_release_changed")
    elif len(message) > v4.MAX_USER_MESSAGE_CHARS:
        analysis = v4._empty_analysis(
            user_message=message, status="partial", error_code="user_message_over_limit",
        )
        v4_projection = v4.project_structured_education_evidence_v4(
            message, analysis, verified_school_facts,
        )
        projection = project_v5_conclusions(v4_projection)
        finalizer = {"reply": _CONTINUATION, "decision_status": "server_fallback",
                     "decision_reason": "user_message_over_limit",
                     "selector": {"usage_status": "not_called"}}
    else:
        analysis = v4.extract_structured_education_context(
            user_message=message, owner_id=owner_id, run_id=run_id,
            verified_school_facts=verified_school_facts, receipts=receipts,
            expected_runtime_contract=expected_runtime_contract["extractor_release"], llm=llm,
        )
        v4_projection = v4.project_structured_education_evidence_v4(
            message, analysis, verified_school_facts,
        )
        projection = project_v5_conclusions(v4_projection)
        if analysis.get("status") in {"model_candidate_unverified", "partial"}:
            if projection.get("analysis_incomplete_reason"):
                finalizer = _incomplete_analysis(
                    str(projection["analysis_incomplete_reason"]),
                )
            else:
                finalizer = select_finalizer_plan(
                    projection=projection, expected_runtime_contract=expected_runtime_contract,
                    llm=llm,
                )
        else:
            finalizer = _incomplete_analysis(
                str(analysis.get("error_code") or "analysis_unavailable"),
            )
    try:
        assets = v4.load_v4_assets()
        skill_versions = v4.v2._school_skill_versions(assets, verified_school_facts)
    except Exception:
        skill_versions = []
        assets = {"references": []}
    tool_calls = [{
        "kind": "skill_reference_read", "tool_name": "skill_reference_read",
        "status": "done", "source_id": ref.reference_id, "version": ref.version,
    } for ref in assets["references"]]
    return {
        "reply": str(finalizer.get("reply") or _CONTINUATION)[:MAX_PUBLIC_REPLY_CHARS],
        "verified_facts": [dict(row) for row in list(verified_school_facts)[:4]
                           if isinstance(row, Mapping)],
        "skill_versions": skill_versions, "tool_calls": tool_calls,
        "education_screening_v5": {"schema_version": 5, "extractor_schema_version": 4,
                                    "analysis": analysis, "projection": projection},
        "education_screening_v4": analysis,
        "analysis": analysis, "projection": projection,
        "finalizer": finalizer,
    }
