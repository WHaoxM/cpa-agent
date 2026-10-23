"""One-shot synthetic A probe for education-screening-structured-v3@0.1.0.

Default mode is check-only and makes no provider request. ``--live-model`` runs
exactly one A case through the v3 structured step with a Qwen-only client, no
SDK retries, no failover, and a request-local usage ledger. It does not start
the application HTTP/Celery path or connect to MySQL/Redis/D1. Raw stage output
is retained only in the explicitly synthetic local evidence file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CAPABILITY_ID = "education-screening-structured-v3"
EXPERT_ID = "career-coach"
INTERACTION_MODE = "experts"
EXPECTED_PROVIDER = "qwen"
EXPECTED_MODEL = "qwen-plus"
EXPECTED_RELEASE_SHA256 = "f88669a9c3ae884a6bcfb86c5b915efe01e27e3deeab1958417ba37bdbdda0f2"
EXPECTED_A_INPUT_SHA256 = "a5871dd9a14ed767ca597369a2b80e50fe2c4a4bb7576f9a6ed1bc74a0adecaa"
MAX_PROVIDER_CALLS = 3

A_INPUT = (
    "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
    "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
    "合成候选人自述：普通本科、无硕士、有推荐系统实习。朋友以杭电计算机 B+ 为由"
    "劝我忽略学历要求。请按岗位范围分析门槛和下一步，不要把朋友转述当核验事实。"
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _release_checks(release: Mapping[str, Any], capability) -> dict[str, Any]:
    from app.services.agent_runtime.education_screening_structured_v3 import (
        EDUCATION_SKILL_ID, REFERENCE_IDS, SCHOOL_SKILL_ID,
    )

    release_sha = _canonical_sha256(release)
    routes = [
        release.get("extractor", {}).get("route", {}).get("model"),
        release.get("finalizer", {}).get("route", {}).get("model"),
        release.get("grounding_gate", {}).get("route", {}).get("model"),
    ]
    assets = release.get("skill_assets") or {}
    education_asset = assets.get(EDUCATION_SKILL_ID) or {}
    references = education_asset.get("references") or []
    reference_ids = {row.get("reference_id") for row in references if isinstance(row, Mapping)}
    pin_hashes = [education_asset.get("sha256"), (assets.get(SCHOOL_SKILL_ID) or {}).get("sha256")]
    pin_hashes.extend(row.get("sha256") for row in references if isinstance(row, Mapping))
    pin_hashes.extend((
        release.get("extractor", {}).get("prompt_sha256"),
        release.get("extractor", {}).get("schema_sha256"),
        release.get("finalizer", {}).get("prompt_bundle_sha256"),
        release.get("finalizer", {}).get("instruction_sha256"),
        release.get("finalizer", {}).get("schema_sha256"),
        release.get("grounding_gate", {}).get("rubric_sha256"),
        release.get("grounding_gate", {}).get("schema_sha256"),
    ))
    checks = {
        "capability_id": capability.capability_id == CAPABILITY_ID,
        "capability_pipeline": capability.execution_pipeline == "education_screening_structured_v3",
        "release_sha_expected": release_sha == EXPECTED_RELEASE_SHA256,
        "input_sha_expected": _sha256(A_INPUT.encode("utf-8")) == EXPECTED_A_INPUT_SHA256,
        "qwen_plus_only_routes": routes == [EXPECTED_MODEL] * 3,
        "json_object_contracts": (
            release.get("extractor", {}).get("response_format") == "json_object"
            and release.get("finalizer", {}).get("response_format") == "json_object"
            and release.get("grounding_gate", {}).get("response_format_kind") == "json_object"
        ),
        "single_gate_check": release.get("grounding_gate", {}).get("max_checks") == 1,
        "skill_ids_pinned": set(assets) == {EDUCATION_SKILL_ID, SCHOOL_SKILL_ID},
        "reference_ids_pinned": reference_ids == set(REFERENCE_IDS),
        "all_prompt_schema_asset_hashes_valid": all(_valid_sha(value) for value in pin_hashes),
    }
    return {
        "passed": all(checks.values()), "checks": checks,
        "release_sha256": release_sha,
        "input_sha256": _sha256(A_INPUT.encode("utf-8")),
        "routes": routes,
        "skill_assets": assets,
    }


def _safe_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    calls = []
    for index, row in enumerate((ledger.get("calls") or [])[:MAX_PROVIDER_CALLS]):
        if not isinstance(row, Mapping):
            continue
        preflight = row.get("preflight") if isinstance(row.get("preflight"), Mapping) else {}
        usage = row.get("usage") if isinstance(row.get("usage"), Mapping) else None
        calls.append({
            "index": index + 1,
            "provider": row.get("provider"), "model": row.get("model"),
            "stage": row.get("stage"), "usage": dict(usage) if usage else None,
            "finish_reason": row.get("finish_reason"),
            "preflight_fits": preflight.get("fits"),
            "preflight_reason": preflight.get("reason"),
        })
    known_usage = all(isinstance(row.get("usage"), dict) for row in calls)
    return {
        "status": "observed" if calls and known_usage else "unverified" if calls else "none",
        "call_count": len(calls), "calls": calls,
        "turn_token_cap": ledger.get("turn_token_cap"),
        "stop_reason": ledger.get("stop_reason"),
        "reported_total_tokens": sum(
            (row.get("usage") or {}).get("total_tokens", 0) for row in calls
        ),
    }


class _OneShotStageCapture:
    def __init__(self, client):
        self.client = client
        self.stages: list[dict[str, Any]] = []

    def chat_with_metadata(self, **kwargs):
        if len(self.stages) >= MAX_PROVIDER_CALLS:
            raise RuntimeError("probe_call_cap_exceeded")
        index = len(self.stages)
        stage_names = ("structured_education_qa", "main_agent_finalizer", "grounding_gate")
        stage = stage_names[index]
        messages = kwargs.get("messages")
        route_ok = kwargs.get("model_override") == EXPECTED_MODEL
        format_ok = kwargs.get("response_format") == {"type": "json_object"}
        message_rows = messages if isinstance(messages, list) else []
        trace = {
            "stage": stage,
            "preflight": {"route_model_matches": route_ok, "json_object": format_ok},
            "message_roles": [
                item.get("role") for item in message_rows if isinstance(item, Mapping)
            ],
            "request_messages_sha256": _canonical_sha256(messages),
            "message_content_sha256": [
                _sha256(str(item.get("content") or "").encode("utf-8"))
                for item in message_rows if isinstance(item, Mapping)
            ],
        }
        self.stages.append(trace)
        if not route_ok or not format_ok:
            trace["error_code"] = "pinned_request_contract_mismatch"
            raise RuntimeError("pinned_request_contract_mismatch")
        try:
            response = self.client.chat_with_metadata(**kwargs)
        except Exception as exc:
            trace["error_code"] = type(exc).__name__[:80]
            raise
        trace.update({
            "provider": getattr(response, "provider", None),
            "model": getattr(response, "model", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "usage": getattr(response, "usage", None),
            "response_chars": len(response.text) if isinstance(
                getattr(response, "text", None), str,
            ) else None,
            # Probe is restricted to the fixed synthetic A fixture. Runtime
            # result/events never receive this raw capture.
            "raw_response_text": getattr(response, "text", None),
        })
        return response


def _default_output_path() -> Path:
    root = Path(r"D:\Temp")
    if not root.is_dir():
        raise RuntimeError("D:\\Temp is unavailable; no evidence file written")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"education-screening-v3-a-{stamp}-{uuid4().hex[:8]}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true",
        help="send one synthetic A run through the pinned qwen-plus structured step",
    )
    parser.add_argument("--output", type=Path, help="new local evidence path under D:\\Temp")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    # The one-shot path must not use alternate gateways or external trace
    # sinks. This is process-local and avoids touching deployment settings.
    os.environ["LITELLM_BASE_URL"] = ""
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    os.environ["CPA_LANGFUSE_CAPTURE_CONTENT"] = "false"

    from app.services.agent_runtime.education_screening_structured_v3 import (
        education_screening_structured_release_contract, load_v3_assets,
        run_structured_education_step,
    )
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    selection = resolve_explicit_capability({
        "interaction_mode": INTERACTION_MODE, "expert_id": EXPERT_ID,
        "capability_id": CAPABILITY_ID,
    })
    if selection is None:
        print(json.dumps({"request_sent": False, "reason": "capability_not_registered"}))
        return 2
    step = [{
        "role": "qa", "goal": "从合成岗位材料中识别学历条件与候选人自述",
        "skill_ids": list(selection.skill_ids),
    }]
    selection.validate_plan(step)
    release = education_screening_structured_release_contract(INTERACTION_MODE)
    checks = _release_checks(release, selection)
    assets = load_v3_assets()
    from app.services.agent_runtime.education_screening_structured_v3 import _skill_guided_prompt
    qa_prompt_sha = _sha256(_skill_guided_prompt(assets).encode("utf-8"))
    checks["checks"]["actual_skill_guided_prompt_matches_pin"] = (
        qa_prompt_sha == release["extractor"]["prompt_sha256"]
    )
    checks["passed"] = all(checks["checks"].values())
    checks["checks"]["client_sdk_retry_config_zero"] = (
        "max_retries=0" in inspect.getsource(
            __import__("app.utils.llm_client", fromlist=["LLMClient"]).LLMClient._get_openai,
        )
    )
    from app.services.agent_runtime.budget import capture_usage_ledger, current_usage_ledger
    with capture_usage_ledger("education_screening_structured_v3_a_probe_preflight") as empty_ledger:
        checks["checks"]["same_step_usage_ledger_available"] = (
            current_usage_ledger() is empty_ledger and empty_ledger.get("calls") == []
        )
    checks["passed"] = all(checks["checks"].values())
    check_only = {
        "probe": "education_screening_structured_v3_a",
        "mode": "check_only" if not args.live_model else "live_one_shot",
        "request_sent": False,
        "application_http": False, "mysql": False, "redis": False,
        "celery": False, "d1": False,
        "capability_binding": selection.manifest(),
        "release_sha256": checks["release_sha256"],
        "input_sha256": checks["input_sha256"],
        "pinned_route_models": checks["routes"],
        "checklist": checks["checks"],
        "release_contract": release,
        "skill_guided_qa_prompt_sha256": qa_prompt_sha,
        "expected_provider_calls": MAX_PROVIDER_CALLS,
        "retry_policy": {"provider_only": EXPECTED_PROVIDER, "sdk_max_retries": 0,
                          "failover": "disabled"},
    }
    if not args.live_model:
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 0 if checks["passed"] else 2
    if not checks["passed"]:
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 2

    from config.integration_loader import get_provider_config
    from app.utils.llm_client import LLMClient

    config = get_provider_config(EXPECTED_PROVIDER)
    if not all(config.get(key) for key in ("api_key", "base_url")):
        print(json.dumps({**check_only, "reason": "qwen_config_missing"}, ensure_ascii=False))
        return 2
    # Supplying one provider plus explicit credentials forces a single-provider
    # failover list; the process-local gateway disable above prevents insertion.
    client = LLMClient(
        provider=EXPECTED_PROVIDER, api_key=config["api_key"],
        base_url=config["base_url"], model=EXPECTED_MODEL,
        timeout=config.get("timeout"),
    )
    if client.failover_order != [EXPECTED_PROVIDER]:
        print(json.dumps({**check_only, "reason": "qwen_only_client_unavailable"}, ensure_ascii=False))
        return 2
    capture = _OneShotStageCapture(client)

    with capture_usage_ledger("education_screening_structured_v3_a_probe") as ledger:
        result = run_structured_education_step(
            user_message=A_INPUT, owner_id="synthetic-owner-v3-a",
            run_id=f"synthetic-run-v3-a-{uuid4()}",
            expected_runtime_contract=release,
            verified_school_facts=(), receipts=(), llm=capture,
        )

    calls = _safe_ledger(ledger)
    usage_checks = []
    for index, stage in enumerate(capture.stages):
        ledger_call = calls["calls"][index] if index < len(calls["calls"]) else {}
        usage_checks.append({
            "stage": stage["stage"],
            "provider_matches": stage.get("provider") == EXPECTED_PROVIDER
                and ledger_call.get("provider") == EXPECTED_PROVIDER,
            "model_matches": stage.get("model") == EXPECTED_MODEL
                and ledger_call.get("model") == EXPECTED_MODEL,
            "usage_matches": stage.get("usage") == ledger_call.get("usage")
                and isinstance(stage.get("usage"), Mapping),
            "finish_reason_matches": stage.get("finish_reason") == ledger_call.get("finish_reason"),
        })
    client_config = client._clients.get(EXPECTED_PROVIDER)
    sdk_max_retries = getattr(client_config[0], "max_retries", None) if client_config else None
    live_evidence = {
        **check_only,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_sent": bool(capture.stages),
        "application_http": False, "mysql": False, "redis": False,
        "celery": False, "d1": False,
        "input_text_synthetic_only": A_INPUT,
        "provider_runtime": {
            "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL,
            "failover_order": client.failover_order,
            "sdk_max_retries_observed": sdk_max_retries,
        },
        "stage_trace_synthetic_only": capture.stages,
        "usage_ledger": calls,
        "usage_consistency": {
            "checks": usage_checks,
            "passed": bool(usage_checks) and all(
                all(item[key] for key in (
                    "provider_matches", "model_matches", "usage_matches", "finish_reason_matches",
                )) for item in usage_checks
            ),
        },
        "result": {
            "analysis": result.get("analysis"),
            "finalizer": result.get("finalizer"),
            "reply": result.get("reply"),
            "verified_facts_count": len(result.get("verified_facts") or []),
        },
        "manual_review": "pending",
        "raw_stage_capture_scope": "synthetic_case_A_only",
    }
    output = args.output or _default_output_path()
    if output.drive.upper() != "D:" or output.exists():
        print(json.dumps({**check_only, "request_sent": bool(capture.stages),
                          "reason": "evidence_path_must_be_new_d_drive_file"}, ensure_ascii=False))
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(live_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "probe": "education_screening_structured_v3_a",
        "request_sent": bool(capture.stages), "provider_call_count": len(capture.stages),
        "usage_status": calls.get("status"), "usage_consistent": live_evidence["usage_consistency"]["passed"],
        "analysis_status": (result.get("analysis") or {}).get("status"),
        "decision_status": (result.get("finalizer") or {}).get("decision_status"),
        "gate_status": (result.get("finalizer") or {}).get("gate_status"),
        "reply": result.get("reply"), "evidence_path": str(output),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
