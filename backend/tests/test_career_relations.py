"""Career relation projection contracts; sample data is test-only and synthetic."""
import hashlib

import pytest
from sqlalchemy import create_engine, text

from app.services import career_relations as relations


def _path(role_id, role_name, revision, skills):
    return {
        "role_id": role_id,
        "role_name": role_name,
        "revision": revision,
        "content_sha256": hashlib.sha256(revision.encode("utf-8")).hexdigest(),
        "skills": [{
            "skill_id": skill_id,
            "name": name,
            "evidence": {"url": f"https://example.org/{skill_id}", "note": f"Curated source for {name}."},
        } for skill_id, name in skills],
    }


@pytest.fixture
def pathways():
    return [
        _path("role-a", "Role A", "a-v1", [(1, "Python"), (2, "SQL")]),
        _path("role-b", "Role B", "b-v1", [(1, "Python"), (3, "Linux")]),
        _path("role-c", "Role C", "c-v1", [(4, "Design")]),
    ]


def test_shared_skill_edges_are_exact_explained_and_deterministic(pathways):
    result = relations.build_relation_snapshot(pathways)
    assert result["status"] == "ready"
    assert result["source_count"] == 3
    assert result["node_count"] == 3
    assert result["edge_count"] == 1
    edge = result["edges"][0]
    assert (edge["source"], edge["target"]) == ("role-a", "role-b")
    assert edge["relation_type"] == "shared_skill"
    assert edge["weight"] == pytest.approx(1 / 3)
    assert edge["shared_skills"][0]["name"] == "Python"
    assert edge["shared_skills"][0]["source_evidence"]["url"] == "https://example.org/1"
    assert "eligibility" not in relations.WEIGHT_DEFINITION
    assert relations.build_relation_snapshot(list(reversed(pathways))) == result


def test_empty_and_insufficient_source_sets_do_not_invent_edges(pathways):
    single = relations.build_relation_snapshot(pathways[:1])
    assert single["status"] == "empty"
    assert single["reason"] == "fewer_than_two_active_pathways"
    assert single["node_count"] == 1
    assert single["edges"] == []
    disjoint = relations.build_relation_snapshot([pathways[0], pathways[2]])
    assert disjoint["status"] == "empty"
    assert disjoint["reason"] == "no_shared_verified_skills"
    assert disjoint["edges"] == []


def test_active_revision_changes_the_input_fingerprint(pathways):
    before = relations.build_relation_snapshot(pathways)
    changed = [*pathways[:2], _path("role-c", "Role C", "c-v2", [(4, "Design")])]
    after = relations.build_relation_snapshot(changed)
    assert after["input_sha256"] != before["input_sha256"]
    assert after["edges"][0]["id"] != before["edges"][0]["id"]


@pytest.mark.parametrize("bad_skill", [
    {"skill_id": 0, "name": "Python", "evidence": {"url": "https://example.org", "note": "evidence"}},
    {"skill_id": 1, "name": "Python", "evidence": {"url": "http://example.org", "note": "evidence"}},
    {"skill_id": 1, "name": "Python", "evidence": {"url": "https://example.org", "note": " "}},
])
def test_invalid_identity_or_missing_evidence_fails_closed(pathways, bad_skill):
    pathways[0]["skills"] = [bad_skill]
    with pytest.raises(ValueError):
        relations.build_relation_snapshot(pathways)


class SQLiteMysql:
    def __init__(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE career_relation_run ("
                "run_id VARCHAR(64) PRIMARY KEY, algorithm_version VARCHAR(64), input_sha256 VARCHAR(64), source_manifest_json JSON, "
                "source_count INTEGER, node_count INTEGER, edge_count INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"))
            conn.execute(text("CREATE TABLE career_role_relation ("
                "edge_id VARCHAR(64) PRIMARY KEY, run_id VARCHAR(64), source_role_id VARCHAR(64), source_revision VARCHAR(64), "
                "source_sha256 VARCHAR(64), target_role_id VARCHAR(64), target_revision VARCHAR(64), target_sha256 VARCHAR(64), "
                "relation_type VARCHAR(24), weight NUMERIC, shared_skill_count INTEGER, union_skill_count INTEGER, "
                "shared_skill_ids_json JSON, evidence_json JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"))

    def execute_single(self, sql, parameters=None):
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), parameters or {}).mappings().first()
            return dict(row) if row else None

    def execute(self, sql, parameters=None):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), parameters or {}).mappings().all()]


