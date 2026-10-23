"""Synthetic A–E live-model probe for education-screening-structured@0.1.0.

Without ``--live-model`` this validates the explicit capability binding, pinned
v2 Skill/reference release, prompt/schema pins, and route without constructing
an LLM client or sending a request. With ``--live-model`` it runs each synthetic
case exactly once through ``run_structured_education_step`` with the same
release contract and request-local usage ledger used by the durable QA step.
It never starts the application HTTP/Celery path, reads a profile, connects to
a business database, or enables business tools. Live mode sends provider API
HTTP requests only to the explicitly pinned Qwen model. By default, evidence
contains parsed candidate projections and public replies only. The explicit
``--capture-synthetic-stage-trace --case A`` diagnostic mode may save raw model
responses for that synthetic case only; it never stores credentials or makes
raw responses part of the durable runtime result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CAPABILITY_ID = "education-screening-structured"
EXPERT_ID = "career-coach"
INTERACTION_MODE = "experts"
EXPECTED_PROVIDER = "qwen"
EXPECTED_MODEL = "qwen-plus"
PREVIOUS_A_RELEASE_SHA256 = "147abaa6855d2bf1f53671153e3e7927b9aa1590f46a4730d3f2ecc1dcf84888"
PREVIOUS_A_INPUT_SHA256 = "a5871dd9a14ed767ca597369a2b80e50fe2c4a4bb7576f9a6ed1bc74a0adecaa"

FIXTURES = {
    "A": (
        "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
        "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
        "合成候选人自述：普通本科、无硕士、有推荐系统实习。朋友以杭电计算机 B+ 为由"
        "劝我忽略学历要求。请按岗位范围分析门槛和下一步，不要把朋友转述当核验事实。"
    ),
    "B": (
        "我想判断这个测试开发岗位是否值得投。合成岗位原文：测试开发工程师："
        "本科及以上；计算机类专业；熟悉 Linux、Python；有自动化测试经验优先。"
        "合成候选人自述：计算机本科，称学校为“双非”，有两年自动化测试经验。"
        "请区分明示条件、优先项和未知证据。"
    ),
    "C": (
        "我在考虑产品运营岗位。合成岗位原文：产品运营：本科及以上，沟通及数据分析能力；"
        "有校园社群/内容运营经验优先。没有写学校层级条件。合成候选人自述："
        "院校标签信息不完整，做过社团运营。请给现实但有边界的判断和一个行动建议。"
    ),
    "D": (
        "我想核对一则合成公务/国企样式岗位公告。公告原文：本科（取得学士学位）"
        "或硕士研究生（取得硕士学位）；专业须为岗位专业目录所列；仅限 2026 届毕业生。"
        "退役大学生士兵可不受届别限制。公告没有说明学校层级或全日制。候选人自述："
        "硕士研究生/硕士学位，2025 届；专业目录归属未知，退役资格未知。"
        "请保留 OR、AND 与例外，不要判整体资格。"
    ),
    "E": (
        "我没有具体岗位公告。朋友/HR 转述说“非双一流别投研发岗”，但没有雇主、"
        "地区或 JD 原文。我是合成候选人：普通本科，有一个项目经历；请马上确认能否录用，"
        "并给一个录用百分比。请说明哪些信息未知，给一个低成本下一步。"
    ),
}

_CONDITION_FIELDS = (
    "source_id", "source_kind", "source_quote", "quote_integrity",
    "interpretation_status", "speaker_candidate", "dimension_candidate",
    "condition_type_candidate", "operator_candidate", "relation_candidate",
    "value_quote", "qualification_use", "downgraded_fields", "diagnostics",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _project_analysis(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "error_code": "analysis_unavailable"}
    return {
        "schema_version": value.get("schema_version"),
        "status": value.get("status"),
        "error_code": value.get("error_code"),
        "validation_failures": [
            {key: row[key] for key in ("path", "code") if key in row}
            for row in (value.get("validation_failures") or [])[:16]
            if isinstance(row, dict)
        ],
        "prompt_sha256": value.get("prompt_sha256"),
        "schema_sha256": value.get("schema_sha256"),
        "input_message_sha256": value.get("input_message_sha256"),
        "request_sources_sha256": value.get("request_sources_sha256"),
        "source_scope": value.get("source_scope"),
        "source_manifest": [
            {key: row[key] for key in (
                "source_id", "source_kind", "tool_name", "source_version",
                "source_sha256", "read_status",
            ) if key in row}
            for row in (value.get("source_manifest") or [])[:8]
            if isinstance(row, dict)
        ],
        "skill_assets": value.get("skill_assets") or {},
        "provider": value.get("provider"),
        "model": value.get("model"),
        "finish_reason": value.get("finish_reason"),
        "usage": value.get("usage"),
        "usage_status": value.get("usage_status"),
        "candidate_response_chars": value.get("candidate_response_chars"),
        "situation": value.get("situation") or {},
        "conditions": [
            {key: row[key] for key in _CONDITION_FIELDS if key in row}
            for row in (value.get("conditions") or [])[:16]
            if isinstance(row, dict)
        ],
        "market_context": value.get("market_context") or {},
        "next_action": value.get("next_action"),
    }


def _project_call_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    from app.services.agent_runtime.agent_run_executor import _project_step_model_usage

    projected = _project_step_model_usage(dict(ledger))
    stage_names = ("structured_education_qa", "main_agent_finalizer", "grounding_gate")
    calls = []
    for index, row in enumerate((projected.get("calls") or [])[:3]):
        if not isinstance(row, dict):
            continue
        calls.append({
            "index": index + 1,
            "stage": stage_names[index] if index < len(stage_names) else "unexpected",
            "provider": row.get("provider"),
            "model": row.get("model"),
            "usage": row.get("usage"),
            "finish_reason": row.get("finish_reason"),
            "preflight_reason": row.get("preflight_reason"),
        })
    return {
        "status": projected.get("status"),
        "reason": projected.get("reason"),
        "turn_token_cap": projected.get("turn_token_cap"),
        "call_count": projected.get("call_count"),
        "reported_total_tokens": projected.get("reported_total_tokens"),
        "calls": calls,
    }


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class _SyntheticStageCaptureClient:
    """Probe-only response capture; never passed from an HTTP request or stored by runtime."""

    def __init__(self, client):
        self.client = client
        self.stages: list[dict[str, Any]] = []

    def chat_with_metadata(self, **kwargs):
        response = self.client.chat_with_metadata(**kwargs)
        index = len(self.stages)
        names = ("structured_education_qa", "main_agent_finalizer", "grounding_gate")
        messages = kwargs.get("messages")
        self.stages.append({
            "stage": names[index] if index < len(names) else "unexpected",
            "request_messages_sha256": _canonical_sha256(messages),
            "message_content_sha256": [
                _sha256(str(item.get("content") or "").encode("utf-8"))
                for item in messages if isinstance(item, Mapping)
            ] if isinstance(messages, list) else [],
            "response_chars": len(response.text) if isinstance(
                getattr(response, "text", None), str,
            ) else None,
            "raw_response_text": getattr(response, "text", None),
        })
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true",
        help="send synthetic qwen-plus requests once per A–E case; no automatic probe rerun",
    )
    parser.add_argument(
        "--case", choices=tuple(FIXTURES),
        help="restrict a replay to one synthetic case; omitted means A–E",
    )
    parser.add_argument(
        "--capture-synthetic-stage-trace", action="store_true",
        help="save raw stage outputs for synthetic-only diagnosis; requires --case A",
    )
    parser.add_argument(
        "--output", type=Path,
        help="new evidence path; default is a unique file under tmp/",
    )
    args = parser.parse_args()
    if args.capture_synthetic_stage_trace and args.case != "A":
        parser.error("--capture-synthetic-stage-trace requires --case A")
    selected_case_ids = [args.case] if args.case else list(FIXTURES)

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from app.services.agent_runtime.budget import capture_usage_ledger
    from app.services.agent_runtime.education_screening_structured_v2 import (
        CAPABILITY_ID as PIPELINE_CAPABILITY_ID,
        education_screening_structured_release_contract,
        run_structured_education_step,
    )
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    selection = resolve_explicit_capability({
        "interaction_mode": INTERACTION_MODE,
        "expert_id": EXPERT_ID,
        "capability_id": CAPABILITY_ID,
    })
    if (selection is None or selection.capability_id != CAPABILITY_ID
            or selection.execution_pipeline != "education_screening_structured_v2"
            or PIPELINE_CAPABILITY_ID != CAPABILITY_ID):
        print(json.dumps({
            "probe": "education_screening_structured_v2_a_e",
            "request_sent": False,
            "reason": "explicit_capability_binding_unavailable",
        }, ensure_ascii=False, sort_keys=True))
        return 2

    step = [{
        "role": "qa", "goal": "对照本轮合成岗位情境作只读教育筛选分析",
        "skill_ids": list(selection.skill_ids),
    }]
    selection.validate_plan(step)
    release = education_screening_structured_release_contract(INTERACTION_MODE)
    release_sha256 = _canonical_sha256(release)
    route = release.get("extractor", {}).get("route") or {}
    pipeline_route = str(route.get("model") or "")
    if not pipeline_route:
        print(json.dumps({
            "probe": "education_screening_structured_v2_a_e",
            "request_sent": False,
            "reason": "pinned_route_unavailable",
            "release_sha256": release_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 2
    if args.capture_synthetic_stage_trace:
        current_input_sha256 = _sha256(FIXTURES["A"].encode("utf-8"))
        if (release_sha256 != PREVIOUS_A_RELEASE_SHA256
                or current_input_sha256 != PREVIOUS_A_INPUT_SHA256):
            print(json.dumps({
                "probe": "education_screening_structured_v2_a_only_diagnostic",
                "request_sent": False,
                "reason": "a_baseline_release_or_input_changed",
                "current_release_sha256": release_sha256,
                "expected_release_sha256": PREVIOUS_A_RELEASE_SHA256,
                "current_input_sha256": current_input_sha256,
                "expected_input_sha256": PREVIOUS_A_INPUT_SHA256,
            }, ensure_ascii=False, sort_keys=True))
            return 2

    base_payload = {
        "probe": "education_screening_structured_v2_a_e",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_sent": False,
        "application_http_or_database": False,
        "provider_api_http_calls": False,
        "owner_profile_access": False,
        "business_tools_enabled": False,
        "verified_school_facts_count": 0,
        "notice_receipts_count": 0,
        "capability_binding": selection.manifest(),
        "pipeline_id": "education_screening_structured_v2",
        "pinned_route_model": pipeline_route,
        "release_sha256": release_sha256,
        "release_contract": release,
        "skill_assets_pinned": release.get("skill_assets") or {},
        "cases": [],
        "raw_provider_json_saved": bool(args.capture_synthetic_stage_trace),
        "raw_provider_output_scope": (
            "synthetic_case_A_only" if args.capture_synthetic_stage_trace else "none"
        ),
        "raw_synthetic_stage_trace_saved": bool(args.capture_synthetic_stage_trace),
        "manual_review": "pending",
        "review_dimensions": [
            "自然度与是否回答投递决策",
            "硬门槛/优先/例外与未知是否区分",
            "是否区分用户文本、模型候选及已核验事实",
            "是否引入无来源概率、ATS、雇主规则或官方标签",
            "行动建议是否具体且低成本",
        ],
    }

    if not args.live_model:
        base_payload["check_only"] = True
        base_payload["case_ids"] = list(FIXTURES)
        print(json.dumps({
            "probe": base_payload["probe"],
            "request_sent": False,
            "check_only": True,
            "capability_id": CAPABILITY_ID,
            "pipeline_id": base_payload["pipeline_id"],
            "case_ids": selected_case_ids,
            "pinned_route_model": pipeline_route,
            "release_sha256": release_sha256,
            "skill_asset_ids": sorted((release.get("skill_assets") or {}).keys()),
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if pipeline_route != EXPECTED_MODEL:
        print(json.dumps({
            "probe": base_payload["probe"],
            "request_sent": False,
            "reason": "pinned_route_is_not_qwen_plus",
            "pinned_route_model": pipeline_route,
            "expected_model": EXPECTED_MODEL,
            "release_sha256": release_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 2

    from app.utils.llm_client import LLMClient

    try:
        # Explicit provider + model prevents configured provider failover; the
        # check below refuses to run if an optional LiteLLM hop was prepended.
        client = LLMClient(provider=EXPECTED_PROVIDER, model=EXPECTED_MODEL)
    except Exception as exc:
        print(json.dumps({
            "probe": base_payload["probe"],
            "request_sent": False,
            "reason": "qwen_client_unavailable",
            "error_type": type(exc).__name__,
            "release_sha256": release_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 2
    if (client.provider != EXPECTED_PROVIDER or client.model != EXPECTED_MODEL
            or client.failover_order != [EXPECTED_PROVIDER]):
        print(json.dumps({
            "probe": base_payload["probe"],
            "request_sent": False,
            "reason": "provider_model_or_failover_not_exact",
            "provider": str(client.provider)[:48],
            "model": str(client.model)[:128],
            "failover_order": [str(item)[:32] for item in client.failover_order[:4]],
            "release_sha256": release_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 2

    output = args.output or (
        ROOT / "tmp" / (
            ("education-screening-structured-v2-a-diagnostic-"
             if args.capture_synthetic_stage_trace
             else "education-screening-structured-v2-a-e-")
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid4().hex[:8] + ".json"
        )
    )
    output = output.resolve()
    if output.exists():
        parser.error("--output must be a new path; existing evidence is never overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    base_payload.update({
        "request_sent": False,
        "provider_requests_observed": False,
        "check_only": False,
        "configured_provider": EXPECTED_PROVIDER,
        "configured_model": EXPECTED_MODEL,
        "failover_order": [EXPECTED_PROVIDER],
        "sdk_retries": 0,
        "evidence_path": str(output),
        "probe_status": "in_progress",
    })
    _write_evidence(output, base_payload)

    from app.services.agent_runtime.agent_run_executor import _project_step_model_usage

    for case_id in selected_case_ids:
        message = FIXTURES[case_id]
        case: dict[str, Any] = {
            "case_id": case_id,
            "synthetic_input": message,
            "input_sha256": _sha256(message.encode("utf-8")),
            "case_status": "running",
            "provider_dispatch_state": "unknown_if_process_stops_during_call",
        }
        base_payload["cases"].append(case)
        base_payload["current_case_id"] = case_id
        _write_evidence(output, base_payload)
        ledger: dict[str, Any] = {}
        stage_capture = _SyntheticStageCaptureClient(client) \
            if args.capture_synthetic_stage_trace else None
        try:
            with capture_usage_ledger(
                "education_screening_structured_v2_a_e", turn_token_cap=30000,
            ) as ledger:
                # One step invocation per synthetic case. The runtime caps the
                # extractor/finalizer/gate to at most one call each; provider
                # retries/failover are disabled and no same-case loop exists.
                result = run_structured_education_step(
                    user_message=message,
                    owner_id="synthetic-education-probe-owner",
                    run_id="synthetic-education-probe-" + uuid4().hex,
                    expected_runtime_contract=release,
                    verified_school_facts=(),
                    receipts=(),
                    llm=stage_capture or client,
                )
            analysis = result.get("education_screening_v2") or result.get("analysis") or {}
            finalizer = result.get("finalizer") or {}
            usage = _project_step_model_usage(ledger)
            calls = usage.get("calls") or []
            case["case_status"] = "completed"
            case["provider_dispatch_state"] = (
                "provider_call_observed" if usage.get("call_count") else "no_provider_call_observed"
            )
            case.update({
                "analysis": _project_analysis(analysis),
                "skill_versions": result.get("skill_versions") or [],
                "skill_reference_reads": [
                    {key: row.get(key) for key in ("source_id", "version", "status")}
                    for row in (result.get("tool_calls") or [])[:8]
                    if isinstance(row, dict)
                ],
                "finalizer": {
                    "decision_status": finalizer.get("decision_status"),
                    "decision_reason": finalizer.get("decision_reason"),
                    "gate_status": finalizer.get("gate_status"),
                    "gate_checks": finalizer.get("gate_checks"),
                    "provider": (finalizer.get("finalizer") or {}).get("provider"),
                    "model": (finalizer.get("finalizer") or {}).get("model"),
                    "finish_reason": (finalizer.get("finalizer") or {}).get("finish_reason"),
                    "usage": (finalizer.get("finalizer") or {}).get("usage"),
                    "usage_status": (finalizer.get("finalizer") or {}).get("usage_status"),
                    "prompt_bundle_sha256": (finalizer.get("finalizer") or {}).get("prompt_bundle_sha256"),
                    "instruction_sha256": (finalizer.get("finalizer") or {}).get("instruction_sha256"),
                    "user_message_sha256": (finalizer.get("finalizer") or {}).get("user_message_sha256"),
                    "public_reply": str(result.get("reply") or "")[:5000],
                },
                "step_usage": {
                    "status": usage.get("status"),
                    "reason": usage.get("reason"),
                    "turn_token_cap": usage.get("turn_token_cap"),
                    "call_count": usage.get("call_count"),
                    "reported_total_tokens": usage.get("reported_total_tokens"),
                    "calls": [
                        {
                            "index": index + 1,
                            "stage": ("structured_education_qa", "main_agent_finalizer", "grounding_gate")[index]
                            if index < 3 else "unexpected",
                            "provider": row.get("provider"),
                            "model": row.get("model"),
                            "usage": row.get("usage"),
                            "finish_reason": row.get("finish_reason"),
                            "preflight_reason": row.get("preflight_reason"),
                        }
                        for index, row in enumerate(calls[:3]) if isinstance(row, dict)
                    ],
                },
                "manual_review": "pending",
            })
            if stage_capture is not None:
                case["synthetic_stage_trace"] = stage_capture.stages
                case["synthetic_stage_trace_scope"] = "local_synthetic_fixture_only_not_runtime_result"
                finalizer_stage = next((item for item in stage_capture.stages
                                        if item.get("stage") == "main_agent_finalizer"), {})
                recorded_finalizer_user_hash = (
                    (finalizer.get("finalizer") or {}).get("user_message_sha256")
                )
                case["stage_trace_checks"] = {
                    "call_count_matches_ledger": len(stage_capture.stages) == len(calls),
                    "finalizer_user_message_hash_matches_runtime": (
                        bool(recorded_finalizer_user_hash)
                        and len(finalizer_stage.get("message_content_sha256") or []) == 2
                        and finalizer_stage["message_content_sha256"][1]
                        == recorded_finalizer_user_hash
                    ),
                    "release_pin_matches_previous_a_baseline": (
                        release_sha256 == PREVIOUS_A_RELEASE_SHA256
                    ),
                    "input_hash_matches_previous_a_baseline": (
                        case["input_sha256"] == PREVIOUS_A_INPUT_SHA256
                    ),
                }
        except Exception as exc:
            # Never retry this case. Store only the exception class, not an
            # error string which could contain request data or provider details.
            case["step_usage"] = _project_call_ledger(ledger)
            case["case_status"] = "failed"
            case["provider_dispatch_state"] = (
                "provider_call_observed" if case["step_usage"].get("call_count")
                else "unknown_or_no_call_observed"
            )
            case["probe_error_type"] = type(exc).__name__
            case["manual_review"] = "blocked_by_probe_error"
            if stage_capture is not None:
                case["synthetic_stage_trace"] = stage_capture.stages
                case["synthetic_stage_trace_scope"] = "local_synthetic_fixture_only_not_runtime_result"
        base_payload["provider_requests_observed"] = any(
            isinstance(item.get("step_usage"), dict)
            and (item["step_usage"].get("call_count") or 0) > 0
            for item in base_payload["cases"]
        )
        base_payload["request_sent"] = base_payload["provider_requests_observed"]
        base_payload["provider_api_http_calls"] = base_payload["provider_requests_observed"]
        base_payload.pop("current_case_id", None)
        base_payload["completed_case_count"] = len(base_payload["cases"])
        _write_evidence(output, base_payload)

    base_payload["probe_status"] = "complete"
    base_payload["all_case_usage_observed"] = all(
        isinstance(case.get("step_usage"), dict)
        and case["step_usage"].get("status") == "observed"
        for case in base_payload["cases"]
    )
    if args.capture_synthetic_stage_trace:
        base_payload["all_stage_trace_checks_passed"] = all(
            isinstance(case.get("stage_trace_checks"), dict)
            and case["stage_trace_checks"].get("call_count_matches_ledger") is True
            and case["stage_trace_checks"].get(
                "finalizer_user_message_hash_matches_runtime",
            ) is True
            and case["stage_trace_checks"].get(
                "release_pin_matches_previous_a_baseline",
            ) is True
            and case["stage_trace_checks"].get(
                "input_hash_matches_previous_a_baseline",
            ) is True
            for case in base_payload["cases"]
        )
    else:
        base_payload["all_stage_trace_checks_passed"] = None
    base_payload["final_manual_review"] = "pending"
    _write_evidence(output, base_payload)
    print(json.dumps({
        "probe": base_payload["probe"],
        "request_sent": True,
        "application_http_or_database": False,
        "provider_api_http_calls": base_payload["provider_api_http_calls"],
        "capability_id": CAPABILITY_ID,
        "provider": EXPECTED_PROVIDER,
        "model": EXPECTED_MODEL,
        "release_sha256": release_sha256,
        "all_stage_trace_checks_passed": base_payload["all_stage_trace_checks_passed"],
        "case_summaries": [{
            "case_id": case.get("case_id"),
            "analysis_status": (case.get("analysis") or {}).get("status"),
            "analysis_error": (case.get("analysis") or {}).get("error_code"),
            "decision_status": (case.get("finalizer") or {}).get("decision_status"),
            "gate_status": (case.get("finalizer") or {}).get("gate_status"),
            "usage_status": (case.get("step_usage") or {}).get("status"),
            "call_count": (case.get("step_usage") or {}).get("call_count"),
        } for case in base_payload["cases"]],
        "evidence_path": str(output),
        "raw_provider_json_saved": bool(args.capture_synthetic_stage_trace),
        "raw_provider_output_scope": (
            "synthetic_case_A_only" if args.capture_synthetic_stage_trace else "none"
        ),
        "manual_review": "pending",
    }, ensure_ascii=False, sort_keys=True))
    return 0 if (base_payload["all_case_usage_observed"]
                 and base_payload["all_stage_trace_checks_passed"] is not False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
