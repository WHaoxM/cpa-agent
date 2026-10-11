"""Confirmed MySQL profile -> transactional Neo4j projection -> main tool."""

import json
import os
import uuid
from contextlib import contextmanager

import pytest
from flask import Flask, g

from app.api._helpers import Principal
from app.config import Config
from app.services import personal_graph as pg


class SQL:
    def __init__(self, owner="owner", skills=None, confirmed=True):
        self.owner = owner
        self.skills = ["Python", {"name": "SQL"}] if skills is None else skills
        self.confirmed = confirmed

    def fetch_all(self, sql, params):
        candidate = {"skills": self.skills}
        if "FROM student_profile" in sql:
            return [{"profile_json": {**candidate, "_resume_confirmation": {"student_id": self.owner, "task_id": "task"}}}]
        return [{"task_id": "task", "student_id": self.owner, "pipeline": "L0_parse_resume", "status": "completed",
                 "result_json": {"confirmed": self.confirmed, "candidate_profile": candidate}}]


def test_projection_uses_confirmed_fields_and_stable_owner_bound_identity():
    first = pg.confirmed_projection(SQL(), "owner")
    assert first == pg.confirmed_projection(SQL(), "owner")
    assert first["skills"] == [{"skill_id": "0", "name": "Python", "evidence": "已确认简历 skills[0]"},
                               {"skill_id": "1", "name": "SQL", "evidence": "已确认简历 skills[1].name"}]
    assert first["snapshot_id"] != pg.confirmed_projection(SQL(owner="other"), "other")["snapshot_id"]
    assert first["snapshot_id"] != pg.confirmed_projection(SQL(skills=["Rust"]), "owner")["snapshot_id"]
    with pytest.raises(ValueError):
        pg.confirmed_projection(SQL(), "victim")
    with pytest.raises(ValueError):
        pg.confirmed_projection(SQL(confirmed=False), "owner")


@pytest.mark.parametrize("skills", [[None], [{"category": "missing name"}], [""], ["x" * 161], ["Python"] * 501])
def test_invalid_confirmed_skill_does_not_silently_partially_publish(skills):
    with pytest.raises(ValueError):
        pg.confirmed_projection(SQL(skills=skills), "owner")


def test_old_graph_is_not_queried_when_confirmation_is_revoked():
    class Never:
        def execute_query(self, *args, **kwargs):
            raise AssertionError("graph should not be queried")
    result = pg.query_personal_skills(Never(), owner_student_id="owner", mysql=SQL(confirmed=False))
    assert result["status"] == "unavailable"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_PERSONAL_GRAPH") != "1", reason="explicit real MySQL/Neo4j opt-in required")
