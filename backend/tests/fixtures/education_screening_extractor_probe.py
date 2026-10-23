"""Single-call, synthetic JSON Object contract probe for the unreleased extractor.

This makes no HTTP request, opens no database, reads no owner data, and prints
no model response text. The caller must explicitly pass ``--live-model``; the
configured default model must be exactly ``qwen-plus`` before one call is sent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


SYNTHETIC_MESSAGE = (
    "请按这段材料做结构抽取，不要判断公告真假或是否当前。"
    "岗位原文：硕士及以上，计算机相关专业优先。候选人自述：最高学历本科，专业软件工程。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true",
        help="explicitly permit one synthetic qwen-plus JSON Object request",
    )
    args = parser.parse_args()
    if not args.live_model:
        parser.error("--live-model is required; no provider request was made")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app.services.agent_runtime.budget import capture_usage_ledger
    from app.services.agent_runtime.education_screening import extract_education_conditions
    from app.utils.llm_client import get_llm_client

    skill_path = BACKEND / "config" / "agent_skills" / "education-screening-context" / "SKILL.md"
    skill_sha256 = hashlib.sha256(skill_path.read_bytes()).hexdigest()
    client = get_llm_client()
    configured_model = str(getattr(client, "model", "") or "")
    if configured_model != "qwen-plus":
        print(json.dumps({
            "probe": "education_screening_json_object_contract",
            "request_sent": False,
            "reason": "configured_default_model_not_exact_qwen_plus",
            "configured_model": configured_model[:128],
            "skill_sha256": skill_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 2

    with capture_usage_ledger("education_screening_contract_probe", turn_token_cap=5000) as ledger:
        result = extract_education_conditions(
            user_message=SYNTHETIC_MESSAGE,
            owner_id="synthetic-probe-owner",
            run_id="synthetic-probe-run",
            skill_sha256=skill_sha256,
            llm=client,
            include_redacted_diagnostics=True,
        )

    summary = {
        "probe": "education_screening_json_object_contract",
        "request_sent": True,
        "http_or_database": False,
        "model": result.get("model"),
        "provider": result.get("provider"),
        "finish_reason": result.get("finish_reason"),
        "usage": result.get("usage"),
        "usage_status": result.get("usage_status"),
        "turn_token_cap": ledger.get("turn_token_cap"),
        "step_ledger_call_count": len(ledger.get("calls") or []),
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "validation_failures": result.get("validation_failures"),
        "redacted_row_diagnostics": result.get("redacted_row_diagnostics", []),
        "condition_count": len(result.get("conditions") or []),
        "prompt_sha256": result.get("prompt_sha256"),
        "schema_sha256": result.get("schema_sha256"),
        "skill_sha256": skill_sha256,
        "input_message_sha256": result.get("input_message_sha256"),
        "request_sources_sha256": result.get("request_sources_sha256"),
        "raw_response_saved": False,
        "private_source_text_emitted": False,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "model_candidate_unverified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
