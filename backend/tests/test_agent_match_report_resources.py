"""Pipeline writes -> owner-scoped resource projections, without new scoring."""

import json
import os

import pytest

from app.services import pipeline
from app.services.agent_runtime import match_report_resources as resources
from backend.tests.test_verified_match_report import scored  # reusable real pipeline/fake SQL fixture


@pytest.fixture
def repo(scored, monkeypatch):
    original = scored.fetch_all

    def fetch(sql, params=None):
        params = params or {}
        for table, attr, key in (("match_result", "match", "id"), ("career_report", "report", "report_id")):
            if f"FROM {table}" in sql and ("COUNT(*)" in sql or "SELECT *" in sql):
                row = getattr(scored, attr)
                rows = [row] if row and row["student_id"] == params["sid"] else []
                if "COUNT(*)" in sql:
                    return [{"total": len(rows)}]
                if "rid" in params:
                    return [r for r in rows if str(r[key]) == params["rid"]]
                return rows[params["offset"]:params["offset"] + params["limit"]]
        if "FROM pipeline_task" in sql and "task_id=:task_id" not in sql:
            rows = [row for row in scored.tasks.values() if row["student_id"] == params["sid"]]
            if "tid" in params:
                rows = [r for r in rows if r["task_id"] == params["tid"]]
            elif "rid" in params:
                rows = [r for r in rows if r["pipeline"] == "L4_generate_report"
                        and resources._object(r.get("result_json")).get("report_id") == params["rid"]]
            else:
                rows = [r for r in rows if r["pipeline"] == params["pipeline"]]
            return list(reversed(rows))[:1]
        return original(sql, params)

    monkeypatch.setattr(scored, "fetch_all", fetch)
    return scored


def _produce(repo):
    runner = pipeline.PipelineRunner()
    match = runner.run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    report = runner.run("L4_generate_report", {"student_id": "owner", "job_id": "job_1"})
    # The shared lightweight DB fixture omits this real SQL column.
    repo.report["job_portrait_id"] = "job_1"
    return match, report


def test_pipeline_saved_match_report_readback_is_partial_and_paged(repo):
    match, report = _produce(repo)
    catalog = resources.list_match_report_resources(repo, student_id="owner")
    assert [item["count"] for item in catalog["resources"]] == [1, 1]
    assert all(item["business_artifact_verified"] is False for item in catalog["resources"])
    for uri in (f"{resources.MATCHES_URI}/{match['match_id']}", f"{resources.REPORTS_URI}/{report['report_id']}"):
        value = resources.read_match_report_resource(repo, student_id="owner", uri=uri)
        assert value["status"] == "partial"
        assert value["business_artifact_verified"] is True
        assert value["data"]["total_score"] == 99
        assert value["data"]["d1_pass"] is False
        assert value["data"]["score_kind"] == "policy_estimate"
        assert "profile_snapshot" not in json.dumps(value)
        first = resources.read_match_report_resource(repo, student_id="owner", uri=uri, section="sources")
        second = resources.read_match_report_resource(repo, student_id="owner", uri=uri, section="sources", offset=3)
        assert first["coverage"]["next_offset"] == 3
        assert second["coverage"]["complete"] is True
        assert len(first["data"] + second["data"]) == 6
    listed = resources.read_match_report_resource(repo, student_id="owner", uri=resources.MATCHES_URI, section="items")
    assert listed["status"] == "partial" and listed["coverage"]["complete"]
    assert listed["data"][0]["resource_id"] == match["match_id"]
    assert resources.read_match_report_resource(repo, student_id="owner", uri=resources.MATCHES_URI)["data"]["latest_task"]["business_artifact_verified"] is False


def test_empty_catalog_does_not_turn_blocked_task_into_match(repo):
    repo.publication = None
    pipeline.PipelineRunner().run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    value = resources.read_match_report_resource(repo, student_id="owner", uri=resources.MATCHES_URI)
    assert value["status"] == "partial" and value["data"]["count"] == 0
    assert value["data"]["latest_task"]["reason"] == "catalog_publication_unavailable"
    assert value["business_artifact_verified"] is False
    assert resources.read_match_report_resource(repo, student_id="owner", uri=resources.MATCHES_URI, section="items")["data"] == []


