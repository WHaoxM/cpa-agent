"""Check-only / one-shot synthetic A probe for education screening v5.

Default mode makes no provider request. ``--live-model`` makes one A-only
structured QA + ID-only selector chain through Qwen with SDK retries/failover
disabled, inside one request-local 30k token ledger. It does not use the
application HTTP, MySQL, Redis, Celery or D1 paths. Raw responses are retained
only in a new D:\Temp evidence file for this fixed synthetic input.
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

CAPABILITY_ID = "education-screening-structured-v5"
EXPERT_ID = "career-coach"
INTERACTION_MODE = "experts"
EXPECTED_PROVIDER = "qwen"
EXPECTED_MODEL = "qwen-plus"
TURN_TOKEN_CAP = 30000
MAX_PROVIDER_CALLS = 2
PROBE_MODE = "education_screening_structured_v5_a"

A_INPUT = (
    "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
    "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
    "合成候选人自述：普通本科、无硕士、有推荐系统实习。"
    "请判断学历材料是否足以显示满足岗位门槛。"
)

EXPECTED_A_INPUT_SHA256 = "1caa229cb3e20541dd3b781a4fd43a75a791c84f5e0d9861c7252321497a12a1"
EXPECTED_V5_RELEASE_SHA256 = "e9bb12e2ffceadd74ad2f30e9c29fef272c15d4a841fcdc954e0a7878e372ae1"
EXPECTED_V4_RELEASE_SHA256 = "b3e03e5fa11bcb3e30ae4e31fed4f02e360b5d757f43423077726142fbff138d"
EXPECTED_SKILLS_AND_REFS = {
    "education-screening-context": {
        "version": "0.3.0",
        "sha256": "247170753ebef6069c92ea80ff69c48922f8ba06e79f2e355e43075d9e3a6a44",
        "references": {
            "evidence-scoping": "cf2d70bad92faa4a5f70bd3ad06cb65fc5d82ae6994e7e3e3769271f8976022e",
            "terms": "d067db677bee47c0aa834caae67c691608e6f69eebea6b432c0730d505e86eb3",
        },
    },
    "school-context": {
        "version": "1.0.2",
        "sha256": "3a5bd19a9611230db78c4549d8ea4ef5f13ff5b9b6366ea549fe2c578f657afb",
        "references": {},
    },
}
EXPECTED_V5_SELECTOR_PROMPT_SHA256 = "c73759af6ebf0b8f5a9ce4ac6a067c53c857e355a47c31f97569778d710b5514"
EXPECTED_V5_SELECTOR_SCHEMA_SHA256 = "5fa8a55bcce37850eefbb1b3bbfecdda762d024480e6b9d38af24a009117a9d9"
EXPECTED_V5_RENDERER_SHA256 = "1c86bde47f344362db49328a8250a501e64923b4577cb1c24cae4ee5aaa265c2"
EXPECTED_V5_DIRECT_DEPENDENCIES = {
    "budget": "f6af951c469eabd897761aa32ec5bc5909f7566a2343adbefc88b04d5b51324f",
    "llm_client": "3fe938c20f1bfe875c672a98dd54bff3b182f822718580da2b2f4598a42d4195",
    "model_router": "f888bddbd78da2a49514274ca4fc66d791a2787bd64972b1560243d2b81add2c",
}
EXPECTED_V4_EXTRACTOR_PROMPT_SHA256 = "0f53fd289696cc7a1ba59b7e5def402e1058ebc9f6b8a9a707b96f01003c989d"
EXPECTED_V4_EXTRACTOR_SCHEMA_SHA256 = "979aed12fd670222d5b10197c68eb64abc14ed3a751892dfadd0958c328464db"
EXPECTED_V4_PROJECTION_SHA256 = "8f8728485b10c0522081ae95139f6cd19322db0955febbb237f6f1fa30ee4c1c"
EXPECTED_V4_HELPER_CLOSURE = {
    "education_screening_v1": "eb8e56c1047ac68d9de664ea4ba980c8fd437e742d4e8ad8c5b688d8dc7ad62f",
    "education_screening_structured_v2": "d78a3fb0d9a45367edf373c194bd0b73ab916e9c63fe0b7dfeb2354914541c3f",
    "education_screening_structured_v3": "cb742019a095ef41c9d2a3df8f76b52067569d6edb0e41599b931fbf992a1da7",
    "quality_gate": "ea67a719801630f95d7a09e55077d8676ccc184c1e2e176f0f3e8a28507af292",
    "reply_egress": "74d330853c2ba9afe793eb32b66eb9aea8691aa6962712bab68350da0c4dfe72",
    "skill_registry": "0118c2fb025d774c1ff141ea71d65ed8c9e066b1de38c3f8d3173dd8d9aa8640",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8"))


def _load_runtime():
    from app.services.agent_runtime import education_screening_structured_v4 as v4
    from app.services.agent_runtime import education_screening_structured_v5 as v5
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    capability = resolve_explicit_capability({
        "interaction_mode": INTERACTION_MODE, "expert_id": EXPERT_ID,
        "capability_id": CAPABILITY_ID,
    })
    if capability is None:
        raise RuntimeError("capability_not_registered")
    release = v5.education_screening_structured_v5_release_contract(INTERACTION_MODE)
    assets = v4.load_v4_assets()
    return v4, v5, capability, release, assets


def _actual_skill_hashes(v4, assets: Mapping[str, Any]) -> dict[str, Any]:
    pins = v4.v3._asset_pins(dict(assets))
    result = {}
    for skill_id, expected in EXPECTED_SKILLS_AND_REFS.items():
        pin = pins.get(skill_id) or {}
        references = {
            row.get("reference_id"): row.get("sha256")
            for row in (pin.get("references") or []) if isinstance(row, Mapping)
        }
        result[skill_id] = {
            "version": pin.get("version"), "sha256": pin.get("sha256"),
            "references": references,
        }
    return result


def _runtime_client_preflight() -> dict[str, Any]:
    """Construct the Qwen-only SDK client but do not issue a request."""
    from config.integration_loader import get_provider_config
    from app.utils.llm_client import LLMClient

    config = get_provider_config(EXPECTED_PROVIDER)
    if not config.get("api_key") or not config.get("base_url"):
        return {
            "configured": False, "provider": EXPECTED_PROVIDER,
            "failover_order": [], "sdk_max_retries": None,
        }
    client = LLMClient(
        provider=EXPECTED_PROVIDER, api_key=config["api_key"], base_url=config["base_url"],
        model=EXPECTED_MODEL, timeout=config.get("timeout"),
    )
    if client.failover_order != [EXPECTED_PROVIDER]:
        return {
            "configured": True, "provider": EXPECTED_PROVIDER,
            "failover_order": list(client.failover_order), "sdk_max_retries": None,
        }
    sdk, _ = client._get_openai(EXPECTED_PROVIDER)
    return {
        "configured": True, "provider": EXPECTED_PROVIDER,
        "failover_order": list(client.failover_order),
        "sdk_max_retries": getattr(sdk, "max_retries", None),
    }


def _release_checks(v4, v5, capability, release, assets, output: Path) -> dict[str, Any]:
    from app.services.agent_runtime.model_router import route_model
    from app.utils.llm_client import LLMClient

    v4_release = release.get("extractor_release") or {}
    v5_selector = release.get("selector") or {}
    v5_renderer = release.get("renderer") or {}
    actual_skills = _actual_skill_hashes(v4, assets)
    actual_extractor_prompt_sha = v4._sha256_text(v4._skill_guided_prompt(dict(assets)))
    routes = {
        "v4_extractor": (v4_release.get("extractor") or {}).get("route", {}).get("model"),
        "v5_selector": (v5_selector.get("route") or {}).get("model"),
    }
    all_nested_routes = [
        (v4_release.get(key) or {}).get("route", {}).get("model")
        for key in ("extractor", "finalizer", "grounding_gate")
    ]
    projection = v4_release.get("projection_implementation") or {}
    direct_dependencies = v5_renderer.get("direct_dependency_source_sha256") or {}
    sdk_static_retries_zero = "max_retries=0" in inspect.getsource(LLMClient._get_openai)
    sdk_runtime = _runtime_client_preflight()
    input_sha = _sha256_bytes(A_INPUT.encode("utf-8"))
    v4_release_sha = _canonical_sha256(v4_release)
    v5_release_sha = _canonical_sha256(release)
    selector_prompt_sha = v5._sha256_text(v5._FINALIZER_SELECTION_PROMPT)
    selector_schema_sha = v5._canonical_sha256(v5.FinalizerSelectionV5.model_json_schema())

    checks = {
        "exact_explicit_capability_qa_read_only_one_step": (
            capability.capability_id == CAPABILITY_ID
            and capability.expert_id == EXPERT_ID
            and capability.version == "0.1.0"
            and capability.execution_pipeline == v5.PIPELINE_ID
            and capability.role == "qa" and capability.manifest().get("read_only") is True
            and capability.max_steps == 1
            and set(capability.skill_ids) == set(EXPECTED_SKILLS_AND_REFS)
        ),
        "v5_exact_release_including_nested_v4_hash": (
            release.get("contract_version") == 5
            and release.get("capability_id") == CAPABILITY_ID
            and v5_release_sha == EXPECTED_V5_RELEASE_SHA256
            and release.get("extractor_release_sha256") == EXPECTED_V4_RELEASE_SHA256
            and v4_release_sha == EXPECTED_V4_RELEASE_SHA256
        ),
        "v5_renderer_v2_and_direct_helper_closure_pinned": (
            v5_renderer.get("version") == "education-screening-v5-selection-renderer-2"
            and v5_renderer.get("source_sha256") == EXPECTED_V5_RENDERER_SHA256
            and direct_dependencies == EXPECTED_V5_DIRECT_DEPENDENCIES
            and _canonical_sha256(v5_renderer) == _canonical_sha256(release["renderer"])
        ),
        "v4_projection_and_helper_closure_pinned": (
            projection.get("source_sha256") == EXPECTED_V4_PROJECTION_SHA256
            and projection.get("dependency_source_sha256") == EXPECTED_V4_HELPER_CLOSURE
            and v4._contract_matches(v4_release)
        ),
        "skill_reference_versions_and_bytes_match_pins": (
            actual_skills == EXPECTED_SKILLS_AND_REFS
            and v4.v3._asset_pins(dict(assets)) == v4_release.get("skill_assets")
            and set(actual_skills) == set(capability.skill_ids)
        ),
        "v4_extractor_prompt_and_schema_match": (
            actual_extractor_prompt_sha == EXPECTED_V4_EXTRACTOR_PROMPT_SHA256
            and (v4_release.get("extractor") or {}).get("prompt_sha256")
            == EXPECTED_V4_EXTRACTOR_PROMPT_SHA256
            and (v4_release.get("extractor") or {}).get("schema_sha256")
            == EXPECTED_V4_EXTRACTOR_SCHEMA_SHA256
        ),
        "v5_selector_prompt_schema_and_json_object_match": (
            selector_prompt_sha == EXPECTED_V5_SELECTOR_PROMPT_SHA256
            and v5_selector.get("prompt_sha256") == EXPECTED_V5_SELECTOR_PROMPT_SHA256
            and selector_schema_sha == EXPECTED_V5_SELECTOR_SCHEMA_SHA256
            and v5_selector.get("schema_sha256") == EXPECTED_V5_SELECTOR_SCHEMA_SHA256
            and v5_selector.get("response_format_kind") == "json_object"
            and v5_selector.get("message_roles") == ["system", "user"]
            and v5_selector.get("temperature") == 0.0
            and v5_selector.get("max_tokens") == 320
        ),
        "qwen_plus_only_routes_and_runtime_client": (
            routes == {"v4_extractor": EXPECTED_MODEL, "v5_selector": EXPECTED_MODEL}
            and all(model == EXPECTED_MODEL for model in all_nested_routes)
            and sdk_runtime.get("configured") is True
            and sdk_runtime.get("provider") == EXPECTED_PROVIDER
            and sdk_runtime.get("failover_order") == [EXPECTED_PROVIDER]
        ),
        "sdk_retry_source_and_runtime_retry_count_zero": (
            sdk_static_retries_zero and sdk_runtime.get("sdk_max_retries") == 0
        ),
        "fixed_synthetic_a_input_hash_matches": input_sha == EXPECTED_A_INPUT_SHA256,
        "one_step_usage_cap_is_30000_and_provider_calls_at_most_two": (
            TURN_TOKEN_CAP == 30000 and MAX_PROVIDER_CALLS == 2
        ),
        "new_evidence_file_directly_under_d_temp": _output_path_ok(output),
        "probe_never_enables_application_or_persistence_path": True,
        "qwen_credentials_and_base_url_present_without_disclosure": sdk_runtime.get("configured") is True,
        "runtime_v5_contract_matches_current_assets_and_route": v5._contract_matches(release),
    }
    return {
        "passed": all(checks.values()), "checks": checks,
        "capability_binding": capability.manifest(),
        "release_sha256": v5_release_sha, "nested_v4_release_sha256": v4_release_sha,
        "input_sha256": input_sha, "routes": routes,
        "v4_extractor_prompt_sha256": actual_extractor_prompt_sha,
        "v4_extractor_schema_sha256": (v4_release.get("extractor") or {}).get("schema_sha256"),
        "v5_selector_prompt_sha256": selector_prompt_sha,
        "v5_selector_schema_sha256": selector_schema_sha,
        "skill_reference_hashes": actual_skills,
        "v4_projection_implementation": projection,
        "v5_renderer_pin": v5_renderer,
        "sdk_runtime_preflight": sdk_runtime,
        "release_contract": release,
        "evidence_path": str(output),
        "token_cap": TURN_TOKEN_CAP,
        "max_provider_calls": MAX_PROVIDER_CALLS,
    }


def _default_output_path() -> Path:
    root = Path(r"D:\Temp")
    if not root.is_dir():
        raise RuntimeError("D:\\Temp is unavailable")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"education-screening-v5-a-{stamp}-{uuid4().hex[:8]}.json"


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
            "stage": row.get("stage"), "usage": dict(usage) if usage else None,
            "finish_reason": row.get("finish_reason"),
            "preflight_fits": preflight.get("fits"),
            "preflight_reason": preflight.get("reason"),
            "estimated_input_tokens": preflight.get("estimated_input_tokens"),
            "input_limit_tokens": preflight.get("input_limit_tokens"),
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
    """Capture only the fixed synthetic A chain; never retry a stage."""

    STAGES = ("structured_education_qa", "v5_id_only_selector")

    def __init__(self, client):
        self.client = client
        self.stages: list[dict[str, Any]] = []

    def chat_with_metadata(self, **kwargs):
        if len(self.stages) >= MAX_PROVIDER_CALLS:
            raise RuntimeError("probe_call_cap_exceeded")
        stage_index = len(self.stages)
        messages = kwargs.get("messages")
        route_ok = kwargs.get("model_override") == EXPECTED_MODEL
        format_ok = kwargs.get("response_format") == {"type": "json_object"}
        stage = {
            "stage": self.STAGES[stage_index],
            "route_model_matches": route_ok, "json_object_matches": format_ok,
            "message_roles": [item.get("role") for item in messages if isinstance(item, Mapping)]
            if isinstance(messages, list) else [],
            "request_messages_sha256": _canonical_sha256(messages),
            "message_content_sha256": [
                _sha256_bytes(str(item.get("content") or "").encode("utf-8"))
                for item in messages if isinstance(item, Mapping)
            ] if isinstance(messages, list) else [],
            "request_attempted": True,
        }
        self.stages.append(stage)
        if not route_ok or not format_ok:
            stage["error_code"] = "pinned_request_contract_mismatch"
            raise RuntimeError("pinned_request_contract_mismatch")
        if stage_index == 1 and (not isinstance(messages, list) or [
            item.get("role") for item in messages if isinstance(item, Mapping)
        ] != ["system", "user"]):
            stage["error_code"] = "selector_message_roles_mismatch"
            raise RuntimeError("selector_message_roles_mismatch")
        if stage_index == 1:
            try:
                selector_payload = json.loads(messages[1]["content"])
                selector_dump = json.dumps(selector_payload, ensure_ascii=False)
                stage["selector_context_safe"] = bool(
                    isinstance(selector_payload, Mapping)
                    and "allowed_conclusions" in selector_payload
                    and A_INPUT not in selector_dump
                    and "source_quote" not in selector_dump
                    and "question_focus" not in selector_dump
                )
            except Exception:
                stage["selector_context_safe"] = False
            if stage["selector_context_safe"] is not True:
                stage["error_code"] = "selector_context_contains_raw_evidence"
                raise RuntimeError("selector_context_contains_raw_evidence")
        try:
            response = self.client.chat_with_metadata(**kwargs)
        except Exception as exc:
            stage["error_code"] = type(exc).__name__[:80]
            raise
        raw_text = getattr(response, "text", None)
        stage.update({
            "provider": getattr(response, "provider", None),
            "model": getattr(response, "model", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "usage": getattr(response, "usage", None),
            "response_chars": len(raw_text) if isinstance(raw_text, str) else None,
            "raw_response_text": raw_text,
        })
        return response


def _parse_json_response(stage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    raw = stage.get("raw_response_text") if isinstance(stage, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _source_binding_checks(analysis: Mapping[str, Any]) -> dict[str, Any]:
    input_sha = EXPECTED_A_INPUT_SHA256
    manifest = {
        row.get("source_id"): row for row in analysis.get("source_manifest", [])
        if isinstance(row, Mapping)
    }
    user_source = manifest.get("user_message") or {}

    def validate_rows(rows, require_value_quote: bool) -> list[bool]:
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                result.append(False)
                continue
            source_quote = row.get("source_quote")
            value_quote = row.get("value_quote")
            result.append(bool(
                row.get("source_id") == "user_message"
                and row.get("source_kind") == "user_provided_unverified"
                and row.get("source_sha256") == input_sha
                and isinstance(source_quote, str) and source_quote in A_INPUT
                and row.get("source_quote_sha256") == _sha256_bytes(source_quote.encode("utf-8"))
                and row.get("quote_integrity") == "exact_substring_only"
                and row.get("interpretation_status") == "model_candidate_unverified"
                and (not require_value_quote or (
                    isinstance(value_quote, str) and bool(value_quote) and value_quote in source_quote
                ))
            ))
        return result

    focus = analysis.get("question_focus")
    focus_bound = bool(
        isinstance(focus, Mapping)
        and focus.get("source_id") == "user_message"
        and focus.get("source_sha256") == input_sha
        and focus.get("source_quote_sha256") == _sha256_bytes(
            str(focus.get("source_quote") or "").encode("utf-8"),
        )
        and focus.get("quote_integrity") == "exact_substring_only"
        and focus.get("interpretation_status") == "model_candidate_unverified"
        and isinstance(focus.get("source_quote"), str)
        and focus["source_quote"] in A_INPUT
    )
    return {
        "user_manifest_hash_matches_input": user_source.get("source_sha256") == input_sha,
        "job_conditions": validate_rows(analysis.get("job_conditions"), require_value_quote=True),
        "candidate_statements": validate_rows(
            analysis.get("candidate_statements"), require_value_quote=True,
        ),
        "question_focus_bound": focus_bound,
    }


def _usage_consistency(capture: _OneShotCapture, ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    calls = _safe_ledger(ledger).get("calls") or []
    for index, stage in enumerate(capture.stages):
        call = calls[index] if index < len(calls) else {}
        usage = stage.get("usage")
        rows.append({
            "stage": stage.get("stage"),
            "provider_matches_qwen": stage.get("provider") == EXPECTED_PROVIDER
                and call.get("provider") == EXPECTED_PROVIDER,
            "model_matches_qwen_plus": stage.get("model") == EXPECTED_MODEL
                and call.get("model") == EXPECTED_MODEL,
            "usage_matches_ledger": isinstance(usage, Mapping) and usage == call.get("usage"),
            "finish_reason_matches_ledger": stage.get("finish_reason") == call.get("finish_reason"),
            "request_route_and_json_contract_match": (
                stage.get("route_model_matches") is True
                and stage.get("json_object_matches") is True
            ),
        })
    return rows


def _run_live(v4, v5, release: Mapping[str, Any], check: Mapping[str, Any], output: Path) -> dict[str, Any]:
    from config.integration_loader import get_provider_config
    from app.services.agent_runtime.budget import capture_usage_ledger, current_usage_ledger
    from app.utils.llm_client import LLMClient

    config = get_provider_config(EXPECTED_PROVIDER)
    if not config.get("api_key") or not config.get("base_url"):
        raise RuntimeError("qwen_configuration_unavailable")
    # Explicit provider/API/base URL forces LLMClient to a one-provider chain;
    # probe setup clears LITELLM_BASE_URL before constructing the client.
    client = LLMClient(
        provider=EXPECTED_PROVIDER, api_key=config["api_key"], base_url=config["base_url"],
        model=EXPECTED_MODEL, timeout=config.get("timeout"),
    )
    if client.failover_order != [EXPECTED_PROVIDER]:
        raise RuntimeError("qwen_only_client_unavailable")
    sdk, _ = client._get_openai(EXPECTED_PROVIDER)
    if getattr(sdk, "max_retries", None) != 0:
        raise RuntimeError("runtime_sdk_retries_not_zero")
    capture = _OneShotCapture(client)
    run_error = None
    result: dict[str, Any] = {}
    with capture_usage_ledger(PROBE_MODE, turn_token_cap=TURN_TOKEN_CAP) as ledger:
        if current_usage_ledger() is not ledger or ledger.get("calls"):
            raise RuntimeError("same_step_usage_ledger_preflight_failed")
        try:
            result = v5.run_structured_education_step_v5(
                user_message=A_INPUT, owner_id="synthetic-owner-v5-a",
                run_id=f"synthetic-run-v5-a-{uuid4()}", expected_runtime_contract=dict(release),
                verified_school_facts=(), receipts=(), llm=capture,
            )
        except Exception as exc:
            run_error = type(exc).__name__[:80]

    safe_ledger = _safe_ledger(ledger)
    usage_checks = _usage_consistency(capture, ledger)
    analysis = result.get("analysis") if isinstance(result.get("analysis"), Mapping) else {}
    projection = result.get("projection") if isinstance(result.get("projection"), Mapping) else {}
    finalizer = result.get("finalizer") if isinstance(result.get("finalizer"), Mapping) else {}
    qa_json = _parse_json_response(capture.stages[0] if capture.stages else None)
    selector_json = _parse_json_response(capture.stages[1] if len(capture.stages) > 1 else None)
    source_checks = _source_binding_checks(analysis)
    conclusions = [row for row in (projection.get("allowed_conclusions") or []) if isinstance(row, Mapping)]
    primary = next((row for row in conclusions
                    if row.get("id") == projection.get("primary_conclusion_id")), {})
    refs = primary.get("source_refs") if isinstance(primary.get("source_refs"), list) else []
    ref_by_id = {row.get("id"): row for row in refs if isinstance(row, Mapping)}
    final_reply = result.get("reply") if isinstance(result.get("reply"), str) else ""
    checks = {
        "provider_calls_exactly_two_ordered_stages": (
            len(capture.stages) == 2
            and [row.get("stage") for row in capture.stages] == list(_OneShotCapture.STAGES)
            and safe_ledger.get("call_count") == 2
        ),
        "usage_observed_matches_provider_and_ledger_within_30000_cap": (
            safe_ledger.get("status") == "observed"
            and safe_ledger.get("turn_token_cap") == TURN_TOKEN_CAP
            and safe_ledger.get("reported_total_tokens", TURN_TOKEN_CAP + 1) <= TURN_TOKEN_CAP
            and len(usage_checks) == 2
            and all(all(row[key] for key in (
                "provider_matches_qwen", "model_matches_qwen_plus", "usage_matches_ledger",
                "finish_reason_matches_ledger", "request_route_and_json_contract_match",
            )) for row in usage_checks)
        ),
        "qa_status_and_job_candidate_focus_are_source_bound": (
            analysis.get("status") == "model_candidate_unverified"
            and source_checks["user_manifest_hash_matches_input"]
            and bool(source_checks["job_conditions"]) and all(source_checks["job_conditions"])
            and bool(source_checks["candidate_statements"])
            and all(source_checks["candidate_statements"])
            and source_checks["question_focus_bound"]
        ),
        "projection_is_conditional_local_degree_conclusion_with_role_refs": (
            primary.get("status") == "conditional_not_demonstrated"
            and primary.get("scope")
            and ref_by_id.get("job_condition_source", {}).get("source_quote") == "硕士研究生及以上"
            and isinstance(ref_by_id.get("candidate_statement_source", {}).get("source_quote"), str)
            and "普通本科、无硕士" in ref_by_id.get("candidate_statement_source", {}).get("source_quote", "")
        ),
        "selector_is_strict_id_only_choice_and_server_reply": (
            isinstance(selector_json, Mapping)
            and set(selector_json) == {"selected_conclusion_ids", "action_id", "style"}
            and projection.get("primary_conclusion_id") in selector_json.get("selected_conclusion_ids", [])
            and finalizer.get("decision_status") == "server_rendered_from_selection"
            and isinstance(finalizer.get("selector"), Mapping)
            and finalizer["selector"].get("usage_status") == "observed"
            and len(capture.stages) > 1
            and capture.stages[1].get("selector_context_safe") is True
        ),
        "public_reply_quotes_sources_and_stays_local": (
            "你提供且未核验的岗位条款「硕士研究生及以上」" in final_reply
            and "学历自述「" in final_reply
            and "当前材料尚未显示达到该学历层次" in final_reply
            and "整体资格不符合" in final_reply
            and "录用概率" not in final_reply and "ATS" not in final_reply
            and "例外情形" not in final_reply and "岗位明确要求" not in final_reply
        ),
        "no_error_and_no_durable_or_http_services": (
            run_error is None and result.get("verified_facts") == []
            and all(result.get(key) is None for key in (
                "application_http", "mysql", "redis", "celery", "d1",
            ))
        ),
    }
    evidence = {
        "probe": PROBE_MODE, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True, "input_text_synthetic_only": A_INPUT,
        "input_sha256": EXPECTED_A_INPUT_SHA256,
        "capability_id": CAPABILITY_ID, "expert_id": EXPERT_ID,
        "release_sha256": check["release_sha256"],
        "nested_v4_release_sha256": check["nested_v4_release_sha256"],
        "release_contract": release,
        "provider_runtime": {
            "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL,
            "failover_order": list(client.failover_order),
            "sdk_max_retries_observed": getattr(sdk, "max_retries", None),
            "provider_call_count": len(capture.stages),
            "configured_secrets_disclosed": False,
        },
        "stage_trace_synthetic_only": capture.stages,
        "usage_ledger": safe_ledger,
        "usage_consistency": usage_checks,
        "preflight_passed": check.get("passed"),
        "preflight_checks": check.get("checks"),
        "run_error_type": run_error,
        "qa_raw_json_parsed": qa_json,
        "qa_analysis": analysis,
        "source_binding_checks": source_checks,
        "server_projection": projection,
        "selector_raw_json_parsed": selector_json,
        "selector_metadata": finalizer.get("selector"),
        "public_reply": final_reply,
        "result_checks": checks,
        "application_http": False, "mysql": False, "redis": False,
        "celery": False, "d1": False,
        "manual_review": "pending",
        "non_claim": (
            "Quotes prove only that the submitted synthetic message contains these bytes; "
            "they do not establish a real, current or complete job notice. Model classifications "
            "remain candidates and the projection only makes a scoped comparison."
        ),
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "request_sent": bool(capture.stages), "stage_count": len(capture.stages),
        "run_error_type": run_error, "qa_status": analysis.get("status"),
        "job_rows": len(analysis.get("job_conditions") or []),
        "candidate_rows": len(analysis.get("candidate_statements") or []),
        "question_focus_source_bound": source_checks.get("question_focus_bound"),
        "primary_conclusion_status": primary.get("status"),
        "selector_status": finalizer.get("decision_status"),
        "usage_status": safe_ledger.get("status"),
        "usage_calls": safe_ledger.get("call_count"),
        "usage_consistency": usage_checks,
        "result_checks": checks, "public_reply": final_reply,
        "evidence_path": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="send one synthetic A chain to Qwen")
    parser.add_argument("--output", type=Path, help="new evidence file directly under D:\\Temp")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    # Disable proxy/tracing sinks: the intended provider is only Qwen, and raw
    # synthetic stage output must remain in the explicit local D:\Temp file.
    os.environ["LITELLM_BASE_URL"] = ""
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    os.environ["CPA_LANGFUSE_CAPTURE_CONTENT"] = "false"

    output = args.output or _default_output_path()
    try:
        v4, v5, capability, release, assets = _load_runtime()
        check = _release_checks(v4, v5, capability, release, assets, output)
    except Exception as exc:
        print(json.dumps({
            "probe": PROBE_MODE, "request_sent": False,
            "preflight_error_type": type(exc).__name__[:80],
            "evidence_path": str(output),
        }, ensure_ascii=False))
        return 2

    check_only = {
        "probe": PROBE_MODE,
        "mode": "one_shot_live" if args.live_model else "check_only",
        "request_sent": False, "application_http": False, "mysql": False,
        "redis": False, "celery": False, "d1": False,
        "checklist": check["checks"], "passed": check["passed"],
        "capability_binding": check["capability_binding"],
        "release_sha256": check["release_sha256"],
        "nested_v4_release_sha256": check["nested_v4_release_sha256"],
        "input_sha256": check["input_sha256"], "routes": check["routes"],
        "v4_extractor_prompt_sha256": check["v4_extractor_prompt_sha256"],
        "v4_extractor_schema_sha256": check["v4_extractor_schema_sha256"],
        "v5_selector_prompt_sha256": check["v5_selector_prompt_sha256"],
        "v5_selector_schema_sha256": check["v5_selector_schema_sha256"],
        "skill_reference_hashes": check["skill_reference_hashes"],
        "v4_projection_implementation": check["v4_projection_implementation"],
        "v5_renderer_pin": check["v5_renderer_pin"],
        "sdk_runtime_preflight": check["sdk_runtime_preflight"],
        "retry_policy": {
            "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL,
            "sdk_max_retries": 0, "provider_failover": "disabled",
            "maximum_provider_calls": MAX_PROVIDER_CALLS,
        },
        "usage_ledger": {"scope": "one v5 structured step", "turn_token_cap": TURN_TOKEN_CAP},
        "evidence_path": check["evidence_path"],
    }
    if not args.live_model:
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 0 if check["passed"] else 2
    if not check["passed"] or not _output_path_ok(output):
        print(json.dumps(check_only, ensure_ascii=False, sort_keys=True))
        return 2

    try:
        summary = _run_live(v4, v5, release, check, output)
    except Exception as exc:
        # The provider may already have received a request; preserve this
        # result as an attempt, never retry automatically.
        print(json.dumps({
            **check_only, "request_sent": "unknown", "request_may_have_been_sent": True,
            "no_retry": True, "live_error_type": type(exc).__name__[:80],
        }, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({**check_only, **summary}, ensure_ascii=False, sort_keys=True))
    return 0 if all(summary["result_checks"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
