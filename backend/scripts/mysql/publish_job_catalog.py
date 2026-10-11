"""Operator-only publication; no automatic trust upgrade for old catalog rows."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-file", type=Path, required=True,
                        help="UTF-8 source capture reviewed by operator; must exactly match snapshot source_text")
    parser.add_argument("--check", action="store_true", help="Validate without writing MySQL")
    parser.add_argument("--reviewed", action="store_true", required=True,
                        help="Confirm the source and extracted requirements were manually reviewed")
    args = parser.parse_args()
    from app.services.job_publication import publish_job, scoring_readiness, validate_snapshot
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    capture = args.source_file.read_bytes()
    if capture.decode("utf-8-sig") != snapshot.get("source_text"):
        parser.error("--source-file 与 source_text 不一致；请重新审核快照")
    if hashlib.sha256(snapshot["source_text"].encode("utf-8")).hexdigest() != snapshot.get("source_text_sha256"):
        parser.error("source_text_sha256 与来源文本不一致")
    validate_snapshot(snapshot)
    missing = scoring_readiness(snapshot)
    if args.check:
        print(json.dumps({"valid": True, "scoring_readiness": not missing,
                          "publishable": snapshot["review_state"] == "approved",
                          "missing_job_fields": missing}, ensure_ascii=False))
        return
    from app.services.db.mysql_client import get_mysql_client
    result = publish_job(get_mysql_client(), job_portrait_id=args.job_id, version=args.version,
                         snapshot=snapshot)
    print(json.dumps({k: result[k] for k in ("id", "job_portrait_id", "publication_version", "content_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
