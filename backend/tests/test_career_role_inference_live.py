"""Opt-in live HTTP dependency coverage for career direction inference.

CPA_TEST_LIVE_MYSQL=1 python -B -m pytest backend/tests/test_career_role_inference_live.py -q
The read-only test creates no business rows; failure arms replace one client only.
"""

import os
import threading
import uuid

import pytest
import requests
from flask import Flask
from werkzeug.serving import make_server

from app import create_app
from app.api import career_bp
from app.services import db as db_services


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL and Neo4j")
def test_http_inference_distinguishes_empty_and_partial_dependencies(monkeypatch):
    client = create_app().test_client()
    unknown = "__never_catalogued_" + uuid.uuid4().hex

    empty = client.post("/api/career/infer-role", json={"skills": [unknown]})
    assert empty.status_code == 200
    assert empty.json["success"] is True
    assert empty.json["status"] == empty.json["data"]["status"] == "empty"
    assert empty.json["data"]["failed_sources"] == []
    assert set(empty.json["data"]["source_coverage"].values()) == {"available"}

    class FailedNeo4j:
        def execute_query(self, *_args, **_kwargs):
            raise OSError("injected neo4j failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_services, "get_neo4j_client", lambda: FailedNeo4j())
        partial_graph = client.post("/api/career/infer-role", json={"skills": ["Python", "SQL"]})
    assert partial_graph.status_code == 200
    assert partial_graph.json["success"] is True
    assert partial_graph.json["status"] == "partial"
    assert partial_graph.json["data"]["failed_sources"] == ["neo4j_requires"]
    assert partial_graph.json["data"]["source_coverage"]["skill_taxonomy"] == "available"

    class FailedMySQL:
        def execute(self, *_args, **_kwargs):
            raise OSError("injected mysql failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_services, "get_mysql_client", lambda: FailedMySQL())
        partial_mysql = client.post("/api/career/infer-role", json={"skills": [unknown]})
    assert partial_mysql.status_code == 200
    assert partial_mysql.json["success"] is True
    assert partial_mysql.json["status"] == "partial"
    assert set(partial_mysql.json["data"]["failed_sources"]) == {
        "career_insights", "skill_taxonomy",
    }
    assert partial_mysql.json["data"]["next_action"] == "retry_when_sources_available"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL and Neo4j")
def test_real_socket_inference_withholds_seed_market_priors():
    """Production route + real read-only dependencies, without app-factory DDL."""
    mysql = db_services.get_mysql_client()
    seed = mysql.execute(
        "SELECT demand_level, entry_difficulty FROM career_insights "
        "WHERE role_name=:role LIMIT 1", {"role": "RAG工程师"},
    )
    assert seed and seed[0]["demand_level"] is not None

    app = Flask(__name__)
    app.register_blueprint(career_bp, url_prefix="/api/career")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.post(
            f"http://127.0.0.1:{server.server_port}/api/career/infer-role",
            json={"skills": ["RAG", "Python", "LangChain"], "top_k": 20},
            timeout=20,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert body["status"] == data["status"] == "partial"
        assert data["failed_sources"] == []
        assert set(data["source_coverage"].values()) == {"available"}
        assert "unverified_seed_catalogue" in data["partial_reasons"]
        assert data["market_data_status"] == "unverified_seed"
        assert data["predicted_role"]
        assert any("career_insights" in c["sources"] for c in data["candidates"])
        assert all(c["demand_level"] is None for c in data["candidates"])
        assert all("entry_difficulty" not in c for c in data["candidates"])
    finally:
        server.shutdown()
        thread.join(timeout=5)
