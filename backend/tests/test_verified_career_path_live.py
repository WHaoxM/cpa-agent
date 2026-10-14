"""Opt-in Flask HTTP route with real MySQL L3 and public Neo4j career edges."""

import hashlib
import os
import threading
import uuid

import pytest
import requests
from flask import g, request
from werkzeug.serving import make_server

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db import get_neo4j_client
from app.services.db.mysql_client import get_mysql_client
from app.services.job_publication import canonical_json, publish_job
from backend.tests.test_verified_match_report import snapshot


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL and Neo4j opt-in")
def test_verified_career_path_live_sources_and_revocation(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db, graph = get_mysql_client(), get_neo4j_client()
    token = uuid.uuid4().hex[:16]
    sid, jid = f"career_{token}", f"career_{token}_job"
    l0, l2 = f"career_{token}_l0", f"career_{token}_l2"
    start, end = f"test_career_{token}", f"senior_{token}"
    job = snapshot()
    job["source_text"] = job["source_text"].replace("Test-only role", start)
    job["source_text_sha256"] = hashlib.sha256(job["source_text"].encode()).hexdigest()
    job["position_name"] = start
    job["field_evidence"]["position_name"]["excerpt"] = start

    source = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
    digest = hashlib.sha256(source.encode()).hexdigest()
    facts = {key: {"value": "中", "evidence": phrase, "source_sha256": digest}
             for key, phrase in {"communication": "沟通协作", "teamwork": "团队项目",
                                 "stress_tolerance": "紧急截止日前完成交付",
                                 "responsibility": "我负责课程团队项目"}.items()}
    candidate = {"skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
                 "degree": "专科", "source_sha256": digest, "semantic_facts": facts}
    profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
    from app.pipeline.entity_extractor.matcher import SkillMatcher
    monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda self: None))
    server = None
    thread = None
    try:
        db.execute("INSERT INTO student_profile (student_id,profile_json) VALUES (:sid,:p)",
                   {"sid": sid, "p": canonical_json(profile)}, commit=True)
        portrait = {"source_confirmation_task_id": l0, "source_portrait_task_id": l2}
        db.execute("INSERT INTO student_portrait_4d (student_id,target_job_id,portrait_json) "
                   "VALUES (:sid,:jid,:p)", {"sid": sid, "jid": jid, "p": canonical_json(portrait)}, commit=True)
        portrait_id = db.fetch_all("SELECT id FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
                                   {"sid": sid, "jid": jid})[0]["id"]
        for tid, pipeline, outcome in (
            (l0, "L0_parse_resume", {"confirmed": True, "candidate_profile": candidate}),
            (l2, "L2_generate_portrait", {"portrait_id": str(portrait_id), "portrait": portrait}),
        ):
            db.execute("INSERT INTO pipeline_task (task_id,pipeline,status,student_id,result_json,params_json) "
                       "VALUES (:tid,:pipeline,'completed',:sid,:result,'{}')",
                       {"tid": tid, "pipeline": pipeline, "sid": sid, "result": canonical_json(outcome)},
                       commit=True)
        publish_job(db, job_portrait_id=jid, version="career-test-v1", snapshot=job)
        db.execute("INSERT INTO career_path (student_id,job_portrait_id,recommended_dir,promotion_steps,"
                   "lateral_count,path_json) VALUES (:sid,:jid,'FORGED_DIRECTION',99,99,:payload)",
                   {"sid": sid, "jid": jid, "payload": '["FORGED_PATH"]'}, commit=True)

        app = create_app()

        @app.before_request
        def bind_owner():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = Principal(owner, owner, "http-test")

        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def get(path, **kwargs):
            return requests.get(base + path, timeout=15, **kwargs)

        def post(path, **kwargs):
            return requests.post(base + path, timeout=15, **kwargs)

        path = f"/api/match/career-recommendation/{sid}?job_portrait_id={jid}"
        assert get(path).status_code == 404  # legacy row is not an L3 result
        match = post("/api/match/run", json={"job_portrait_id": jid})
        assert match.status_code == 200 and match.json()["data"]["result"]["match_id"]

        empty = get(path)
        assert empty.status_code == 200 and empty.json()["status"] == "partial"
        assert empty.json()["data"]["paths"] == []
        assert empty.json()["data"]["reason"] == "exact_public_promotion_path_unavailable"
        assert "FORGED" not in empty.text

        graph.execute_query(
            "MERGE (s:CareerNode {position_name:$start}) "
            "MERGE (e:CareerNode {position_name:$end}) "
            "MERGE (s)-[:PROMOTION]->(e) "
            "MERGE (private:CareerNode:Person {position_name:$start}) "
            "SET private.secret=$secret MERGE (private)-[:PROMOTION]->(e)",
            {"start": start, "end": end, "secret": f"PRIVATE_{token}"}, read_only=False,
        )
        matched = get(path)
        assert matched.status_code == 200 and matched.json()["status"] == "partial"
        assert matched.json()["data"]["paths"] == [{"start": start, "end": end, "steps": 1}]
        assert matched.json()["data"]["personalized_recommendation_verified"] is False
        assert "PRIVATE_" not in matched.text
        assert "FORGED" not in matched.text
        assert get(path, headers={"X-Test-Owner": f"other_{token}"}).status_code == 403

        from app.api import graph as graph_api
        with monkeypatch.context() as fault:
            fault.setattr(graph_api, "get_neo4j_client", lambda: (_ for _ in ()).throw(
                ConnectionError("graph unavailable")))
            degraded = get(path)
        assert degraded.status_code == 200 and degraded.json()["status"] == "partial"
        assert degraded.json()["data"]["position_name"] == start
        assert degraded.json()["data"]["paths"] == []
        assert degraded.json()["data"]["unavailable_sources"] == ["public_career_graph"]

        db.execute("UPDATE job_catalog_publication SET status='revoked' WHERE job_portrait_id=:jid",
                   {"jid": jid}, commit=True)
        stale = get(path)
        assert stale.status_code == 409 and stale.json()["status"] == "stale"
        assert start not in stale.text
        assert "FORGED" not in stale.text
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        for table in ("career_path", "career_report", "match_result", "pipeline_task_event",
                      "pipeline_task", "student_portrait_4d", "student_profile"):
            db.execute(f"DELETE FROM {table} WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid", {"jid": jid}, commit=True)
        graph.execute_query("MATCH (n) WHERE n.position_name IN [$start,$end] DETACH DELETE n",
                            {"start": start, "end": end}, read_only=False)
        assert db.fetch_all("SELECT COUNT(*) AS n FROM career_path WHERE student_id=:sid", {"sid": sid})[0]["n"] == 0
        assert graph.execute_query("MATCH (n) WHERE n.position_name IN [$start,$end] RETURN count(n) AS n",
                                   {"start": start, "end": end}) == [{"n": 0}]
