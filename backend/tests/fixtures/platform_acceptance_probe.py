"""Opt-in synthetic Ask HTTP probe and conservative acceptance metrics.

Never loads repository credentials or reads real resumes. Default usage is
offline metrics; --live is required before calling a configured local Ask API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import unicodedata
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def character_error_rate(reference, hypothesis, *, normalize=False):
    """Levenshtein CER; insertions can legitimately produce a rate above 1."""
    if normalize:
        reference = "".join(unicodedata.normalize("NFC", reference).split())
        hypothesis = "".join(unicodedata.normalize("NFC", hypothesis).split())
    if not reference:
        return {"status": "unscorable", "reason": "empty_human_reference", "cer": None}
    row = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        nxt = [i]
        for j, actual in enumerate(hypothesis, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (expected != actual)))
        row = nxt
    return {"status": "scored", "reference_chars": len(reference), "edits": row[-1],
            "cer": row[-1] / len(reference), "normalization": "NFC_no_whitespace" if normalize else "none"}


def score_page_cer_dirs(reference_dir, hypothesis_dir):
    """Compare matching human/OCR page_NNN.txt files without exposing text."""
    reference_files = {p.name: p for p in reference_dir.glob("page_*.txt")}
    hypothesis_files = {p.name: p for p in hypothesis_dir.glob("page_*.txt")}
    names = sorted(reference_files)
    if not names or set(names) != set(hypothesis_files):
        return {
            "status": "not_accepted", "reason": "page_manifest_mismatch",
            "reference_pages": len(reference_files), "hypothesis_pages": len(hypothesis_files),
        }
    pages = []
    totals = {"strict": {"reference_chars": 0, "edits": 0},
              "normalized": {"reference_chars": 0, "edits": 0}}
    for name in names:
        reference = reference_files[name].read_text(encoding="utf-8")
        hypothesis = hypothesis_files[name].read_text(encoding="utf-8")
        scores = {"strict": character_error_rate(reference, hypothesis),
                  "normalized": character_error_rate(reference, hypothesis, normalize=True)}
        if any(score["status"] != "scored" for score in scores.values()):
            return {"status": "not_accepted", "reason": "empty_human_reference", "page": name}
        pages.append({"page": name, **scores})
        for label, score in scores.items():
            totals[label]["reference_chars"] += score["reference_chars"]
            totals[label]["edits"] += score["edits"]
    for total in totals.values():
        total["cer"] = total["edits"] / total["reference_chars"]
    return {"status": "scored", "pages": pages, "aggregate": totals,
            "human_reference_required": True}


def audit_usage_ledger(ledger):
    """A turn is not budget-verified from one provider's last_usage alone."""
    calls = ledger.get("calls") or []
    expected = set(ledger.get("expected_call_ids") or [])
    actual = [call.get("call_id") for call in calls]
    issues = []
    if not expected or set(actual) != expected or len(actual) != len(set(actual)):
        issues.append("incomplete_or_duplicate_call_manifest")
    total = 0
    for call in calls:
        prefix = str(call.get("call_id", "unknown"))
        usage = call.get("usage") or {}
        if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
            issues.append(prefix + ":missing_provider_usage")
            continue
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            issues.append(prefix + ":inconsistent_provider_usage")
        total += usage["total_tokens"]
        preflight = call.get("preflight") or {}
        if preflight.get("fits") is not True or not preflight.get("final_messages_checked"):
            issues.append(prefix + ":unproven_final_message_budget")
        if not all(call.get(k) for k in ("stage", "provider", "model")):
            issues.append(prefix + ":missing_call_identity")
        if call.get("finish_reason") not in {"stop", "tool_calls"}:
            issues.append(prefix + ":nonfinal_or_truncated_completion")
    if calls and calls[-1].get("finish_reason") != "stop":
        issues.append("turn_has_no_final_completion")
    cap = ledger.get("turn_token_cap")
    if type(cap) is not int or cap <= 0:
        issues.append("missing_turn_cap")
    elif total > cap:
        issues.append("turn_cap_exceeded")
    return {"verified": not issues, "recorded_calls": len(calls), "recorded_total_tokens": total, "issues": issues}