def test_dry_run_is_read_only_and_apply_is_immutable_idempotent(monkeypatch, pathways):
    mysql = SQLiteMysql()
    monkeypatch.setattr(relations, "load_active_pathways", lambda _mysql: pathways)
    preview = relations.compute_and_persist(mysql)
    assert preview["status"] == "dry_run"
    assert preview["writes_performed"] is False
    assert preview["edge_count"] == 1
    assert mysql.execute("SELECT * FROM career_relation_run") == []

    applied = relations.compute_and_persist(mysql, apply=True)
    unchanged = relations.compute_and_persist(mysql, apply=True)
    assert applied["status"] == "applied"
    assert applied["writes_performed"] is True
    assert unchanged["status"] == "unchanged"
    assert mysql.execute("SELECT COUNT(*) AS count FROM career_relation_run")[0]["count"] == 1
    assert mysql.execute("SELECT COUNT(*) AS count FROM career_role_relation")[0]["count"] == 1
    graph = relations.read_current_graph(mysql)
    assert graph["status"] == "ready"
    assert graph["edges"][0]["shared_skills"][0]["name"] == "Python"

    pathways[2] = _path("role-c", "Role C", "c-v2", [(1, "Python"), (4, "Design")])
    incremental = relations.compute_and_persist(mysql, apply=True)
    assert incremental["status"] == "applied"
    assert incremental["incremental"] == {
        "mode": "incremental", "previous_run_id": applied["run_id"],
        "reused_edges": 1, "recomputed_pairs": 2,
    }
    graph = relations.read_current_graph(mysql)
    assert graph["edge_count"] == 3
    assert mysql.execute("SELECT COUNT(*) AS count FROM career_relation_run")[0]["count"] == 2
    assert mysql.execute("SELECT COUNT(*) AS count FROM career_role_relation")[0]["count"] == 4
    mysql.engine.dispose()


def test_read_graph_is_pending_when_the_current_fingerprint_has_no_projection(monkeypatch, pathways):
    mysql = SQLiteMysql()
    monkeypatch.setattr(relations, "load_active_pathways", lambda _mysql: pathways)
    graph = relations.read_current_graph(mysql)
    assert graph["status"] == "pending"
    assert graph["source_count"] == 3
    assert graph["nodes"]
    assert graph["edges"] == []
    mysql.engine.dispose()


def test_read_graph_rejects_an_edge_id_that_does_not_match_the_projection(monkeypatch, pathways):
    mysql = SQLiteMysql()
    monkeypatch.setattr(relations, "load_active_pathways", lambda _mysql: pathways)
    relations.compute_and_persist(mysql, apply=True)
    with mysql.engine.begin() as conn:
        conn.execute(text("UPDATE career_role_relation SET edge_id='tampered' WHERE run_id=:run_id"),
                     {"run_id": relations._source_fingerprint(pathways)})
    with pytest.raises(RuntimeError, match="incomplete or stale"):
        relations.read_current_graph(mysql)
    mysql.engine.dispose()


def test_public_route_is_read_only_and_returns_current_projection(monkeypatch, pathways):
    from flask import Flask
    from app.api import career_bp
    from app.api import career_pathway as api

    expected = relations.build_relation_snapshot(pathways)
    monkeypatch.setattr(api, "get_mysql_client", lambda: object())
    monkeypatch.setattr(api, "read_current_graph", lambda _mysql: expected)
    app = Flask(__name__)
    app.register_blueprint(career_bp, url_prefix="/api/career")
    client = app.test_client()
    response = client.get("/api/career/relationship-graph")
    assert response.status_code == 200
    assert response.json["data"]["edge_count"] == 1
    assert client.post("/api/career/relationship-graph", json={}).status_code == 405
