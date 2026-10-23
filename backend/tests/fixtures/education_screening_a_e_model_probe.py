"""Opt-in A–E model review for the unreleased education-screening pipeline.

This is a model-only synthetic replay of the QA prompt/Skill, typed extractor,
and constrained finalizer. It deliberately has no owner id, business tools,
HTTP request, Celery worker, or database access. It is not a substitute for the
later isolated durable HTTP acceptance.

Run only with ``--live-model`` after the no-model contracts pass. The active
provider/model must be exactly qwen/qwen-plus. The synthetic inputs and
reviewable user-visible answers are written to a new evidence JSON under tmp/.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _condition_projection(record: dict) -> list[dict]:
    projected = []
    for row in record.get("conditions") or []:
        if not isinstance(row, dict):
            continue
        projected.append({
            "source_id": row.get("source_id"),
            "source_kind": row.get("source_kind"),
            "source_quote": row.get("source_quote"),
            "quote_integrity": row.get("quote_integrity"),
            "interpretation_status": row.get("interpretation_status"),
            "speaker_candidate": row.get("speaker_candidate"),
            "dimension_candidate": row.get("dimension_candidate"),
            "condition_type_candidate": row.get("condition_type_candidate"),
            "operator_candidate": row.get("operator_candidate"),
            "relation_candidate": row.get("relation_candidate"),
            "value_quote": row.get("value_quote"),
            "qualification_use": row.get("qualification_use"),
            "downgraded_fields": row.get("downgraded_fields") or [],
            "diagnostics": row.get("diagnostics") or [],
        })
    return projected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true",
        help="explicitly permit synthetic qwen-plus requests; each case makes up to three calls",
    )
    parser.add_argument(
        "--output", type=Path,
        help="new evidence file path (default: unique tmp/education-screening-a-e-*.json)",
    )
    args = parser.parse_args()
    if not args.live_model:
        parser.error("--live-model is required; no provider request was made")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app.services.agent_runtime.agent_run_executor import (
        _persist_education_conditions,
        _project_step_model_usage,
    )
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.budget import capture_usage_ledger
    from app.services.agent_runtime.education_screening import (
        education_screening_release_contract,
        extract_education_conditions,
    )
    from app.services.agent_runtime.mode_runtime import ModeRuntime, _invoke_subagent
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.plan import build_plan_packets
    from app.services.agent_runtime.skill_registry import load_skill
    from app.utils.llm_client import get_llm_client

    client = get_llm_client()
    provider = str(getattr(client, "provider", "") or "")
    model = str(getattr(client, "model", "") or "")
    if provider != "qwen" or model != "qwen-plus":
        print(json.dumps({
            "probe": "education_screening_a_e_model_review",
            "request_sent": False,
            "reason": "configured_provider_or_model_not_exact_qwen_plus",
            "provider": provider[:48],
            "model": model[:128],
        }, ensure_ascii=False, sort_keys=True))
        return 2

    capability_id = "education-screening"
    skills = [
        load_skill("education-screening-context", role="qa", capability_id=capability_id),
        load_skill("school-context", role="qa"),
    ]
    skill_pins = [{
        "skill_id": skill.skill_id,
        "version": skill.version,
        "sha256": skill.sha256,
    } for skill in skills]
    cases = []

    for case_id, message in FIXTURES.items():
        run_id = "synthetic-education-review-" + uuid4().hex
        release = education_screening_release_contract("experts")
        step_spec = [{
            "role": "qa",
            "goal": "按当前用户目标分析该具体招聘情境中的教育条件、来源与未知项，并给出只读答复。",
            "skill_ids": ["education-screening-context", "school-context"],
        }]
        packet = build_plan_packets(
            step_spec,
            message,
            handoff_brief="对照用户提供的岗位条件与候选人自述，区分明示门槛、优先项、来源与未知。",
            capability_id=capability_id,
        )[0]

        with capture_usage_ledger("education_screening_a_e_model_review", turn_token_cap=30000) as ledger:
            # Same capability scope, TaskPacket and loaded Skills as the QA
            # executor path, but tools and owner-profile access stay disabled.
            qa = _invoke_subagent(
                packet,
                Blackboard(),
                None,
                allow_tools=False,
                interaction_mode=InteractionMode.EXPERTS,
                student_id=None,
                allow_artifact_commit=False,
            )
            education_skill = next(
                row for row in qa.get("skill_versions") or []
                if isinstance(row, dict) and row.get("skill_id") == "education-screening-context"
            )
            extracted = extract_education_conditions(
                user_message=message,
                owner_id="synthetic-model-review-owner",
                run_id=run_id,
                skill_sha256=str(education_skill.get("sha256") or ""),
                receipts=(),
                llm=client,
                include_redacted_diagnostics=True,
                expected_runtime_contract=release,
            )
            persisted_conditions = _persist_education_conditions(extracted)
            finalizer = ModeRuntime()._finalize_plan_reply(
                message=message,
                steps=[{
                    "role": "qa",
                    "status": "completed",
                    "verified_facts": qa.get("verified_facts") or [],
                    "education_conditions": persisted_conditions,
                    # Deliberately include the narrative to prove the
                    # constrained finalizer does not treat it as evidence.
                    "reply_candidate_unverified": qa.get("reply") or "",
                }],
                interaction_mode=InteractionMode.EXPERTS,
                education_screening_constrained=True,
                return_details=True,
                education_pipeline_release=release,
            )
            model_usage = _project_step_model_usage(ledger)
            calls = ledger.get("calls") or []
            call_roles = ["qa", "extractor", "main_finalizer"]
            usage_by_stage = []
            for index, call in enumerate(calls[:3]):
                if not isinstance(call, dict):
                    continue
                usage_by_stage.append({
                    "stage": call_roles[index] if index < len(call_roles) else "unexpected",
                    "provider": call.get("provider"),
                    "model": call.get("model"),
                    "finish_reason": call.get("finish_reason"),
                    "usage": call.get("usage"),
                })

        cases.append({
            "case_id": case_id,
            "synthetic_input": message,
            "input_sha256": _sha256(message.encode("utf-8")),
            "qa_candidate_unverified": str(qa.get("reply") or "")[:1200],
            "qa_incomplete": bool(qa.get("incomplete")),
            "skill_versions_observed": qa.get("skill_versions") or [],
            "qa_source_read_cards": qa.get("tool_calls") or [],
            "extractor": {
                "status": extracted.get("status"),
                "error_code": extracted.get("error_code"),
                "usage_status": extracted.get("usage_status"),
                "prompt_sha256": extracted.get("prompt_sha256"),
                "schema_sha256": extracted.get("schema_sha256"),
                "source_manifest": extracted.get("source_manifest") or [],
                "source_scope": extracted.get("source_scope"),
                "conditions": _condition_projection(extracted),
                "redacted_row_diagnostics": extracted.get("redacted_row_diagnostics") or [],
            },
            "finalizer": {
                "decision_status": finalizer.get("decision_status"),
                "decision_reason": finalizer.get("decision_reason"),
                "provider": finalizer.get("provider"),
                "model": finalizer.get("model"),
                "finish_reason": finalizer.get("finish_reason"),
                "usage": finalizer.get("usage"),
                "usage_status": finalizer.get("usage_status"),
                "pipeline_release_sha256": finalizer.get("pipeline_release_sha256"),
                "reply": str(finalizer.get("reply") or "")[:4000],
            },
            "step_model_usage": model_usage,
            "provider_calls": usage_by_stage,
            "pipeline_contract": release,
        })

    output = args.output
    if output is None:
        output_dir = ROOT / "tmp"
        output = output_dir / (
            "education-screening-a-e-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid4().hex[:8] + ".json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "probe": "education_screening_a_e_model_review",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_sent": True,
        "http_or_database": False,
        "owner_profile_access": False,
        "business_tools_enabled": False,
        "configured_provider": provider,
        "configured_model": model,
        "capability_id": capability_id,
        "skill_pins": skill_pins,
        "pipeline_contract_sha256": _sha256(json.dumps(
            education_screening_release_contract("experts"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")),
        "cases": cases,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "probe": payload["probe"],
        "request_sent": True,
        "http_or_database": False,
        "owner_profile_access": False,
        "business_tools_enabled": False,
        "provider": provider,
        "model": model,
        "skill_pins": skill_pins,
        "pipeline_contract_sha256": payload["pipeline_contract_sha256"],
        "case_summaries": [{
            "case_id": case["case_id"],
            "extractor_status": case["extractor"]["status"],
            "extractor_error_code": case["extractor"]["error_code"],
            "finalizer_status": case["finalizer"]["decision_status"],
            "finalizer_reason": case["finalizer"]["decision_reason"],
            "model_usage_status": case["step_model_usage"]["status"],
            "call_count": case["step_model_usage"]["call_count"],
        } for case in cases],
        "evidence_path": str(output.resolve()),
        "raw_provider_json_saved": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if all(case["step_model_usage"].get("status") == "observed" for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
