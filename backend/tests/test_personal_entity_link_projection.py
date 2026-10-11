"""Real MySQL+Neo4j opt-in for reviewed equivalent private graph links."""

import hashlib
import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import text

from app.api._helpers import Principal
from app.config import Config
from app.services import personal_entity_link_jobs as jobs
from app.services.personal_entity_links import query_reviewed_entity_links
from app.services.db import get_mysql_client, get_neo4j_client
from app.services.public_skill_graph import SOURCE_ID as PUBLIC_SOURCE
from app.services.resume_entity_review import write_entity_review
from app.services.tools.graph_query import GraphQueryTool


@pytest.fixture
def live(monkeypatch):
    if os.getenv("CPA_TEST_LIVE_ENTITY_LINK_GRAPH") != "1":
        pytest.skip("explicit real MySQL+Neo4j opt-in required")
    mysql, neo = get_mysql_client(), get_neo4j_client()
    assert mysql.test_connection() and neo.test_connection()
    assert neo.execute_query(
        "MATCH (s:PublicSkill {name:'Python'}) "
        "WHERE labels(s)=['PublicSkill'] AND s.source_id=$sid AND s.visibility='public' "
        "RETURN count(s) AS count", {"sid": PUBLIC_SOURCE}) == [{"count": 1}]
    from app.services.personal_graph_jobs import install_personal_graph_projection
    from app.services.pipeline_events import install_pipeline_event_journal
    from app.services.resume_entity_review import _SHA256
    install_pipeline_event_journal(mysql)
    install_personal_graph_projection(mysql)
    jobs.install_entity_link_projection(mysql)
    ddl = """CREATE TABLE IF NOT EXISTS resume_entity_review (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        student_id VARCHAR(64) NOT NULL, source_confirmation_task_id VARCHAR(64) NOT NULL,
        source_sha256 CHAR(64) NOT NULL, request_id VARCHAR(128) NOT NULL,
        payload_sha256 CHAR(64) NOT NULL, revision INT NOT NULL,
        reviews_json JSON NOT NULL, snapshot_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uk_entity_review_request (student_id, request_id),
        UNIQUE KEY uk_entity_review_revision (student_id, source_confirmation_task_id, revision)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    mysql.execute(ddl, commit=True)
    owner = "reviewgraph_" + uuid.uuid4().hex[:12]
    task = "task_" + uuid.uuid4().hex[:12]
    source_sha = hashlib.sha256("Python 项目".encode()).hexdigest()
    assert _SHA256.fullmatch(source_sha)
    linking = {"status": "proposed", "source_sha256": source_sha,
               "mentions": [{"name": "Python", "evidence": "Python"}],
               "candidates": [{"name": "Python", "category": "编程", "source_id": PUBLIC_SOURCE}],
               "decisions": [{"mention_index": 0, "decision": "equivalent",
                              "candidate_index": 0, "rationale": "同一技能"}]}
    candidate = {"skills": ["Python"], "entity_linking": linking}
    profile = {**candidate, "_resume_confirmation": {"student_id": owner, "task_id": task}}
    result = {"confirmed": True, "candidate_profile": candidate}
    monkeypatch.setattr(jobs.projection_task, "apply_async", lambda **kwargs: None)
    try:
        with mysql.engine.begin() as conn:
            conn.execute(text("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,progress,result_json,"
                              "triggered_by,created_at,updated_at) VALUES "
                              "(:tid,:sid,'L0_parse_resume','completed',100,:result,'api',NOW(),NOW())"),
                         {"tid": task, "sid": owner, "result": json.dumps(result, ensure_ascii=False)})
            conn.execute(text("INSERT INTO student_profile(student_id,profile_json,created_at,updated_at) "
                              "VALUES (:sid,:profile,NOW(),NOW())"),
                         {"sid": owner, "profile": json.dumps(profile, ensure_ascii=False)})
        yield mysql, neo, owner, task, source_sha
    finally:
        neo.execute_query("MATCH (n) WHERE (n:PersonalEntityMention OR n:PersonalGraphSnapshot "
                          "OR n:PersonalSkill) AND n.owner_student_id=$sid DETACH DELETE n",
                          {"sid": owner}, read_only=False)
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM personal_entity_link_projection WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM personal_graph_projection WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM resume_entity_review WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})


def _review(task, sha, action, revision):
    return {"source_confirmation_task_id": task, "source_sha256": sha,
            "request_id": "req_" + uuid.uuid4().hex, "expected_revision": revision,
            "reviews": [{"mention_index": 0, "action": action}]}


def test_worker_ready_replays_only_dedicated_graph_queue(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: object())
    monkeypatch.setattr(jobs, "replay_entity_link_projections", lambda _mysql, **kw:
                        (calls.append(kw) or {"has_more": False, "next_after_id": None}))
    jobs.replay_pending_entity_links_on_graph_worker_ready(SimpleNamespace(hostname="celery@host"))
    assert calls == []
    jobs.replay_pending_entity_links_on_graph_worker_ready(SimpleNamespace(hostname="cpa-graph@host"))
    assert calls == [{"limit": 50, "after_id": 0}]


def test_equivalent_published_private_and_reject_revokes(live, monkeypatch):
    mysql, neo, owner, task, sha = live
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    initial_public = neo.execute_query("MATCH (s:PublicSkill {name:'Python'}) RETURN count(s) AS count")
    first = write_entity_review(mysql, student_id=owner, payload=_review(task, sha, "accept", 0))
    assert first["revision"] == 1 and first["graph_publication_status"] == "queued"
    published = jobs.execute_entity_link_projection(student_id=owner, task_id=task,
        revision=1, mysql=mysql, neo4j=neo)
    assert published["status"] == "completed" and published["equivalent_link_count"] == 1
    rows = neo.execute_query(
        "MATCH (m:PersonalEntityMention)-[:REVIEWED_EQUIVALENT]->(s:PublicSkill) "
        "WHERE m.owner_student_id=$sid RETURN m.mention_index AS idx,s.name AS target, "
        "m.revision AS revision", {"sid": owner})
    assert rows == [{"idx": 0, "target": "Python", "revision": 1}]
    scoped = query_reviewed_entity_links(neo, owner_student_id=owner, mysql=mysql)
    assert scoped["status"] == "ready" and scoped["rows"][0]["canonical_name"] == "Python"
    assert query_reviewed_entity_links(neo, owner_student_id=owner + "_other", mysql=mysql)["status"] == "unavailable"
    with Flask(__name__).test_request_context():
        g.cpa_principal = Principal(owner, owner, "test")
        tool_result = json.loads(GraphQueryTool(neo).execute(
            query="我的已审阅技能实体关系", query_type="personal_entity_links",
            student_id=owner + "_other"))
        assert tool_result["status"] == "completed"
        assert "Python" in tool_result["message"]
        assert tool_result["review_revision"] == 1
    with Flask(__name__).test_request_context():
        g.cpa_principal = Principal(owner + "_other", owner + "_other", "test")
        assert json.loads(GraphQueryTool(neo).execute(
            query="我的已审阅技能实体关系", query_type="personal_entity_links"))["status"] == "unavailable"
    assert neo.execute_query("MATCH (m:PersonalEntityMention) WHERE m.owner_student_id=$sid "
                             "RETURN count(m) AS count", {"sid": owner + "_other"}) == [{"count": 0}]
    second = write_entity_review(mysql, student_id=owner, payload=_review(task, sha, "reject", 1))
    assert second["revision"] == 2
    revoked = jobs.execute_entity_link_projection(student_id=owner, task_id=task,
        revision=2, mysql=mysql, neo4j=neo)
    assert revoked["status"] == "completed" and revoked["equivalent_link_count"] == 0
    assert neo.execute_query("MATCH (m:PersonalEntityMention) WHERE m.owner_student_id=$sid "
                             "RETURN count(m) AS count", {"sid": owner}) == [{"count": 0}]
    assert query_reviewed_entity_links(neo, owner_student_id=owner, mysql=mysql)["rows"] == []
    with Flask(__name__).test_request_context():
        g.cpa_principal = Principal(owner, owner, "test")
        tool_result = json.loads(GraphQueryTool(neo).execute(
            query="我的已审阅技能实体关系", query_type="personal_entity_links"))
        assert tool_result["status"] == "completed" and tool_result["row_count"] == 0
        assert tool_result["review_revision"] == 2
    assert neo.execute_query("MATCH (s:PublicSkill {name:'Python'}) RETURN count(s) AS count") == initial_public
    assert [r["revision"] for r in mysql.execute(
        "SELECT revision FROM resume_entity_review WHERE student_id=:sid ORDER BY revision", {"sid": owner})] == [1, 2]


def test_source_revision_drift_supersedes_old_pending_job(live):
    mysql, neo, owner, task, sha = live
    write_entity_review(mysql, student_id=owner, payload=_review(task, sha, "accept", 0))
    write_entity_review(mysql, student_id=owner, payload=_review(task, sha, "defer", 1))
    stale = jobs.execute_entity_link_projection(student_id=owner, task_id=task,
        revision=1, mysql=mysql, neo4j=neo)
    assert stale["status"] == "superseded"
    current = jobs.execute_entity_link_projection(student_id=owner, task_id=task,
        revision=2, mysql=mysql, neo4j=neo)
    assert current["status"] == "completed" and current["equivalent_link_count"] == 0


def test_new_confirmation_supersedes_pending_old_review(live):
    mysql, neo, owner, task, sha = live
    write_entity_review(mysql, student_id=owner, payload=_review(task, sha, "accept", 0))
    next_task = "task_" + uuid.uuid4().hex[:12]
    candidate = {"skills": ["Rust"]}
    profile = {**candidate, "_resume_confirmation": {"student_id": owner, "task_id": next_task}}
    result = {"confirmed": True, "candidate_profile": candidate}
    try:
        with mysql.engine.begin() as conn:
            conn.execute(text("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,progress,result_json,"
                              "triggered_by,created_at,updated_at) VALUES "
                              "(:tid,:sid,'L0_parse_resume','completed',100,:result,'api',NOW(),NOW())"),
                         {"tid": next_task, "sid": owner, "result": json.dumps(result)})
            conn.execute(text("UPDATE student_profile SET profile_json=:profile WHERE student_id=:sid"),
                         {"sid": owner, "profile": json.dumps(profile)})
        stale = jobs.execute_entity_link_projection(student_id=owner, task_id=task,
            revision=1, mysql=mysql, neo4j=neo)
        assert stale["status"] == "superseded"
        assert neo.execute_query("MATCH (m:PersonalEntityMention) WHERE m.owner_student_id=$sid "
                                 "RETURN count(m) AS count", {"sid": owner}) == [{"count": 0}]
    finally:
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task WHERE task_id=:tid AND student_id=:sid"),
                         {"tid": next_task, "sid": owner})


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_ENTITY_LINK_QUEUE") != "1",
                    reason="explicit real Redis/Celery worker opt-in required")
def test_real_graph_worker_consumes_review_commit(live, monkeypatch):
    mysql, neo, owner, task, sha = live
    # The fixture suppresses delivery for deterministic direct-publication
    # tests. This opt-in case restores the real broker path before POST.
    monkeypatch.undo()
    result = write_entity_review(mysql, student_id=owner,
                                 payload=_review(task, sha, "accept", 0))
    assert result["graph_publication_status"] in {"queued", "completed"}
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = jobs.projection_state(mysql, student_id=owner, task_id=task, revision=1)
        if state and state["status"] == "completed":
            break
        time.sleep(0.5)
    assert state["status"] == "completed", state
    assert query_reviewed_entity_links(neo, owner_student_id=owner, mysql=mysql)["rows"][0]["canonical_name"] == "Python"
