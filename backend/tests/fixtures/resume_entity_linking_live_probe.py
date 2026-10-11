"""Read-only real-model replay; prints counts/usage, never resume body or writes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from app.services.db import get_mysql_client
    from app.services.pipeline import require_confirmed_profile
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    db = get_mysql_client()
    profile = require_confirmed_profile(db, "student_001")
    tid = profile["_resume_confirmation"]["task_id"]
    row = db.fetch_all("SELECT result_json FROM pipeline_task WHERE task_id=:tid AND student_id=:sid",
                       {"tid": tid, "sid": "student_001"})[0]["result_json"]
    payload = json.loads(row) if isinstance(row, str) else row
    blocks = payload["source_evidence"]["blocks"]
    text = "".join(b["text"] for b in blocks)
    print(json.dumps({"source_task": tid, "input_chars": len(text), "blocks": len(blocks)}, ensure_ascii=False), flush=True)
    result = NSLEPipeline(use_deepseek=True).run_resume_text(text)
    output = result.get("student_profile", {})
    keys = ["projects", "competitions", "skills", "research"]
    print(json.dumps({"success": result.get("success"), "error": result.get("error"),
        "input_chars": len(text), "output_raw_text_chars": output.get("raw_text_chars"),
        "baseline_counts": {k: len(profile.get(k, [])) for k in keys},
        "output_counts": {k: len(output.get(k, [])) for k in keys},
        "structure_calls": output.get("structure_calls"),
        "entity_extraction_call": output.get("entity_extraction_call"),
        "entity_extraction_status": output.get("entity_extraction_status"),
        "entity_extraction_issues": output.get("entity_extraction_issues"),
        "mention_count": len(output.get("skill_mentions", [])),
        "link_status": output.get("entity_linking", {}).get("status"),
        "link_call": output.get("entity_linking", {}).get("call"),
        "link_reason": output.get("entity_linking", {}).get("reason")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
