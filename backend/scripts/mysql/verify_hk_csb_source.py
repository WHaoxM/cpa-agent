"""Read-only verification of one official Hong Kong Civil Service vacancy.

The feed is mutable. A changed record is a re-review signal, never an automatic
publication update. This script does not write MySQL or candidate files.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FEED_URI = "https://www.csb.gov.hk/datagovhk/gov-vacancies/gov-job-vacancies-en.json"


def verify(snapshot, *, now=None):
    from app.services.job_publication import canonical_json, validate_snapshot

    validate_snapshot(snapshot)
    if snapshot["source_uri"] != FEED_URI:
        raise ValueError("只接受已审查的公务员事务局官方 feed URI")
    response = requests.get(FEED_URI, timeout=20)
    response.raise_for_status()
    document = json.loads(response.content.decode("utf-8-sig"))
    records = [record for group in document["common"] for record in group.get("vacancies", [])]
    matches = [row for row in records if str(row.get("jobid")) == snapshot.get("source_record_id")]
    if not matches:
        return {"current": False, "reason": "record_missing", "record_id": snapshot.get("source_record_id")}
    if len(matches) != 1:
        return {"current": False, "reason": "record_ambiguous", "record_id": snapshot.get("source_record_id")}
    record = matches[0]
    raw_entry = record.get("entreq") or ""
    excerpts = [item["excerpt"] for item in snapshot["field_evidence"].values()
                if item["status"] == "explicit"]
    current_text = (record.get("jobname") or "") + "\n" + raw_entry
    excerpt_match = all(excerpt in current_text for excerpt in excerpts)
    try:
        closes = datetime.fromisoformat(record["enddate"])
        if closes.tzinfo is None:
            closes = closes.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
    except (KeyError, TypeError, ValueError):
        return {"current": False, "reason": "closing_time_unknown", "record_id": snapshot["source_record_id"]}
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("核验时间必须包含时区")
    digest = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
    unchanged = digest == snapshot.get("source_record_sha256")
    is_open = closes > checked_at
    # Checking only quoted fragments silently accepted changes to unquoted
    # conditions (salary, eligibility, duties, etc.). The reviewed record,
    # rather than the mutable feed as a whole, is the freshness boundary.
    reason = ("record_expired" if not is_open else "record_changed" if not unchanged
              else "excerpts_changed" if not excerpt_match else "verified_unchanged")
    return {"current": excerpt_match and is_open and unchanged, "reason": reason,
            "record_id": snapshot["source_record_id"],
            "record_sha256": digest, "record_unchanged": unchanged,
            "feed_sha256": hashlib.sha256(response.content).hexdigest(),
            "excerpts_match": excerpt_match, "closes_at": closes.isoformat(),
            "checked_at": checked_at.isoformat(), "open": is_open}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.snapshot.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))
    if not result.get("current"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