def score_ask_contract(answer, golden):
    """Scores structured factual commitments, not overall career advice quality."""
    if not isinstance(answer, dict):
        return {"schema_valid": False, "contract_pass": False, "human_review_required": True}
    facts = golden["expected"]
    sources = answer.get("recommendation_sources")
    scores = {
        "education_fact": answer.get("education") == facts["education"],
        "last_page_used": answer.get("education_page") == facts["education_page"],
        "project_limit_acknowledged": answer.get("project_metrics_available") is False,
        "salary_not_invented": answer.get("salary_amount") is None and answer.get("salary_supported") is False,
        "cross_turn_city_preserved": answer.get("target_city") == facts["target_city"],
        "recommendation_sources_supported": isinstance(sources, list) and bool(sources)
            and all(source in golden["allowed_sources"] for source in sources),
        "advice_shape": isinstance(answer.get("advice"), list)
            and len(answer["advice"]) == 2
            and all(isinstance(item, str) and item.strip() for item in answer["advice"]),
    }
    required = set(facts) | {"project_metrics_available", "salary_amount", "salary_supported", "recommendation_sources", "advice"}
    return {"schema_valid": required <= set(answer), **scores,
            "contract_pass": required <= set(answer) and all(scores.values()), "human_review_required": True}


def run_live_ask(base_url, token, golden, *, dev_student_id=""):
    """Two HTTP turns; endpoint failures are not turned into model scores."""
    session_id = None
    hashes = []
    messages = [golden["initial_message"], golden["followup_message"]]
    for message in messages:
        payload = {"message": message, "interaction_mode": "ask", "engine": "supervisor"}
        if session_id:
            payload["session_id"] = session_id
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if dev_student_id:
            headers["X-Dev-Student-Id"] = dev_student_id
        req = Request(base_url.rstrip("/") + "/api/agent/chat", data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(req, timeout=120) as response:
                envelope = json.loads(response.read().decode())
        except HTTPError as exc:
            return {"status": "not_accepted", "reason": "http_error", "http_status": exc.code}
        except (URLError, TimeoutError):
            return {"status": "not_accepted", "reason": "endpoint_unavailable"}
        data = envelope.get("data") or {}
        if envelope.get("success") is not True or not data.get("session_id"):
            return {"status": "not_accepted", "reason": "missing_authoritative_session"}
        if session_id and session_id != data["session_id"]:
            return {"status": "not_accepted", "reason": "session_discontinuity"}
        session_id = data["session_id"]
        reply = str(data.get("reply") or data.get("response") or "")
        hashes.append(hashlib.sha256(reply.encode()).hexdigest())
    try:
        answer = json.loads(reply)
    except ValueError:
        answer = None
    return {"status": "observed", "reply_hashes": hashes, "turns": 2,
            "metrics": score_ask_contract(answer, golden), "provider_quality_accepted": False,
            "notes": "Synthetic inline text only; attachment parsing, real JD freshness and human quality scoring not covered."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ocr = sub.add_parser("ocr")
    ocr.add_argument("reference", type=Path)
    ocr.add_argument("hypothesis", type=Path)
    ocr_pages = sub.add_parser("ocr-pages")
    ocr_pages.add_argument("reference_dir", type=Path)
    ocr_pages.add_argument("hypothesis_dir", type=Path)
    ledger = sub.add_parser("budget")
    ledger.add_argument("ledger", type=Path)
    ask = sub.add_parser("ask")
    ask.add_argument("--live", action="store_true")
    ask.add_argument("--base-url", default="http://127.0.0.1:5001")
    ask.add_argument("--token-env", default="CPA_ACCEPTANCE_TOKEN")
    ask.add_argument("--dev-student-id-env", default="CPA_ACCEPTANCE_DEV_STUDENT_ID")
    args = parser.parse_args()
    if args.command == "ocr":
        reference, hypothesis = args.reference.read_text(encoding="utf-8"), args.hypothesis.read_text(encoding="utf-8")
        result = {"strict": character_error_rate(reference, hypothesis),
                  "normalized": character_error_rate(reference, hypothesis, normalize=True),
                  "human_reference_required": True}
    elif args.command == "ocr-pages":
        result = score_page_cer_dirs(args.reference_dir, args.hypothesis_dir)
    elif args.command == "budget":
        result = audit_usage_ledger(json.loads(args.ledger.read_text(encoding="utf-8")))
    elif not args.live:
        result = {"status": "not_run", "reason": "explicit_live_opt_in_required"}
    else:
        golden = json.loads(Path(__file__).with_name("platform_acceptance_golden.json").read_text(encoding="utf-8"))
        result = run_live_ask(
            args.base_url, os.getenv(args.token_env, ""), golden,
            dev_student_id=os.getenv(args.dev_student_id_env, ""),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