@pytest.mark.parametrize("fault", ["digest", "task_failed", "publication_version", "publication_revoked", "portrait", "match_owner"])
def test_source_or_snapshot_fault_never_exposes_scores(repo, fault):
    match, report = _produce(repo)
    if fault == "digest": repo.match["result_json"]["total_score"] = 100
    elif fault == "task_failed": repo.tasks[match["task_id"]]["status"] = "failed"
    elif fault == "publication_version": repo.publication["publication_version"] = "v2"
    elif fault == "publication_revoked": repo.publication["status"] = "revoked"
    elif fault == "portrait": repo.portrait = None
    elif fault == "match_owner": repo.match["result_json"]["student_id"] = "other"
    value = resources.read_match_report_resource(repo, student_id="owner", uri=f"{resources.MATCHES_URI}/{match['match_id']}")
    assert value["status"] == "not_available"
    assert value["business_artifact_verified"] is False and "data" not in value
    if fault not in {"digest", "match_owner"}:
        report_value = resources.read_match_report_resource(repo, student_id="owner", uri=f"{resources.REPORTS_URI}/{report['report_id']}")
        assert report_value["status"] == "not_available" and "data" not in report_value


def test_report_snapshot_is_immutable_and_legacy_report_not_trusted(repo):
    _, report = _produce(repo)
    uri = f"{resources.REPORTS_URI}/{report['report_id']}"
    repo.match["result_json"]["total_score"] = 100
    assert resources.read_match_report_resource(repo, student_id="owner", uri=uri)["data"]["total_score"] == 99
    repo.report["report_json"]["match_snapshot"]["total_score"] = 80
    assert resources.read_match_report_resource(repo, student_id="owner", uri=uri)["status"] == "not_available"
    repo.report["report_json"] = {"content": "legacy text"}
    assert resources.read_match_report_resource(repo, student_id="owner", uri=uri)["status"] == "not_available"


def test_cross_owner_and_uri_validation(repo):
    match, report = _produce(repo)
    assert all(r["count"] == 0 for r in resources.list_match_report_resources(repo, student_id="other")["resources"])
    for uri in (f"{resources.MATCHES_URI}/{match['match_id']}", f"{resources.REPORTS_URI}/{report['report_id']}"):
        with pytest.raises(LookupError):
            resources.read_match_report_resource(repo, student_id="other", uri=uri)
    with pytest.raises(PermissionError):
        resources.list_match_report_resources(repo, student_id="")
    for uri in ("career://other/reports", "career://me/reports/../1", "career://me/reports/1/extra",
                "career://me/reports?owner=other", "career://me/matches/7foo", "career://me/matches/07"):
        assert not resources.handles_uri(uri)
        with pytest.raises(ValueError):
            resources.read_match_report_resource(repo, student_id="owner", uri=uri)
    with pytest.raises(ValueError):
        resources.read_match_report_resource(repo, student_id="owner", uri=resources.MATCHES_URI, limit=True)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_live_resource_reads_share_pipeline_records_and_revocation(monkeypatch):
    # Reuse the existing isolated SQL roundtrip and its exact-owner cleanup;
    # observe immediately after each committed pipeline result, before cleanup.
    from backend.tests.test_verified_match_report_mysql import test_live_publication_match_report_roundtrip
    original = pipeline.PipelineRunner.run
    observed = []
    saved_uris = []

    def run(runner, level, params):
        result = original(runner, level, params)
        sid = params["student_id"]
        if result.get("verified"):
            kind = "matches" if level == "L3_run_match" else "reports"
            rid = result["match_id" if kind == "matches" else "report_id"]
            uri = f"career://me/{kind}/{rid}"
            value = resources.read_match_report_resource(runner.mysql, student_id=sid, uri=uri)
            assert value["status"] == "partial" and value["business_artifact_verified"]
            assert value["data"]["d1_pass"] is False
            listing = resources.read_match_report_resource(runner.mysql, student_id=sid,
                                                          uri=f"career://me/{kind}", section="items")
            assert listing["data"][0]["resource_id"] == rid
            sources = resources.read_match_report_resource(runner.mysql, student_id=sid,
                                                          uri=uri, section="sources", offset=3)
            assert sources["coverage"]["returned"] == 3
            with pytest.raises(LookupError):
                resources.read_match_report_resource(runner.mysql, student_id=sid + "_other", uri=uri)
            saved_uris.append(uri)
            observed.append(kind)
        elif result.get("reason") == "catalog_publication_unavailable":
            for uri in saved_uris:
                value = resources.read_match_report_resource(runner.mysql, student_id=sid, uri=uri)
                assert value["status"] == "not_available" and "data" not in value
            observed.append("revoked")
        return result

    monkeypatch.setattr(pipeline.PipelineRunner, "run", run)
    test_live_publication_match_report_roundtrip(monkeypatch)
    # The roundtrip rejects both L3 and L4 after publication revocation.
    assert observed == ["matches", "reports", "revoked", "revoked"]
