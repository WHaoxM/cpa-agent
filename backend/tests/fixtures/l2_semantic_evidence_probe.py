"""Read-only live NSLE/L2 evidence probe; never confirms a model proposal for a user.

Run with mkg from the repo root. The source path is resolved from an owner-checked
L0 task, not from an arbitrary CLI file. Output excludes the private resume body.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(BACKEND.parent / ".env")
    from app.services.db.mysql_client import get_mysql_client
    from app.services.pipeline import require_confirmed_profile
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
    from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile, _parse_json
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    from app.utils.llm_client import get_llm_client

    db = get_mysql_client()
    confirmed = require_confirmed_profile(db, args.student_id)
    if confirmed["_resume_confirmation"]["task_id"] != args.task_id:
        raise ValueError("probe_requires_current_confirmed_source")
    row = db.fetch_all("SELECT student_id, pipeline, params_json, result_json FROM pipeline_task "
                       "WHERE task_id=:tid", {"tid": args.task_id})[0]
    if row["student_id"] != args.student_id or row["pipeline"] != "L0_parse_resume":
        raise ValueError("source_owner_or_type_mismatch")
    params = json.loads(row["params_json"]) if isinstance(row["params_json"], str) else row["params_json"]
    result = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else row["result_json"]
    parsed = L0FullParser().parse_file(params["file_path"])
    if not parsed.success:
        raise RuntimeError(parsed.error)
    source = parsed.text.strip()
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != confirmed.get("source_sha256"):
        raise ValueError("reparsed_source_differs_from_confirmed_snapshot")
    client = get_llm_client()
    calls = []

    class RecordedClient:
        def chat(self, **kwargs):
            response = client.chat_with_metadata(**kwargs)
            decoded = _parse_json(response.text)
            raw_facts = (decoded or {}).get("semantic_facts", {}) if isinstance(decoded, dict) else {}
            calls.append({"provider": response.provider, "model": response.model,
                "usage": response.usage, "finish_reason": response.finish_reason,
                "context_fits": response.context_status.fits,
                "raw_fact_keys": list(raw_facts) if isinstance(raw_facts, dict) else [],
                "raw_fact_shapes": {key: {"type": type(fact).__name__,
                    "fields": list(fact) if isinstance(fact, dict) else []}
                    for key, fact in raw_facts.items()} if isinstance(raw_facts, dict) else {},
                "evidence_in_source": {key: fact.get("evidence") in source
                    for key, fact in raw_facts.items() if isinstance(fact, dict)
                    and isinstance(fact.get("evidence"), str)} if isinstance(raw_facts, dict) else {}})
            return response.text

    proposed = extract_canonical_profile(RecordedClient(), source)
    # This remains an unconfirmed proposal. In particular, do not transplant the
    # existing confirmation marker into a freshly generated profile.
    generator = PortraitGenerator()
    existing = generator.generate_student_portrait(args.student_id,
        [{"name": s} if isinstance(s, str) else s for s in confirmed.get("skills", [])],
        student_profile=confirmed)
    candidate = generator.generate_student_portrait(args.student_id,
        [{"name": s} if isinstance(s, str) else s for s in proposed.get("skills", [])],
        student_profile=proposed)
    after = require_confirmed_profile(db, args.student_id)
    if confirmed != after:
        raise RuntimeError("authoritative_profile_changed_during_probe")
    pages = result["source_evidence"]
    print(json.dumps({
        "source_task_id": args.task_id, "source_sha256": digest,
        "source_chars": len(source), "source_path_verified": True,
        "observed_pages": pages.get("observed_pages"), "missing_pages": pages.get("missing_pages"),
        "page_block_counts": {str(page): sum(b["page"] == page for b in pages["blocks"])
                              for page in pages.get("observed_pages", [])},
        "model_calls": calls,
        "proposal_structure_path": proposed.get("structure_path"),
        "proposal_error": proposed.get("error"),
        "proposed_semantic_facts": proposed.get("semantic_facts", {}),
        "semantic_fact_issues": proposed.get("semantic_fact_issues", []),
        "confirmed_l2_status": existing["status"], "confirmed_l2_missing": existing["missing_fields"],
        "unconfirmed_proposal_l2_status": candidate["status"],
        "unconfirmed_proposal_l2_missing": candidate["missing_fields"],
        "authoritative_profile_unchanged": True, "writes": 0,
        "next_action": "review_model_candidate_not_auto_confirmation",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
