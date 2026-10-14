"""A blocked L4 task must expose its persisted reason, not just a generic message."""

import uuid

from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


def test_report_generate_preserves_blocked_task_reason(monkeypatch):
    from app.api import report
    from app.services import pipeline

    class FakeRunner:
        def run(self, *args, **kwargs):
            return {"task_id": "task_blocked_fixture", "status": "partial",
                    "report_id": None, "reason": "confirmed_portrait_unavailable",
                    "next_action": "generate_portrait",
                    "summary": "缺少与当前简历一致的画像"}

    class FakeMysql:
        def execute(self, sql, params):
            assert params["task_id"] == "task_blocked_fixture"
            assert params["student_id"] == "owner_fixture"
            return [{"status": "partial"}]

    monkeypatch.setattr(pipeline, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(report, "get_mysql_client", lambda: FakeMysql())
    config = type("ReportPartialConfig", (Config,), {
        "SECRET_KEY": "report-partial-test-secret-with-32-bytes",
        "DEMO_MODE": False,
        "RATELIMIT_KEY_PREFIX": f"report-partial-{uuid.uuid4().hex}",
    })
    app = create_app(config)

    @app.before_request
    def test_identity():
        g.cpa_principal = Principal("fixture_account", "owner_fixture", "test")

    response = app.test_client().post("/api/report/generate", json={
        "job_portrait_id": "published_fixture"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is False and body["status"] == "partial"
    assert body["data"]["report_id"] is None
    assert body["data"]["reason"] == "confirmed_portrait_unavailable"
    assert body["data"]["next_action"] == "generate_portrait"
