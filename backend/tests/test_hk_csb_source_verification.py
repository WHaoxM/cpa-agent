"""Official-source freshness contract; network fixtures are explicitly synthetic."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.job_publication import content_sha256
from backend.scripts.mysql import verify_hk_csb_source as probe


@pytest.fixture
def source(monkeypatch):
    path = Path(__file__).parent / "fixtures/reviewed_sources/hk_csb_50368_snapshot.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    record = {"jobid": 50368, "jobname": snapshot["position_name"],
              "entreq": snapshot["source_text"], "enddate": "2099-12-31 23:59:00"}
    snapshot["source_record_sha256"] = content_sha256(record)
    rows = [record]

    def get(url, timeout):
        assert url == probe.FEED_URI and timeout == 20
        return SimpleNamespace(content=json.dumps({"common": [{"vacancies": rows}]}).encode(),
                               raise_for_status=lambda: None)

    monkeypatch.setattr(probe.requests, "get", get)
    return snapshot, record, rows


def test_unchanged_record_is_current_even_when_unrelated_feed_changes(source):
    snapshot, _, rows = source
    rows.append({"jobid": 99999, "jobname": "Unrelated test-only record"})
    result = probe.verify(snapshot)
    assert result["current"] is True
    assert result["reason"] == "verified_unchanged"
    assert result["closes_at"].endswith("+08:00")


def test_unquoted_requirement_change_requires_rereview(source):
    snapshot, record, _ = source
    record["salary"] = "Changed test-only salary"
    result = probe.verify(snapshot)
    assert result["excerpts_match"] is True
    assert result["open"] is True
    assert result["record_unchanged"] is False
    assert result["current"] is False
    assert result["reason"] == "record_changed"


@pytest.mark.parametrize("fault,reason", [("missing", "record_missing"),
                                         ("duplicate", "record_ambiguous"),
                                         ("deadline", "closing_time_unknown")])
def test_missing_ambiguous_or_undated_record_fails_closed(source, fault, reason):
    snapshot, record, rows = source
    if fault == "missing":
        rows.clear()
    elif fault == "duplicate":
        rows.append(copy.deepcopy(record))
    else:
        record["enddate"] = ""
    result = probe.verify(snapshot)
    assert result["current"] is False and result["reason"] == reason


def test_expiry_includes_exact_deadline_and_preserves_explicit_timezone(source):
    snapshot, record, _ = source
    record["enddate"] = "2099-12-31T23:59:00+00:00"
    snapshot["source_record_sha256"] = content_sha256(record)
    result = probe.verify(snapshot, now=datetime(2099, 12, 31, 23, 58, tzinfo=timezone.utc))
    assert result["current"] is True  # must not overwrite UTC with Hong Kong
    result = probe.verify(snapshot, now=datetime(2099, 12, 31, 23, 59, tzinfo=timezone.utc))
    assert result["current"] is False and result["reason"] == "record_expired"


def test_cli_changed_record_exits_nonzero(source, monkeypatch, capsys):
    snapshot, record, _ = source
    record["salary"] = "Changed test-only salary"
    monkeypatch.setattr(probe.sys, "argv", ["verify_hk_csb_source.py", "unused.json"])
    monkeypatch.setattr(probe.Path, "read_text", lambda *args, **kwargs: json.dumps(snapshot))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "record_changed"
