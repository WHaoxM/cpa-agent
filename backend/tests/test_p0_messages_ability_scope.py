"""HTTP + live MySQL/Neo4j checks for legacy messages and public ability graph."""

import uuid

from flask import Flask, g

from app.api import ability_bp, messages_bp
from app.api._helpers import Principal
from app.config import Config
from app.repositories.messages_repo import messages_repo
from app.services.ability_graph_service import clear_role_graph, import_role_payload
from app.services.db import get_mysql_client


def _client(monkeypatch, owner="owner_a", auth=True):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(messages_bp, url_prefix="/api")
    app.register_blueprint(ability_bp, url_prefix="/api")
    if auth:
        @app.before_request
        def identity():
            g.cpa_principal = Principal(f"auth:{owner}", owner, "test")
    return app.test_client()


def test_messages_live_owner_state_and_verified_mutations(monkeypatch):
    mysql = get_mysql_client()
    messages_repo.ensure_schema()
    mid = "p0_" + uuid.uuid4().hex
    owner_a, owner_b = "p0_a_" + mid, "p0_b_" + mid
    mysql.execute_write(
        "INSERT INTO inbox_message (id, title, body, type) VALUES (:id, 'notice', 'body', 'test')",
        {"id": mid},
    )
    try:
        assert _client(monkeypatch, auth=False).get("/api/messages").status_code == 401
        a = _client(monkeypatch, owner_a)
        b = _client(monkeypatch, owner_b)
        assert any(row["id"] == mid for row in a.get("/api/messages?user_id=" + owner_b).json["data"])
        assert a.post(f"/api/messages/{mid}/read", json={"user_id": owner_b}).status_code == 200
        assert next(row for row in a.get("/api/messages").json["data"] if row["id"] == mid)["read"] is True
        assert next(row for row in b.get("/api/messages").json["data"] if row["id"] == mid)["read"] is False
        assert a.delete(f"/api/messages/{mid}").status_code == 200
        assert all(row["id"] != mid for row in a.get("/api/messages").json["data"])
        assert any(row["id"] == mid for row in b.get("/api/messages").json["data"])
        assert a.post(f"/api/messages/{mid}/read").status_code == 503
        assert all(row["id"] != mid for row in a.get("/api/messages").json["data"])
        assert b.post("/api/messages/not_real/read").status_code == 404
    finally:
        mysql.execute_write("DELETE FROM inbox_message_user_state WHERE message_id = :id", {"id": mid})
        mysql.execute_write("DELETE FROM inbox_message WHERE id = :id", {"id": mid})


def test_ability_public_role_graph_rejects_cross_role_edge(monkeypatch):
    role = "p0_ability_" + uuid.uuid4().hex
    other = role + "_other"
    payload = {
        "aliases": ["alias_" + role],
        "nodes": [{"id": "a", "name": "public A", "level": 1}],
        "edges": [],
    }
    try:
        import_role_payload(role, payload)
        import_role_payload(other, {"nodes": [{"id": "b", "name": "other B", "level": 1}]})
        from app.services.db import get_neo4j_client
        neo4j = get_neo4j_client()
        neo4j.execute_query(
            "MATCH (a:AbilityNode {role:$role,id:'a'}), (b:AbilityNode {role:$other,id:'b'}) "
            "MERGE (a)-[:RELATES_TO {role:$role, relation:'noise'}]->(b)",
            {"role": role, "other": other}, read_only=False,
        )
        c = _client(monkeypatch, auth=False)
        graph = c.get("/api/ability-graph", query_string={"role": role})
        assert graph.status_code == 200
        assert graph.json["data"]["nodes"][0]["id"] == "a"
        assert graph.json["data"]["edges"] == []
        assert c.get("/api/ability-graph", query_string={"role": "alias_" + role}).status_code == 200
        assert c.get("/api/ability-graph", query_string={"role": role[:10]}).status_code == 404
    finally:
        clear_role_graph(role)
        clear_role_graph(other)
