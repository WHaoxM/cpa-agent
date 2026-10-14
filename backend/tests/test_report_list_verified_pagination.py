"""Strict report lists paginate the verified projection, not stale DB rows."""

from flask import Flask, g

from app.api import report as report_api
from app.api._helpers import Principal
from app.config import Config
from app.services.agent_runtime import match_report_resources


def test_report_list_filters_stale_before_paging_and_uses_verified_status(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)

    class Reports:
        def execute(self, sql, params):
            assert params == {"student_id": "owner"}
            assert "LIMIT" not in sql
            return [
                {"report_id": "stale", "status": "completed"},
                {"report_id": "current", "status": "completed"},
                {"report_id": "partial", "status": "completed"},
            ]

    monkeypatch.setattr(report_api, "get_mysql_client", lambda: Reports())
    observed = []

    def verify(_mysql, *, student_id, uri):
        assert student_id == "owner"
        observed.append(uri)
        return {
            "business_artifact_verified": not uri.endswith("/stale"),
            "status": "partial" if uri.endswith("/partial") else "completed",
        }

    monkeypatch.setattr(match_report_resources, "read_match_report_resource", verify)
    app = Flask(__name__)
    with app.test_request_context("/api/report/list?status=partial&limit=1"):
        g.cpa_principal = Principal("owner", "owner", "test")
        response = report_api.list_reports()
        body = response.get_json()
        assert body["total"] == body["count"] == 1
        assert [row["report_id"] for row in body["data"]] == ["partial"]
        assert body["data"][0]["status"] == "partial"

    with app.test_request_context("/api/report/list?limit=1&offset=1"):
        g.cpa_principal = Principal("owner", "owner", "test")
        body = report_api.list_reports().get_json()
        assert body["total"] == 2
        assert [row["report_id"] for row in body["data"]] == ["partial"]
    assert len(observed) == 6