def test_live_publication_replay_repair_rollback_version_revocation_and_owner(monkeypatch):
    from app.services.db import get_mysql_client, get_neo4j_client
    from app.services.tools.graph_query import GraphQueryTool
    from sqlalchemy import text

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    mysql, neo = get_mysql_client(), get_neo4j_client()
    owner = "pgpub_" + uuid.uuid4().hex[:16]
    victim = owner + "_v"
    first_task, next_task = "task_" + uuid.uuid4().hex[:16], "task_" + uuid.uuid4().hex[:16]

    def source(task, skills, sid=owner):
        candidate = {"skills": skills}
        profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": task}}
        result = {"confirmed": True, "candidate_profile": candidate}
        with mysql.engine.begin() as conn:
            conn.execute(text("INSERT INTO pipeline_task(task_id, student_id, pipeline, status, result_json) "
                              "VALUES (:tid,:sid,'L0_parse_resume','completed',:result) "
                              "ON DUPLICATE KEY UPDATE result_json=:result,status='completed'"),
                         {"tid": task, "sid": sid, "result": json.dumps(result)})
            conn.execute(text("INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile) "
                              "ON DUPLICATE KEY UPDATE profile_json=:profile"), {"sid": sid, "profile": json.dumps(profile)})

    class FailingClient:
        def execute_query(self, *args, **kwargs):
            return neo.execute_query(*args, **kwargs)

        @contextmanager
        def session(self):
            with neo.session() as actual:
                class Session:
                    def execute_write(self, callback):
                        def fail_mid_transaction(tx):
                            class Tx:
                                def run(self, query, *args, **kwargs):
                                    if query == pg.INTEGRITY_QUERY:
                                        raise RuntimeError("injected failure after all writes, before verify")
                                    return tx.run(query, *args, **kwargs)
                            return callback(Tx())
                        return actual.execute_write(fail_mid_transaction)
                yield Session()

    try:
        source(first_task, ["Python", "Neo4j"])
        first = pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=owner)
        assert first["skill_count"] == 2
        replay = pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=owner)
        assert replay == first
        rows = neo.execute_query("MATCH (s:PersonalSkill {snapshot_id:$id}) RETURN count(s) AS count", {"id": first["snapshot_id"]})
        assert rows == [{"count": 2}]
        result = pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)
        assert [r["name"] for r in result["rows"]] == ["Neo4j", "Python"]
        with Flask(__name__).test_request_context():
            g.cpa_principal = Principal(owner, owner, "test")
            result = json.loads(GraphQueryTool(neo).execute(query="我的技能", query_type="personal_skills", student_id=victim))
            assert result["status"] == "completed" and "Python" in result["message"]

        # Failure after destructive repair statements must restore prior snapshot.
        with pytest.raises(RuntimeError, match="injected failure"):
            pg.publish_confirmed_personal_graph(mysql, FailingClient(), owner_student_id=owner)
        assert len(pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"]) == 2

        # A conflicting owner is not silently repaired by deleting their node.
        neo.execute_query("MATCH (s:PersonalSkill {snapshot_id:$id,skill_id:'0'}) SET s.owner_student_id=$victim",
                          {"id": first["snapshot_id"], "victim": victim}, read_only=False)
        with pytest.raises(ValueError, match="归属冲突"):
            pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=owner)
        assert neo.execute_query("MATCH (s:PersonalSkill {snapshot_id:$id,skill_id:'0'}) RETURN s.owner_student_id AS owner",
                                 {"id": first["snapshot_id"]}) == [{"owner": victim}]
        neo.execute_query("MATCH (s:PersonalSkill {snapshot_id:$id,skill_id:'0'}) SET s.owner_student_id=$owner",
                          {"id": first["snapshot_id"], "owner": owner}, read_only=False)

        # Persisted partial legacy attempt is not reported as complete; replay repairs.
        neo.execute_query("MATCH (s:PersonalSkill {snapshot_id:$id,skill_id:'0'}) DETACH DELETE s", {"id": first["snapshot_id"]}, read_only=False)
        assert pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["status"] == "unavailable"
        assert pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=owner) == first
        assert len(pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"]) == 2

        source(next_task, ["Rust"])
        assert pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["status"] == "unavailable"
        second = pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=owner)
        assert second["snapshot_id"] != first["snapshot_id"]
        assert [r["name"] for r in pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"]] == ["Rust"]
        assert pg.query_personal_skills(neo, mysql=mysql, owner_student_id=victim)["status"] == "unavailable"

        # Claimed confirmation task belongs to owner, not victim: publisher fails.
        mysql.execute("INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)",
                      {"sid": victim, "profile": json.dumps({"skills": ["Rust"], "_resume_confirmation": {"student_id": victim, "task_id": next_task}})}, commit=True)
        with pytest.raises(ValueError):
            pg.publish_confirmed_personal_graph(mysql, neo, owner_student_id=victim)
        assert neo.execute_query("MATCH (p:PersonalGraphSnapshot {owner_student_id:$sid}) RETURN count(p) AS count", {"sid": victim}) == [{"count": 0}]

        mysql.execute("UPDATE pipeline_task SET status='failed' WHERE task_id=:tid AND student_id=:sid", {"tid": next_task, "sid": owner}, commit=True)
        assert pg.query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["status"] == "unavailable"
    finally:
        neo.execute_query("MATCH (n) WHERE (n:PersonalGraphSnapshot OR n:PersonalSkill) AND n.owner_student_id IN $owners DETACH DELETE n", {"owners": [owner, victim]}, read_only=False)
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id IN (:owner,:victim)"), {"owner": owner, "victim": victim})
            conn.execute(text("DELETE FROM student_profile WHERE student_id IN (:owner,:victim)"), {"owner": owner, "victim": victim})
