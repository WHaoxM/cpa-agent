"""Opt-in real resume/report MySQL + model slice; does not certify model advice quality."""
import os

import pytest
from flask import g, request

from app import create_app
from app.config import Config


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_RESUME_DIAGNOSTIC") != "1",
                    reason="requires explicitly configured student_001 confirmed resume and live model")
def test_live_confirmed_resume_diagnostic(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def principal():
        owner = request.headers.get("X-Test-Owner", "student_001")
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    client = app.test_client()
    generated = client.post("/api/report/resume-diagnostic", json={"student_id": "someone_else"})
    assert generated.status_code == 200, generated.get_json()
    data = generated.get_json()["data"]
    assert data["student_id"] == "student_001"
    assert data["report_type"] == "resume_diagnostic"
    chapters = {chapter["id"]: chapter for chapter in data["chapters"]}
    assert len(chapters["experience"]["items"]) >= 4
    assert len(chapters["achievements"]["items"]) >= 10
    assert len(chapters["capabilities"]["items"]) >= 35
    assert "match_score" not in str(data)
    assert "topMatchScore" not in str(data)
    got = client.get("/api/report/resume-diagnostic/" + data["report_id"])
    assert got.status_code == 200
    assert got.get_json()["data"]["report_id"] == data["report_id"]
    other = client.get("/api/report/resume-diagnostic/" + data["report_id"],
                       headers={"X-Test-Owner": "another_student"})
    assert other.status_code == 404
    assert "chapters" not in other.get_data(as_text=True)
