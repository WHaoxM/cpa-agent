"""Fetch official vacancies and persist attributable model reviews, never publish."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-id", action="append", default=[])
    parser.add_argument("--list", action="store_true", help="List source ids/titles; no model or DB writes")
    parser.add_argument("--persist", action="store_true", help="Persist independent model audit, not publication")
    args = parser.parse_args()
    if not args.list and not args.record_id:
        parser.error("Specify --record-id or --list")
    from app.services.job_catalog_review import fetch_official_feed, review_record, save_review
    records, digest, fetched_at = fetch_official_feed()
    if args.list:
        print(json.dumps({"feed_sha256": digest, "fetched_at": fetched_at,
                          "records": [{k: row.get(k) for k in ("jobid", "jobname", "enddate")}
                                      for row in records]}, ensure_ascii=False))
        return
    from app.utils.llm_client import LLMClient
    from app.services.db.mysql_client import get_mysql_client
    llm = LLMClient()
    for rid in args.record_id:
        matches = [row for row in records if str(row.get("jobid")) == rid]
        if len(matches) != 1:
            parser.error(f"record {rid} missing or ambiguous")
        review = review_record(matches[0], feed_sha256=digest, fetched_at=fetched_at, llm=llm)
        print(json.dumps(save_review(get_mysql_client(), review) if args.persist else review,
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
