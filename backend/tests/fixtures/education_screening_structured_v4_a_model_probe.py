"""Check-only / one-shot synthetic A probe for v4 education screening.

The default path makes no provider request and does not touch application
HTTP, MySQL, Redis, Celery or D1. --live-model sends one synthetic A step to
the pinned Qwen route with SDK retries and failover disabled. Raw stage
outputs are written only to a new D:\Temp evidence file for this synthetic
input; they are never included in durable run events or status.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CAPABILITY_ID = "education-screening-structured-v4"
EXPERT_ID = "career-coach"
INTERACTION_MODE = "experts"
EXPECTED_PROVIDER = "qwen"
EXPECTED_MODEL = "qwen-plus"
EXPECTED_RELEASE_SHA256 = "b3e03e5fa11bcb3e30ae4e31fed4f02e360b5d757f43423077726142fbff138d"
EXPECTED_A_INPUT_SHA256 = "1caa229cb3e20541dd3b781a4fd43a75a791c84f5e0d9861c7252321497a12a1"
MAX_PROVIDER_CALLS = 3
TURN_TOKEN_CAP = 30000

A_INPUT = (
    "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
    "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
    "合成候选人自述：普通本科、无硕士、有推荐系统实习。"
    "请判断学历材料是否足以显示满足岗位门槛。"
)

EXPECTED_PROMPT_SCHEMA_HASHES = {
    "extractor_prompt_sha256": "0f53fd289696cc7a1ba59b7e5def402e1058ebc9f6b8a9a707b96f01003c989d",
    "extractor_schema_sha256": "979aed12fd670222d5b10197c68eb64abc14ed3a751892dfadd0958c328464db",
    "finalizer_instruction_sha256": "4e427037dcfd99ed628cc38ee0dea6eedc33881b0f3ead038af21a26e8c4095a",
    "finalizer_schema_sha256": "f6c2d7cf619d71d68035a7020133cb8bd0bad623f265503ca0c465356f55c5c0",
    "gate_rubric_sha256": "43caf4441185cdb3c6c9992a6203d892dd3fbba75e83ca2a58d1d4c4167f7937",
    "gate_schema_sha256": "20cc2851ce3186f2d0325ac48b15fee87f4d4384fe14603bd5bbfb85cb910ab0",
}
EXPECTED_ASSET_HASHES = {
    "education-screening-context": "247170753ebef6069c92ea80ff69c48922f8ba06e79f2e355e43075d9e3a6a44",
    "school-context": "3a5bd19a9611230db78c4549d8ea4ef5f13ff5b9b6366ea549fe2c578f657afb",
}
EXPECTED_REFERENCE_HASHES = {
    "evidence-scoping": "cf2d70bad92faa4a5f70bd3ad06cb65fc5d82ae6994e7e3e3769271f8976022e",
    "terms": "d067db677bee47c0aa834caae67c691608e6f69eebea6b432c0730d505e86eb3",
}
EXPECTED_PROJECTION_SOURCE_SHA256 = "8f8728485b10c0522081ae95139f6cd19322db0955febbb237f6f1fa30ee4c1c"
EXPECTED_DEPENDENCY_HASHES = {
    "education_screening_v1": "eb8e56c1047ac68d9de664ea4ba980c8fd437e742d4e8ad8c5b688d8dc7ad62f",
    "education_screening_structured_v2": "d78a3fb0d9a45367edf373c194bd0b73ab916e9c63fe0b7dfeb2354914541c3f",
    "education_screening_structured_v3": "cb742019a095ef41c9d2a3df8f76b52067569d6edb0e41599b931fbf992a1da7",
    "quality_gate": "ea67a719801630f95d7a09e55077d8676ccc184c1e2e176f0f3e8a28507af292",
    "reply_egress": "74d330853c2ba9afe793eb32b66eb9aea8691aa6962712bab68350da0c4dfe72",
    "skill_registry": "0118c2fb025d774c1ff141ea71d65ed8c9e066b1de38c3f8d3173dd8d9aa8640",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _load_runtime():
    from app.services.agent_runtime import education_screening_structured_v4 as v4
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    capability = resolve_explicit_capability({
        "interaction_mode": INTERACTION_MODE, "expert_id": EXPERT_ID,
        "capability_id": CAPABILITY_ID,
    })
    if capability is None:
        raise RuntimeError("capability_not_registered")
    release = v4.education_screening_structured_release_contract(INTERACTION_MODE)
    assets = v4.load_v4_assets()
    return v4, capability, release, assets


def _release_checks(v4, capability, release, assets) -> dict[str, Any]:
    from app.services.agent_runtime.quality_gate import GroundingReview

    qa_prompt = v4._skill_guided_prompt(assets)
    actual_prompt_schema = {
        "extractor_prompt_sha256": v4._sha256_text(qa_prompt),
        "extractor_schema_sha256": v4._canonical_sha256(
            v4.EducationScreeningEnvelopeV4.model_json_schema(),
        ),
        "finalizer_instruction_sha256": v4._sha256_text(v4._FINALIZER_INSTRUCTION),
        "finalizer_schema_sha256": v4._canonical_sha256(v4.FinalizerDraftV4.model_json_schema()),
        "gate_rubric_sha256": v4._sha256_text(v4._GROUNDING_RUBRIC),
        "gate_schema_sha256": v4._canonical_sha256(GroundingReview.model_json_schema()),
    }
    asset_pins = v4.v3._asset_pins(assets)
    expected_asset_ids = {"education-screening-context", "school-context"}
    skill_hashes = {key: (asset_pins.get(key) or {}).get("sha256") for key in expected_asset_ids}
    reference_hashes = {
        row.get("reference_id"): row.get("sha256")
        for row in (asset_pins.get("education-screening-context") or {}).get("references", [])
        if isinstance(row, Mapping)
    }
    route_models = [
        (release.get(key) or {}).get("route", {}).get("model")
        for key in ("extractor", "finalizer", "grounding_gate")
    ]
    projection = release.get("projection_implementation") or {}
    dependencies = projection.get("dependency_source_sha256") or {}
    schema_contracts_ok = (
        release.get("extractor", {}).get("response_format") == "json_object"
        and release.get("finalizer", {}).get("response_format") == "json_object"
        and release.get("finalizer", {}).get("message_roles") == ["system", "user"]
        and release.get("grounding_gate", {}).get("response_format_kind") == "json_object"
        and release.get("grounding_gate", {}).get("enabled") is True
        and release.get("grounding_gate", {}).get("max_checks") == 1
    )
    from app.utils.llm_client import LLMClient
    sdk_retries_source_zero = bool(re.search(
        r"max_retries\s*=\s*0\b", inspect.getsource(LLMClient._get_openai),
    ))
    input_sha = _sha256(A_INPUT.encode("utf-8"))
    release_sha = _canonical_sha256(release)
    checks = {
        "exact_capability_and_read_only_one_step": (
            capability.capability_id == CAPABILITY_ID
            and capability.expert_id == EXPERT_ID
            and capability.version == "0.1.0"
            and capability.execution_pipeline == v4.PIPELINE_ID
            and capability.role == "qa" and capability.manifest().get("read_only") is True
            and capability.max_steps == 1
            and set(capability.skill_ids) == expected_asset_ids
        ),
        "release_hash_matches_fixed_v4_contract": release_sha == EXPECTED_RELEASE_SHA256,
        "input_hash_matches_fixed_synthetic_a": input_sha == EXPECTED_A_INPUT_SHA256,
        "route_is_qwen_plus_only": route_models == [EXPECTED_MODEL] * 3,
        "prompt_schema_hashes_match_fixed_contract": actual_prompt_schema == EXPECTED_PROMPT_SCHEMA_HASHES,
        "skill_and_reference_bytes_match_pins": (
            skill_hashes == EXPECTED_ASSET_HASHES
            and reference_hashes == EXPECTED_REFERENCE_HASHES
            and set(reference_hashes) == {"terms", "evidence-scoping"}
            and asset_pins == release.get("skill_assets")
        ),
        "projection_and_direct_dependency_sources_match": (
            projection.get("version") == v4.PROJECTION_IMPLEMENTATION_VERSION
            and projection.get("source_sha256") == EXPECTED_PROJECTION_SOURCE_SHA256
            and dependencies == EXPECTED_DEPENDENCY_HASHES
            and _valid_sha(projection.get("source_sha256"))
            and all(_valid_sha(value) for value in dependencies.values())
        ),
        "json_object_and_single_gate_contract": schema_contracts_ok,
        "sdk_retry_source_is_zero": sdk_retries_source_zero,
    }
    return {
        "passed": all(checks.values()), "checks": checks,
        "release_sha256": release_sha, "input_sha256": input_sha,
        "route_models": route_models, "prompt_schema_hashes": actual_prompt_schema,
        "skill_hashes": skill_hashes, "reference_hashes": reference_hashes,
        "projection_implementation": projection,
        "skill_guided_qa_prompt_sha256": v4._sha256_text(qa_prompt),
        "release_contract": release,
    }


def _provider_preflight() -> dict[str, bool]:
    from config.integration_loader import get_provider_config

    config = get_provider_config(EXPECTED_PROVIDER)
    return {
        "qwen_api_key_available": bool(config.get("api_key")),
        "qwen_base_url_available": bool(config.get("base_url")),
    }


def _default_output_path() -> Path:
    root = Path(r"D:\Temp")
    if not root.is_dir():
        raise RuntimeError("D:\\Temp is unavailable; no evidence file written")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"education-screening-v4-a-{stamp}-{uuid4().hex[:8]}.json"


def _output_path_ok(path: Path) -> bool:
    try:
        return (path.drive.upper() == "D:" and path.parent.resolve() == Path(r"D:\Temp").resolve()
                and not path.exists())
    except Exception:
        return False


def _safe_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    calls = []
    for index, row in enumerate((ledger.get("calls") or [])[:MAX_PROVIDER_CALLS]):
        if not isinstance(row, Mapping):
            continue
        preflight = row.get("preflight") if isinstance(row.get("preflight"), Mapping) else {}
        usage = row.get("usage") if isinstance(row.get("usage"), Mapping) else None
        calls.append({
            "index": index + 1, "provider": row.get("provider"), "model": row.get("model"),
            "usage": dict(usage) if usage else None,
            "finish_reason": row.get("finish_reason"),
            "preflight_fits": preflight.get("fits"),
            "preflight_reason": preflight.get("reason"),
        })
    return {
        "status": "observed" if calls and all(row.get("usage") for row in calls)
        else "unverified" if calls else "none",
        "call_count": len(calls), "calls": calls,
        "turn_token_cap": ledger.get("turn_token_cap"),
        "stop_reason": ledger.get("stop_reason"),
        "reported_total_tokens": sum(
            (row.get("usage") or {}).get("total_tokens", 0) for row in calls
        ),
    }


class _OneShotCapture:
    """Capture responses only for the fixed synthetic A input."""

    STAGES = ("structured_education_qa", "main_agent_finalizer", "grounding_gate")

    def __init__(self, client):
        self.client = client
        self.stages: list[dict[str, Any]] = []

    def chat_with_metadata(self, **kwargs):
        if len(self.stages) >= MAX_PROVIDER_CALLS:
            raise RuntimeError("probe_call_cap_exceeded")
        messages = kwargs.get("messages")
        route_ok = kwargs.get("model_override") == EXPECTED_MODEL
        format_ok = kwargs.get("response_format") == {"type": "json_object"}
        stage = {
            "stage": self.STAGES[len(self.stages)],
            "route_model_matches": route_ok, "json_object_matches": format_ok,
            "message_roles": [item.get("role") for item in messages if isinstance(item, Mapping)]
            if isinstance(messages, list) else [],
            "request_messages_sha256": _canonical_sha256(messages),
            "message_content_sha256": [
                _sha256(str(item.get("content") or "").encode("utf-8"))
                for item in messages if isinstance(item, Mapping)
            ] if isinstance(messages, list) else [],
        }
        self.stages.append(stage)
        if not route_ok or not format_ok:
            stage["error_code"] = "pinned_request_contract_mismatch"
            raise RuntimeError("pinned_request_contract_mismatch")
        try:
            response = self.client.chat_with_metadata(**kwargs)
        except Exception as exc:
            stage["error_code"] = type(exc).__name__[:80]
            raise
        stage.update({
            "provider": getattr(response, "provider", None),
            "model": getattr(response, "model", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "usage": getattr(response, "usage", None),
            "response_chars": len(response.text) if isinstance(
                getattr(response, "text", None), str,
            ) else None,
            "raw_response_text": getattr(response, "text", None),
        })
        return response


def _source_bound_rows(analysis: Mapping[str, Any], input_sha: str) -> dict[str, Any]:
    manifest = {row.get("source_id"): row for row in analysis.get("source_manifest", [])
                if isinstance(row, Mapping)}
    user_source = manifest.get("user_message") or {}

    def rows_bound(rows, required_value=False):
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                result.append(False)
                continue
            quote = row.get("source_quote")
            value = row.get("value_quote")
            result.append(
                row.get("source_id") == "user_message"
                and row.get("source_kind") == "user_provided_unverified"
                and row.get("source_sha256") == input_sha
                and isinstance(quote, str) and quote in A_INPUT
                and row.get("quote_integrity") == "exact_substring_only"
                and row.get("interpretation_status") == "model_candidate_unverified"
                and (not required_value or isinstance(value, str) and value and value in quote)
            )
        return result

    focus = analysis.get("question_focus")
    focus_bound = bool(
        isinstance(focus, Mapping)
        and focus.get("source_id") == "user_message"
        and focus.get("source_sha256") == input_sha
        and focus.get("quote_integrity") == "exact_substring_only"
        and focus.get("interpretation_status") == "model_candidate_unverified"
        and isinstance(focus.get("source_quote"), str)
        and focus["source_quote"] in A_INPUT
    )
    return {
        "manifest_user_input_hash_matches": user_source.get("source_sha256") == input_sha,
        "job_rows_source_bound": rows_bound(analysis.get("job_conditions"), required_value=True),
        "candidate_rows_source_bound": rows_bound(analysis.get("candidate_statements"), required_value=True),
        "question_focus_source_bound": focus_bound,
    }


def _parse_json_response(stage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    raw = stage.get("raw_response_text") if isinstance(stage, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _run_live(check: Mapping[str, Any], output: Path) -> dict[str, Any]:
    from config.integration_loader import get_provider_config
    from app.services.agent_runtime.budget import capture_usage_ledger, current_usage_ledger
    from app.services.agent_runtime.education_screening_structured_v4 import run_structured_education_step
    from app.utils.llm_client import LLMClient

    config = get_provider_config(EXPECTED_PROVIDER)
    if not all(config.get(key) for key in ("api_key", "base_url")):
        raise RuntimeError("qwen_config_missing")
    client = LLMClient(
        provider=EXPECTED_PROVIDER, api_key=config["api_key"], base_url=config["base_url"],
        model=EXPECTED_MODEL, timeout=config.get("timeout"), failover=[EXPECTED_PROVIDER],
    )
    if client.failover_order != [EXPECTED_PROVIDER]:
        raise RuntimeError("qwen_only_client_unavailable")
    capture = _OneShotCapture(client)
    from app.services.agent_runtime.education_screening_structured_v4 import (
        education_screening_structured_release_contract,
    )
    release = education_screening_structured_release_contract(INTERACTION_MODE)

    run_error = None
    result = {}
    with capture_usage_ledger(
        "education_screening_structured_v4_a_probe", turn_token_cap=TURN_TOKEN_CAP,
    ) as ledger:
        if current_usage_ledger() is not ledger or ledger.get("calls"):
            raise RuntimeError("same_step_usage_ledger_preflight_failed")
        try:
            result = run_structured_education_step(
                user_message=A_INPUT, owner_id="synthetic-owner-v4-a",
                run_id=f"synthetic-run-v4-a-{uuid4()}", expected_runtime_contract=release,
                verified_school_facts=(), receipts=(), llm=capture,
            )
        except Exception as exc:
            run_error = type(exc).__name__[:80]

    safe_ledger = _safe_ledger(ledger)
    usage_matches = []
    for index, stage in enumerate(capture.stages):
        call = safe_ledger["calls"][index] if index < len(safe_ledger["calls"]) else {}
        usage_matches.append({
            "stage": stage.get("stage"),
            "provider_matches": stage.get("provider") == EXPECTED_PROVIDER == call.get("provider"),
            "model_matches": stage.get("model") == EXPECTED_MODEL == call.get("model"),
            "usage_matches": stage.get("usage") == call.get("usage") and isinstance(stage.get("usage"), Mapping),
            "finish_reason_matches": stage.get("finish_reason") == call.get("finish_reason"),
        })
    actual_client = client._clients.get(EXPECTED_PROVIDER)
    sdk_retries = getattr(actual_client[0], "max_retries", None) if actual_client else None
    analysis = result.get("analysis") if isinstance(result.get("analysis"), Mapping) else {}
    projection = result.get("projection") if isinstance(result.get("projection"), Mapping) else {}
    finalizer = result.get("finalizer") if isinstance(result.get("finalizer"), Mapping) else {}
    qa_json = _parse_json_response(capture.stages[0] if capture.stages else None)
    main_json = _parse_json_response(capture.stages[1] if len(capture.stages) > 1 else None)
    gate_json = _parse_json_response(capture.stages[2] if len(capture.stages) > 2 else None)
    conclusions = projection.get("allowed_conclusions") or []
    source_checks = _source_bound_rows(analysis, EXPECTED_A_INPUT_SHA256)
    result_checks = {
        "qa_status_model_candidate": analysis.get("status") == "model_candidate_unverified",
        "qa_has_source_bound_job_row": bool(source_checks["job_rows_source_bound"]),
        "qa_has_source_bound_candidate_row": bool(source_checks["candidate_rows_source_bound"]),
        "qa_focus_source_bound": source_checks["question_focus_source_bound"],
        "projection_only_conditional_not_demonstrated": (
            [row.get("status") for row in conclusions if isinstance(row, Mapping)]
            == ["conditional_not_demonstrated"]
        ),
        "main_reply_claim_refs_allowed": bool(
            isinstance(main_json, Mapping)
            and all(ref in {row.get("id") for row in conclusions if isinstance(row, Mapping)}
                    for ref in main_json.get("claim_refs", []))
        ),
        "gate_supported": (
            isinstance(gate_json, Mapping) and gate_json.get("verdict") == "supported"
            and finalizer.get("decision_status") == "main_agent_natural_reviewed"
        ),
        "ledger_usage_observed_and_consistent": (
            safe_ledger.get("status") == "observed"
            and safe_ledger.get("call_count") == len(capture.stages)
            and bool(usage_matches) and all(
                all(item[key] for key in (
                    "provider_matches", "model_matches", "usage_matches", "finish_reason_matches",
                )) for item in usage_matches
            )
        ),
        "sdk_runtime_retry_count_zero": sdk_retries == 0,
        "provider_only_qwen": client.failover_order == [EXPECTED_PROVIDER]
            and all(stage.get("provider") == EXPECTED_PROVIDER for stage in capture.stages),
    }
    evidence = {
        "probe": "education_screening_structured_v4_a",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "input_sha256": EXPECTED_A_INPUT_SHA256,
        "input_text_synthetic_only": A_INPUT,
        "capability_id": CAPABILITY_ID,
        "release_sha256": check["release_sha256"],
        "release_contract": check["release_contract"],
        "application_http": False, "mysql": False, "redis": False, "celery": False, "d1": False,
        "provider_runtime": {
            "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL,
            "failover_order": client.failover_order,
            "sdk_max_retries_observed": sdk_retries,
            "provider_call_count": len(capture.stages),
        },
        "stage_trace_synthetic_only": capture.stages,
        "usage_ledger": safe_ledger,
        "usage_consistency": usage_matches,
        "preflight_passed": check.get("passed"),
        "preflight_checklist": check.get("checks"),
        "run_error_type": run_error,
        "qa_raw_json_parsed": qa_json,
        "qa_analysis": analysis,
        "source_binding_checks": source_checks,
        "projection": projection,
        "main_finalizer": {
            "status": finalizer.get("decision_status"),
            "reason": finalizer.get("decision_reason"),
            "gate_status": finalizer.get("gate_status"),
            "gate_observation": finalizer.get("finalizer", {}).get("grounding_gate")
                if isinstance(finalizer.get("finalizer"), Mapping) else None,
            "draft_json": main_json,
            "gate_json": gate_json,
            "raw_draft_and_gate_are_only_in_synthetic_stage_trace": True,
        },
        "public_reply": result.get("reply"),
        "result_checks": result_checks,
        "manual_review": "pending",
        "non_claim": "The gate reviews consistency with supplied projection; it does not verify a job notice or candidate facts.",
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "request_sent": bool(capture.stages), "stage_count": len(capture.stages),
        "analysis_status": analysis.get("status"),
        "job_conditions_count": len(analysis.get("job_conditions") or []),
        "candidate_statements_count": len(analysis.get("candidate_statements") or []),
        "question_focus": (analysis.get("question_focus") or {}).get("source_quote")
            if isinstance(analysis.get("question_focus"), Mapping) else None,
        "allowed_conclusion_statuses": [
            row.get("status") for row in conclusions if isinstance(row, Mapping)
        ],
        "finalizer_status": finalizer.get("decision_status"),
        "gate_status": finalizer.get("gate_status"),
        "usage_status": safe_ledger.get("status"), "usage_calls": safe_ledger.get("call_count"),
        "result_checks": result_checks,
        "public_reply": result.get("reply"), "evidence_path": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="send one v4 synthetic A step to Qwen only")
    parser.add_argument("--output", type=Path, help="new evidence file directly under D:\\Temp")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    # Isolate this invocation from optional proxy and trace sinks.
    os.environ["LITELLM_BASE_URL"] = ""
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    os.environ["CPA_LANGFUSE_CAPTURE_CONTENT"] = "false"

    try:
        v4, capability, release, assets = _load_runtime()
        check = _release_checks(v4, capability, release, assets)
    except Exception as exc:
        print(json.dumps({
            "probe": "education_screening_structured_v4_a",
            "request_sent": False, "preflight_error": type(exc).__name__[:80],
        }, ensure_ascii=False))
        return 2

    provider_config = _provider_preflight()
    check["checks"]["qwen_credentials_and_base_url_configured"] = all(provider_config.values())
    check["checks"]["application_http_db_and_d1_not_enabled_by_probe"] = True
    output = args.output or _default_output_path()
    check["checks"]["evidence_path_new_and_directly_under_d_temp"] = _output_path_ok(output)
    check["passed"] = all(check["checks"].values())
    check_only = {
        "probe": "education_screening_structured_v4_a",
        "mode": "one_shot_live" if args.live_model else "check_only",
        "request_sent": False, "application_http": False, "mysql": False,
        "redis": False, "celery": False, "d1": False,
        "checklist": check["checks"],
        "capability_binding": capability.manifest(),
        "release_sha256": check["release_sha256"],
        "input_sha256": check["input_sha256"],
        "route_models": check["route_models"],
        "prompt_schema_hashes": check["prompt_schema_hashes"],
        "skill_hashes": check["skill_hashes"],
        "reference_hashes": check["reference_hashes"],
        "projection_implementation": check["projection_implementation"],
        "retry_policy": {
            "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL,
            "sdk_max_retries": 0, "failover": "disabled",
        },
        "max_provider_calls": MAX_PROVIDER_CALLS,
        "usage_ledger": {"scope": "one run_structured_education_step", "turn_token_cap": TURN_TOKEN_CAP},
        "evidence_path": str(output),
    }
    if not args.live_model:
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 0 if check["passed"] else 2
    if not check["passed"]:
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 2
    if not _output_path_ok(output):
        print(json.dumps({**check_only, "reason": "evidence_path_not_new_d_temp_file"}, ensure_ascii=False))
        return 2

    try:
        summary = _run_live(check, output)
    except Exception as exc:
        # The call may already have been sent; never claim zero calls or retry.
        print(json.dumps({
            **check_only, "request_sent": "unknown",
            "request_may_have_been_sent": True,
            "no_retry": True, "live_error_type": type(exc).__name__[:80],
        }, ensure_ascii=False))
        return 2
    print(json.dumps({**check_only, **summary}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
