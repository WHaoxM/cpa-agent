"""Read-only model behavior assessment replay. Never confirms or persists a proposal."""
import argparse
import copy
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
    parser.add_argument("--official-school-url", help="Explicitly selected official school evidence; never discovered by guessing")
    parser.add_argument("--validate-only", action="store_true", help="Validate local evidence IDs without calling a model")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(BACKEND.parent / ".env")
    from app.services.db.mysql_client import get_mysql_client
    from app.services.pipeline import require_confirmed_profile
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    from app.utils.llm_client import get_llm_client

    db = get_mysql_client()
    original = require_confirmed_profile(db, args.student_id)
    if original["_resume_confirmation"]["task_id"] != args.task_id:
        raise ValueError("probe_requires_current_confirmed_source")
    row = db.fetch_all("SELECT student_id, pipeline, params_json, result_json FROM pipeline_task "
                       "WHERE task_id=:tid", {"tid": args.task_id})[0]
    if row["student_id"] != args.student_id or row["pipeline"] != "L0_parse_resume":
        raise ValueError("source_owner_or_type_mismatch")
    params = json.loads(row["params_json"]) if isinstance(row["params_json"], str) else row["params_json"]
    result = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else row["result_json"]
    parsed = L0FullParser().parse_file(params["file_path"])
    if not parsed.success:
        raise RuntimeError("source_parse_failed")
    source = parsed.text.strip()
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != original.get("source_sha256"):
        raise ValueError("reparsed_source_differs_from_confirmed_snapshot")
    evidence = result["source_evidence"]
    if evidence.get("missing_pages"):
        raise ValueError("incomplete_resume_pages")
    sources = [{"source_id": "resume", "source_kind": "resume", "source_task_id": args.task_id,
                "page": block["page"], "block_id": str(block["block_id"]), "text": block["text"]}
               for block in evidence["blocks"]]
    if args.official_school_url:
        import requests
        from bs4 import BeautifulSoup
        response = requests.get(args.official_school_url, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"
        document = BeautifulSoup(response.text, "html.parser")
        for element in document(["script", "style"]):
            element.decompose()
        sources.extend({"source_id": response.url, "source_kind": "official_school", "page": None,
                        "block_id": f"html-text-{index}", "text": text}
                       for index, text in enumerate(document.stripped_strings) if text.strip())
    generator = PortraitGenerator()
    bank = generator._assessment_evidence_bank(source, sources)
    if args.validate_only:
        after = require_confirmed_profile(db, args.student_id)
        if original != after:
            raise RuntimeError("authoritative_profile_changed_during_probe")
        print(json.dumps({"source_task_id": args.task_id, "source_sha256": digest,
            "source_pages": evidence["observed_pages"], "validated_blocks": len(bank),
            "unique_ids": len(set(bank)), "model_calls": 0, "writes": 0,
            "authoritative_profile_unchanged": True}, ensure_ascii=False, indent=2))
        return
    calls = []
    client = get_llm_client()

    class RecordedClient:
        def chat_with_metadata(self, **kwargs):
            response = client.chat_with_metadata(**kwargs)
            reference_checks = []

            def check_quotes(node, path=""):
                if isinstance(node, dict):
                    if "evidence_id" in node:
                        reference_checks.append({"path": path, "evidence_id": node["evidence_id"],
                                                 "matched": node["evidence_id"] in bank})
                    for key, child in node.items():
                        check_quotes(child, path + "." + key)
                elif isinstance(node, list):
                    for index, child in enumerate(node):
                        check_quotes(child, path + "." + str(index))

            try:
                check_quotes(json.loads(response.text))
            except (TypeError, ValueError):
                pass
            calls.append({"provider": response.provider, "model": response.model,
                          "finish_reason": response.finish_reason, "usage": response.usage,
                          "context_fits": response.context_status.fits, "reference_checks": reference_checks})
            return response

    candidate = generator.propose_behavior_assessment(RecordedClient(), source_text=source, sources=sources)
    replay = copy.deepcopy(original)
    # Deliberately retain source confirmation but do not create assessment approval.
    replay["portrait_assessment"] = candidate
    portrait = generator.generate_student_portrait(args.student_id,
        [{"name": s} if isinstance(s, str) else s for s in replay.get("skills", [])], student_profile=replay)
    after = require_confirmed_profile(db, args.student_id)
    if original != after:
        raise RuntimeError("authoritative_profile_changed_during_probe")
    print(json.dumps({"source_task_id": args.task_id, "source_sha256": digest,
        "source_chars": len(source), "source_pages": evidence["observed_pages"],
        "model_calls": calls, "candidate": candidate,
        "l2_status": portrait["status"], "school_tier": portrait["D1_basic"]["school_tier"],
        "school_type": portrait["D1_basic"]["school_type"], "sub_school": portrait["D1_basic"]["sub_school"],
        "d3_scores": {k: portrait["D3_quality"][k]["score"] for k in
                      ("communication", "teamwork", "stress_tolerance", "responsibility")},
        "missing_fields": portrait["missing_fields"], "competitiveness_score": portrait["summary"]["competitiveness_score"],
        "authoritative_profile_unchanged": True, "writes": 0,
        "next_action": "review_assessment_candidate_and_resolve_unmapped_school_rule"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
