"""Owner-bound pipeline status/event routes against live MySQL.

Opt-in: CPA_TEST_LIVE_MYSQL=1 python -B -m pytest backend/tests/test_pipeline_task_read_http_mysql.py -q
"""

import json
import os
import uuid

import pytest
from flask import g, request

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db.mysql_client import get_mysql_client


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_pipeline_status_and_events_http_are_owner_bound_and_projected(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = get_mysql_client()
    sid = "pipeline_read_" + uuid.uuid4().hex[:20]
    task_id = "task_" + uuid.uuid4().hex[:24]
    private_result = {"summary": "owner-only synthetic status", "private_marker": uuid.uuid4().hex}

    try:
        db.execute(
            "INSERT INTO pipeline_task "
            "(task_id,pipeline,status,progress,result_json,params_json,student_id,triggered_by) "
            "VALUES (:task,'L2_generate_portrait','pending',5,:result,:params,:sid,'acceptance')",
            {"task": task_id, "result": json.dumps(private_result),
             "params": json.dumps({"student_id": sid}), "sid": sid}, commit=True,
        )
        db.execute(
            "UPDATE pipeline_task SET status='running', progress=37 "
            "WHERE task_id=:task AND student_id=:sid",
            {"task": task_id, "sid": sid}, commit=True,
        )

        app = create_app()

        @app.before_request
        def bind_test_principal():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = Principal(owner, owner, "http-test")

        client = app.test_client()
        status_url = f"/api/pipeline/status/{task_id}"
        status = client.get(status_url, query_string={"student_id": "forged-owner"})
        assert status.status_code == 200
        assert status.json["success"] is True
        assert status.json["data"]["task_id"] == task_id
        assert status.json["data"]["pipeline"] == "L2_generate_portrait"
        assert status.json["data"]["status"] == "running"
        assert status.json["data"]["progress"] == 37
        assert status.json["data"]["result"] == private_result

        events_url = f"/api/pipeline/events/{task_id}"
        events = client.get(events_url, query_string={"student_id": "forged-owner"})
        assert events.status_code == 200
        event_data = events.json["data"]
        assert event_data["task"] == {
            "task_id": task_id, "pipeline": "L2_generate_portrait", "status": "running",
        }
        assert [event["status"] for event in event_data["events"]] == ["pending", "running"]
        assert event_data["cursor"] == event_data["events"][-1]["event_id"]
        assert event_data["has_more"] is False
        assert event_data["resume_state"] == "not_available"
        assert all(set(event) == {"event_id", "task_id", "type", "status", "occurred_at"}
                   and event["type"] == "pipeline.status" for event in event_data["events"])
        assert private_result["private_marker"] not in events.get_data(as_text=True)

        other_headers = {"X-Test-Owner": sid + "_other"}
        assert client.get(status_url, headers=other_headers).status_code == 404
        assert client.get(events_url, headers=other_headers).status_code == 404
        unknown = "task_unknown_" + uuid.uuid4().hex
        assert client.get(f"/api/pipeline/status/{unknown}").status_code == 404
        assert client.get(f"/api/pipeline/events/{unknown}").status_code == 404
        assert client.get(events_url, query_string={"cursor": "-1"}).status_code == 400
    finally:
        db.execute(
            "DELETE FROM pipeline_task_event WHERE task_id=:task AND student_id=:sid",
            {"task": task_id, "sid": sid}, commit=True,
        )
        db.execute(
            "DELETE FROM pipeline_task WHERE task_id=:task AND student_id=:sid",
            {"task": task_id, "sid": sid}, commit=True,
        )
        assert db.fetch_all(
            "SELECT COUNT(*) AS n FROM pipeline_task_event WHERE task_id=:task AND student_id=:sid",
            {"task": task_id, "sid": sid},
        ) == [{"n": 0}]
        assert db.fetch_all(
            "SELECT COUNT(*) AS n FROM pipeline_task WHERE task_id=:task AND student_id=:sid",
            {"task": task_id, "sid": sid},
        ) == [{"n": 0}]
