"""Live MySQL checks for owner-scoped profile reads and writes."""

import json
import uuid

from flask import Flask, g

from app.api import profile_bp
from app.api._helpers import Principal
from app.config import Config
from app.services.db import get_mysql_client


def _client(owner):
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")

    @app.before_request
    def identity():
        g.cpa_principal = Principal(f"test:{owner}", owner, "test")

    return app.test_client()


def test_profile_patch_rejects_string_boolean_without_mutating_owner_rows(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    mysql = get_mysql_client()
    owner = "profile_scope_" + uuid.uuid4().hex
    email = owner + "@example.test"
    try:
        mysql.execute_write(
            "INSERT INTO user_account (id, username, password_hash, name, email, phone, role) "
            "VALUES (:id, :username, 'test-hash', 'Original Account', :email, '13812345678', 'student')",
            {"id": owner, "username": owner, "email": email},
        )
        mysql.execute_write(
            "INSERT INTO student_profile (student_id, name, phone, email, profile_json) "
            "VALUES (:id, 'Original Resume', '13812345678', :email, :profile_json)",
            {
                "id": owner,
                "email": email,
                "profile_json": json.dumps({"name": "Original Resume"}),
            },
        )

        response = _client(owner).patch(
            f"/api/profile/student/{owner}",
            json={"name": "Unexpectedly Synced", "sync_student_profile": "false"},
        )

        assert response.status_code == 400
        assert response.get_json()["success"] is False
        account = mysql.execute(
            "SELECT name FROM user_account WHERE id=:id", {"id": owner}
        )[0]
        profile = mysql.execute(
            "SELECT name FROM student_profile WHERE student_id=:id", {"id": owner}
        )[0]
        assert account["name"] == "Original Account"
        assert profile["name"] == "Original Resume"
    finally:
        mysql.execute_write(
            "DELETE FROM student_profile WHERE student_id=:id", {"id": owner}
        )
        mysql.execute_write("DELETE FROM user_account WHERE id=:id", {"id": owner})


def test_profile_read_and_patch_are_principal_owner_scoped(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    mysql = get_mysql_client()
    suffix = uuid.uuid4().hex
    owner_a, owner_b = f"profile_a_{suffix}", f"profile_b_{suffix}"
    owners = (owner_a, owner_b)
    try:
        for owner in owners:
            mysql.execute_write(
                "INSERT INTO user_account (id, username, password_hash, name, role) "
                "VALUES (:id, :username, 'test-hash', 'Original', 'student')",
                {"id": owner, "username": owner},
            )
            mysql.execute_write(
                "INSERT INTO student_profile (student_id, name, profile_json) "
                "VALUES (:id, 'Resume Name', :profile_json)",
                {"id": owner, "profile_json": json.dumps({"name": "Resume Name"})},
            )

        a, b = _client(owner_a), _client(owner_b)
        assert a.get(f"/api/profile/student/{owner_a}/basic").status_code == 200
        assert a.get(f"/api/profile/student/{owner_b}/basic").status_code == 403
        assert b.get(f"/api/profile/student/{owner_b}/basic").status_code == 200
        assert a.patch(
            f"/api/profile/student/{owner_b}", json={"name": "attacker"}
        ).status_code == 403
        assert mysql.execute(
            "SELECT name FROM user_account WHERE id=:id", {"id": owner_b}
        )[0]["name"] == "Original"
    finally:
        for owner in owners:
            mysql.execute_write(
                "DELETE FROM student_profile WHERE student_id=:id", {"id": owner}
            )
            mysql.execute_write("DELETE FROM user_account WHERE id=:id", {"id": owner})
