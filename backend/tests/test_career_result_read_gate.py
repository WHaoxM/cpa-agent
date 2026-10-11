"""Legacy HTTP detail endpoints must not bypass the shared source-version gate."""

from flask import g

from app import create_app
from app.api import match as match_api, report as report_api
from app.config import Config
from app.services.agent_runtime import match_report_resources


class DetailDB:
    def execute(self, sql, params=None):
        if "FROM match_result mr" in sql:
            return [{"id": 17, "student_id": "owner", "job_portrait_id": "job_1",
                     "total_score": 99, "result_json": {"total_score": 99}}]
        if "FROM career_report cr" in sql:
            return [{"report_id": "report_1", "student_id": "owner",
                     "job_portrait_id": "job_1", "status": "completed",
                     "report_json": {"private_legacy_text": "must not escape"}}]
        if "FROM career_report WHERE" in sql:
            return [{"report_id": "report_1", "status": "completed",
                     "html_path": "file:///private/stale-report.html"}]
        return []


def test_legacy_detail_routes_hide_stale_score_and_report(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = DetailDB()
    monkeypatch.setattr(match_api, "get_mysql_client", lambda: db)
    monkeypatch.setattr(report_api, "get_mysql_client", lambda: db)
    calls = []

    def stale(_mysql, *, student_id, uri, **_kwargs):
        calls.append((student_id, uri))
        return {"status": "not_available", "business_artifact_verified": False,
                "reason": "catalog_publication_unavailable", "next_action": "review_sources"}

    monkeypatch.setattr(match_report_resources, "read_match_report_resource", stale)
    app = create_app()

    @app.before_request
    def principal():
        g.cpa_principal = {"principal_id": "owner", "student_id": "owner"}

    client = app.test_client()
    match = client.get("/api/match/result/owner/job_1")
    report = client.get("/api/report/report_1")
    exported = client.get("/api/report/report_1/export")
    assert match.status_code == report.status_code == exported.status_code == 409
    assert "99" not in match.get_data(as_text=True)
    assert "must not escape" not in report.get_data(as_text=True)
    assert "stale-report.html" not in exported.get_data(as_text=True)
    assert calls == [("owner", "career://me/matches/17"),
                     ("owner", "career://me/reports/report_1"),
                     ("owner", "career://me/reports/report_1")]
