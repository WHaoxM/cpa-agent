"""Real MySQL audit of the append-only entity-review boundary."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask, g, request
from sqlalchemy import text

from app.api._helpers import Principal
from app.api.pipeline import pipeline_bp
from app.config import Config
from app.services.db import get_mysql_client
from app.services.resume_entity_review import read_entity_review, write_entity_review


def _fixture(mysql, owner, task_id):
    source = "Python 与大模型应用开发"
    linking = {
        "status": "proposed", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "mentions": [{"name": "Python", "evidence": "Python"},
                     {"name": "大模型", "evidence": "大模型"}],
        "candidates": [{"name": "Python", "category": "编程", "source_id": "public_skill_catalog_v1"},
                       {"name": "LLM", "category": "AI", "source_id": "public_skill_catalog_v1"}],
        "decisions": [{"mention_index": 0, "decision": "equivalent", "candidate_index": 0,
                       "rationale": "同名"},
                      {"mention_index": 1, "decision": "related", "candidate_index": 1,
                       "rationale": "相关概念"}],
        "next_action": "confirm_resume",
    }
    candidate = {"name": "测试", "entity_linking": linking}
    result = {"candidate_profile": candidate, "confirmed": True, "status": "completed"}
    profile = {**candidate, "_resume_confirmation": {
        "task_id": task_id, "student_id": owner, "confirmed_at": "2026-10-10T00:00:00"}}
    with mysql.engine.begin() as conn:
        conn.execute(text("INSERT INTO pipeline_task "
            "(task_id,pipeline,status,progress,result_json,student_id,triggered_by,created_at,updated_at) "
            "VALUES (:tid,'L0_parse_resume','completed',100,:result,:sid,'api',NOW(),NOW())"),
            {"tid": task_id, "sid": owner, "result": json.dumps(result, ensure_ascii=False)})
        conn.execute(text("INSERT INTO student_profile(student_id,profile_json,created_at,updated_at) "
                          "VALUES (:sid,:profile,NOW(),NOW())"),
                     {"sid": owner, "profile": json.dumps(profile, ensure_ascii=False)})
    return linking


@pytest.fixture
def review_db(monkeypatch):
    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    migration = Path(__file__).parents[1] / "scripts/mysql/migration_resume_entity_review.sql"
    with mysql.engine.begin() as conn:
        conn.execute(text(migration.read_text(encoding="utf-8")))
    # Isolated DB contract fixture does not dispatch an asynchronous worker.
    from app.services.personal_entity_link_jobs import projection_task
    monkeypatch.setattr(projection_task, "apply_async", lambda **kwargs: None)
    owner = "review_" + uuid.uuid4().hex[:12]
    other = "review_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:12]
    linking = _fixture(mysql, owner, task_id)
    try:
        yield mysql, owner, other, task_id, linking
    finally:
        with mysql.engine.begin() as conn:
            if conn.execute(text("SHOW TABLES LIKE 'personal_entity_link_projection'")).first():
                conn.execute(text("DELETE FROM personal_entity_link_projection WHERE student_id=:sid"),
                             {"sid": owner})
            conn.execute(text("DELETE FROM resume_entity_review WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE task_id=:tid AND student_id=:sid"),
                         {"tid": task_id, "sid": owner})


def _payload(task_id, sha, reviews, revision=0, request_id=None):
    return {"source_confirmation_task_id": task_id, "source_sha256": sha,
            "request_id": request_id or "req_" + uuid.uuid4().hex,
            "expected_revision": revision, "reviews": reviews}


def test_review_persists_replays_and_isolates_owner(review_db):
    mysql, owner, other, task_id, linking = review_db
    source_hash = linking["source_sha256"]
    first = _payload(task_id, source_hash, [{"mention_index": 1, "action": "accept"}])
    saved = write_entity_review(mysql, student_id=owner, payload=first)
    assert saved["revision"] == 1
    assert saved["reviews"] == [{"mention_index": 1, "action": "accept", "note": "",
                                 "relation": "related", "candidate_index": 1}]
    assert write_entity_review(mysql, student_id=owner, payload=first)["replayed"] is True
    assert read_entity_review(mysql, student_id=owner)["revision"] == 1
    with pytest.raises(LookupError):
        read_entity_review(mysql, student_id=other)
    from app.services.resume_entity_review import ReviewConflict
    with pytest.raises(ReviewConflict):
        write_entity_review(mysql, student_id=owner, payload={**first, "reviews": [
            {"mention_index": 1, "action": "reject"}]})
    with pytest.raises(ReviewConflict):
        write_entity_review(mysql, student_id=owner,
            payload=_payload(task_id, source_hash, [{"mention_index": 0, "action": "defer"}]))
    second = _payload(task_id, source_hash, [{"mention_index": 0, "action": "reject", "note": "不是这项"}], 1)
    assert write_entity_review(mysql, student_id=owner, payload=second)["revision"] == 2
    rows = mysql.execute("SELECT revision FROM resume_entity_review WHERE student_id=:sid ORDER BY revision",
                         {"sid": owner})
    assert [r["revision"] for r in rows] == [1, 2]
    profile = mysql.execute("SELECT profile_json FROM student_profile WHERE student_id=:sid", {"sid": owner})[0]
    stored = profile["profile_json"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored["entity_linking"] == linking


def test_review_api_validation_and_strict_identity(review_db, monkeypatch):
    mysql, owner, other, task_id, linking = review_db
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(pipeline_bp, url_prefix="/api/pipeline")

    @app.before_request
    def auth():
        # No identity fallback from body/query.
        sid = request.headers.get("X-Test-Owner")
        if sid:
            g.cpa_principal = Principal("test:" + sid, sid, "test")

    client = app.test_client()
    url = "/api/pipeline/entity-review"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Test-Owner": other}).status_code == 404
    current = client.get(url, headers={"X-Test-Owner": owner})
    assert current.status_code == 200
    assert current.json["data"]["revision"] == 0
    base = _payload(task_id, linking["source_sha256"], [{"mention_index": 0, "action": "accept"}])
    cases = [
        {**base, "source_sha256": "0" * 64},
        {**base, "reviews": [{"mention_index": 2, "action": "accept"}]},
        {**base, "reviews": [{"mention_index": 0, "action": "accept"},
                             {"mention_index": 0, "action": "reject"}]},
        {**base, "reviews": [{"mention_index": 0, "action": "merge"}]},
        {**base, "reviews": [{"mention_index": 0, "action": "accept", "canonical_id": "forged"}]},
    ]
    assert client.post(url, json=cases[0], headers={"X-Test-Owner": owner}).status_code == 409
    for bad in cases[1:]:
        assert client.post(url, json=bad, headers={"X-Test-Owner": owner}).status_code == 400
    assert client.post(url, json=base, headers={"X-Test-Owner": other}).status_code == 404
    good = client.post(url, json=base, headers={"X-Test-Owner": owner})
    assert good.status_code == 200
    assert good.json["data"]["revision"] == 1


def test_review_serializes_expected_revision(review_db):
    mysql, owner, _, task_id, linking = review_db
    left = _payload(task_id, linking["source_sha256"],
                    [{"mention_index": 0, "action": "accept"}])
    right = _payload(task_id, linking["source_sha256"],
                     [{"mention_index": 1, "action": "defer"}])
    from app.services.resume_entity_review import ReviewConflict

    def run(payload):
        try:
            return write_entity_review(mysql, student_id=owner, payload=payload)["revision"]
        except ReviewConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, [left, right]))
    assert sorted(outcomes, key=str) == [1, "conflict"]
    assert len(mysql.execute("SELECT id FROM resume_entity_review WHERE student_id=:sid", {"sid": owner})) == 1


def test_new_confirmation_invalidates_prior_review_source(review_db):
    mysql, owner, _, task_id, linking = review_db
    old = _payload(task_id, linking["source_sha256"],
                   [{"mention_index": 0, "action": "accept"}])
    assert write_entity_review(mysql, student_id=owner, payload=old)["revision"] == 1
    next_task = "task_" + uuid.uuid4().hex[:12]
    candidate = {"name": "新简历", "entity_linking": {**linking,
        "source_sha256": hashlib.sha256(b"new source").hexdigest()}}
    result = {"candidate_profile": candidate, "confirmed": True, "status": "completed"}
    profile = {**candidate, "_resume_confirmation": {
        "task_id": next_task, "student_id": owner, "confirmed_at": "2026-10-10T01:00:00"}}
    try:
        with mysql.engine.begin() as conn:
            conn.execute(text("INSERT INTO pipeline_task "
                "(task_id,pipeline,status,progress,result_json,student_id,triggered_by,created_at,updated_at) "
                "VALUES (:tid,'L0_parse_resume','completed',100,:result,:sid,'api',NOW(),NOW())"),
                {"tid": next_task, "sid": owner, "result": json.dumps(result, ensure_ascii=False)})
            conn.execute(text("UPDATE student_profile SET profile_json=:profile WHERE student_id=:sid"),
                         {"sid": owner, "profile": json.dumps(profile, ensure_ascii=False)})
        from app.services.resume_entity_review import ReviewConflict
        with pytest.raises(ReviewConflict):
            write_entity_review(mysql, student_id=owner, payload=old)
        current = read_entity_review(mysql, student_id=owner)
        assert current["source_confirmation_task_id"] == next_task
        assert current["revision"] == 0
    finally:
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task WHERE task_id=:tid AND student_id=:sid"),
                         {"tid": next_task, "sid": owner})


def test_accept_unresolved_proposal_fails_without_journal_write(review_db):
    mysql, owner, _, task_id, linking = review_db
    linking["decisions"][1] = {"mention_index": 1, "decision": "unresolved",
                                "candidate_index": None, "rationale": "无法对齐"}
    candidate = {"name": "测试", "entity_linking": linking}
    result = {"candidate_profile": candidate, "confirmed": True, "status": "completed"}
    profile = {**candidate, "_resume_confirmation": {
        "task_id": task_id, "student_id": owner, "confirmed_at": "2026-10-10T00:00:00"}}
    with mysql.engine.begin() as conn:
        conn.execute(text("UPDATE pipeline_task SET result_json=:result WHERE task_id=:tid AND student_id=:sid"),
                     {"result": json.dumps(result, ensure_ascii=False), "tid": task_id, "sid": owner})
        conn.execute(text("UPDATE student_profile SET profile_json=:profile WHERE student_id=:sid"),
                     {"profile": json.dumps(profile, ensure_ascii=False), "sid": owner})
    payload = _payload(task_id, linking["source_sha256"],
                       [{"mention_index": 1, "action": "accept"}])
    with pytest.raises(ValueError, match="没有可接受"):
        write_entity_review(mysql, student_id=owner, payload=payload)
    assert mysql.execute("SELECT id FROM resume_entity_review WHERE student_id=:sid", {"sid": owner}) == []
