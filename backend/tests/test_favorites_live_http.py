"""Opt-in-by-environment live HTTP + MySQL acceptance for secondary favorites routes.

Requires the README's local MySQL container at localhost:13306. Test rows use a
unique owner and are deleted without touching pre-existing user data.
"""

import json
import threading
import uuid
from urllib.request import Request, urlopen

from werkzeug.serving import make_server

from app import create_app
from app.config import Config
from app.services.db import get_mysql_client


def test_saved_job_and_target_role_authoritative_http_round_trip(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    suffix = uuid.uuid4().hex[:12]
    owner = f"accept_fav_{suffix}"
    other = f"accept_other_{suffix}"
    job_id = f"job_{suffix}"
    role_name = f"Role_{suffix}"
    mysql = get_mysql_client()
    app = create_app()
    server = make_server("127.0.0.1", 0, app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    def request_json(method, path, student_id, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-Dev-Student-Id": student_id}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    try:
        status, saved = request_json(
            "POST", "/api/saved-jobs", owner,
            {"job_id": job_id, "job_title": "Acceptance Job", "required_skills": ["Python"]},
        )
        assert status == 200 and saved["success"] is True
        rows = mysql.execute(
            "SELECT id, user_id FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        )
        assert len(rows) == 1
        assert saved["data"]["saved_job_id"] == rows[0]["id"]
        assert rows[0]["user_id"] == owner

        status, own_jobs = request_json("GET", "/api/saved-jobs", owner)
        assert status == 200 and any(row["job_id"] == job_id for row in own_jobs["data"])
        status, foreign_jobs = request_json("GET", "/api/saved-jobs", other)
        assert status == 200 and not any(row["job_id"] == job_id for row in foreign_jobs["data"])

        status, removed = request_json("DELETE", f"/api/saved-jobs/{job_id}", owner)
        assert status == 200 and removed["success"] is True
        assert mysql.execute(
            "SELECT id FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        ) == []

        status, added = request_json(
            "POST", "/api/target-roles", owner, {"role_name": role_name, "priority": 1},
        )
        assert status == 200 and added["success"] is True
        rows = mysql.execute(
            "SELECT id, user_id FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        )
        assert len(rows) == 1
        assert added["data"]["target_role_id"] == rows[0]["id"]
        assert rows[0]["user_id"] == owner

        status, own_roles = request_json("GET", "/api/target-roles", owner)
        assert status == 200 and any(row["role_name"] == role_name for row in own_roles["data"])
        status, foreign_roles = request_json("GET", "/api/target-roles", other)
        assert status == 200 and not any(row["role_name"] == role_name for row in foreign_roles["data"])

        status, removed = request_json("DELETE", f"/api/target-roles/{role_name}", owner)
        assert status == 200 and removed["success"] is True
        assert mysql.execute(
            "SELECT id FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        ) == []
    finally:
        mysql.execute_write(
            "DELETE FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        )
        mysql.execute_write(
            "DELETE FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        )
        server.shutdown()
        worker.join(timeout=5)
