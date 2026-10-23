"""A student returns to an evidence-grounded report conversation over real HTTP.

Opt in with CPA_TEST_LIVE_MYSQL=1. Uses a disposable MySQL schema, a dedicated
empty Redis logical DB, real account cookies/CSRF, and a deterministic Ask LLM.
The synthetic report is prepared before the user journey starts; this test does
not re-run the job-decision HTTP flow or assess real-model answer quality.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

import pytest


RESUME_URI = "career://me/resume"
REPORTS_URI = "career://me/reports"
_EVIDENCE_START = '<SERVER_EVIDENCE kind="EvidencePack">\n'
_EVIDENCE_END = "\n</SERVER_EVIDENCE>"


def _extract_evidence(messages):
    joined = "\n".join(str(item.get("content") or "") for item in messages)
    start = joined.find(_EVIDENCE_START)
    if start < 0:
        return None
    start += len(_EVIDENCE_START)
    end = joined.find(_EVIDENCE_END, start)
    if end < 0:
        return None
    return json.loads(joined[start:end])


class _DeterministicAskLLM:
    """Capture the evidence the real Ask runtime supplies; never calls a provider."""

    def __init__(self, evidence_path: Path):
        self.evidence_path = evidence_path
        self.calls = []

    def chat(self, *, messages, **_kwargs):
        system = str(messages[0].get("content") or "") if messages else ""
        joined = "\n".join(str(item.get("content") or "") for item in messages)
        if "只负责为用户当前问题选择最小必要的只读证据" in system:
            stage = "evidence_selection"
            request = json.loads(messages[-1]["content"])
            reads = []
            for resource in request.get("catalog", []):
                title = str(resource.get("title") or "")
                if "简历" in title and "projects" in resource.get("sections", []):
                    reads.append({"id": resource["id"], "section": "projects"})
                elif "岗位报告" in title and "overview" in resource.get("sections", []):
                    reads.append({"id": resource["id"], "section": "overview"})
            response = json.dumps({"needed": True, "answer_kind": "analysis", "reads": reads},
                                  ensure_ascii=False)
        else:
            stage = "answer"
            pack = _extract_evidence(messages) or {}
            unavailable = any(
                item.get("data", {}).get("status") == "not_available"
                or item.get("data", {}).get("business_artifact_verified") is False
                for item in pack.get("items", []) if isinstance(item, dict)
                and isinstance(item.get("data"), dict)
            ) or any(
                isinstance(item, dict)
                and str(item.get("source") or "").startswith(REPORTS_URI + "/")
                for item in pack.get("unavailable", [])
            )
            has_resume = any(item.get("uri") == RESUME_URI
                             for item in pack.get("items", []) if isinstance(item, dict))
            if unavailable:
                response = (
                    "你之前引用的岗位报告来源现已失效，我不会沿用旧分数或旧结论。"
                    "请先查看当前岗位状态；如来源已撤回，需要等待新的有效报告。"
                )
            elif "每周 5 小时" in joined or "每周5小时" in joined:
                response = (
                    "结合当前报告中的待补方向和你刚刚补充的每周 5 小时，"
                    "可以先完成一个小型 Python 数据整理项目并记录过程。"
                    "每周时间是你的自述，未经平台独立核验；这只是准备建议，不代表录用结论。"
                )
            elif has_resume:
                response = (
                    "报告列出了 Python 相关的待补方向；简历没有说明你是否做过这类项目，"
                    "这不代表你不会。你目前每周大约能投入多少时间做练习？"
                )
            else:
                response = (
                    "这份报告把 Python 列为待补方向，但报告本身不能说明你是否已经做过相关项目。"
                    "你可以先回到简历资料确认项目经历，或告诉我你目前做过哪些数据处理练习。"
                )

        record = {"stage": stage, "messages": messages,
                  "evidence_pack": _extract_evidence(messages), "response": response}
        self.calls.append(record)
        with self.evidence_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return response

    def chat_with_metadata(self, **_kwargs):
        raise AssertionError("grounding review is outside this deterministic journey")


def _object(value):
    if isinstance(value, str):
        return json.loads(value)
    return value if isinstance(value, dict) else {}


def _seed_report(mysql, *, student_id: str, nonce: str):
    """Prepare a valid, synthetic current report before the conversation begins."""
    from app.services.job_publication import canonical_json, publish_job
    from app.services.pipeline import PipelineRunner
    from backend.tests.test_verified_match_report import snapshot

    job_id = f"ask_return_job_{nonce}"
    l0 = f"task_{uuid.uuid4().hex}"
    l2 = f"task_{uuid.uuid4().hex}"
    source_text = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    semantic_facts = {
        key: {"value": "中", "evidence": evidence, "source_sha256": source_digest}
        for key, evidence in {
            "communication": "沟通协作", "teamwork": "团队项目",
            "stress_tolerance": "紧急截止日前完成交付", "responsibility": "我负责课程团队项目",
        }.items()
    }
    candidate = {
        "name": "合成学生", "school": "示例大学", "major": "计算机科学", "degree": "本科",
        "skills": [{"name": "Excel", "level": "熟悉", "confidence": 0.9}],
        "projects": [{"name": "课程数据整理", "role": "负责人",
                      "description": "使用 Excel 整理公开数据并完成课程展示"}],
        "source_sha256": source_digest, "semantic_facts": semantic_facts,
    }
    profile = {**candidate, "_resume_confirmation": {
        "student_id": student_id, "task_id": l0,
        "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []},
    }}
    mysql.execute(
        "INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)",
        {"sid": student_id, "profile": canonical_json(profile)}, commit=True,
    )
    mysql.execute(
        "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
        "VALUES (:task,'L0_parse_resume','completed',:sid,:result,'{}')",
        {"task": l0, "sid": student_id,
         "result": canonical_json({"confirmed": True, "candidate_profile": candidate})}, commit=True,
    )
    portrait = {
        "source_confirmation_task_id": l0, "source_portrait_task_id": l2,
        "D1_basic": {"dimension_score": 70}, "D2_skill": {"dimension_score": 55},
        "D3_quality": {"dimension_score": 60}, "D4_potential": {"dimension_score": 58},
    }
    mysql.execute(
        "INSERT INTO student_portrait_4d(student_id,target_job_id,portrait_json) "
        "VALUES (:sid,:jid,:portrait)",
        {"sid": student_id, "jid": job_id, "portrait": canonical_json(portrait)}, commit=True,
    )
    portrait_id = mysql.fetch_one(
        "SELECT id FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
        {"sid": student_id, "jid": job_id},
    )["id"]
    mysql.execute(
        "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
        "VALUES (:task,'L2_generate_portrait','completed',:sid,:result,'{}')",
        {"task": l2, "sid": student_id,
         "result": canonical_json({"portrait_id": str(portrait_id), "portrait": portrait})}, commit=True,
    )
    job_snapshot = snapshot()
    job_snapshot["position_name"] = "数据分析实习生"
    job_snapshot["source_text"] = (
        "数据分析实习生。岗位要求使用 Python 处理业务数据，本科及以上，不限专业，无需相关证书。"
        "这是隔离验收使用的合成岗位，不代表真实招聘信息。"
    )
    job_snapshot["source_text_sha256"] = hashlib.sha256(
        job_snapshot["source_text"].encode("utf-8")
    ).hexdigest()
    job_snapshot["field_evidence"]["position_name"]["excerpt"] = "数据分析实习生"
    job_snapshot["field_evidence"]["degree_requirement"]["excerpt"] = "本科及以上"
    job_snapshot["field_evidence"]["major_requirements"]["excerpt"] = "不限专业"
    job_snapshot["field_evidence"]["major_strict"]["excerpt"] = "不限专业"
    job_snapshot["field_evidence"]["required_certs"]["excerpt"] = "无需相关证书"
    job_snapshot["field_evidence"]["required_skills"]["excerpt"] = "使用 Python 处理业务数据"
    publication = publish_job(mysql, job_portrait_id=job_id,
                              version="ask-return-synthetic-v1", snapshot=job_snapshot)
    match = PipelineRunner().run("L3_run_match", {"student_id": student_id, "job_id": job_id})
    assert match.get("match_id") and match.get("student_id") == student_id
    report = PipelineRunner().run("L4_generate_report", {"student_id": student_id, "job_id": job_id})
    assert report.get("report_id") and report.get("verified") is True
    row = mysql.fetch_one(
        "SELECT report_id,student_id,job_portrait_id,status,report_json FROM career_report "
        "WHERE report_id=:rid AND student_id=:sid",
        {"rid": report["report_id"], "sid": student_id},
    )
    assert row and row["job_portrait_id"] == job_id and row["status"] in {"partial", "completed"}
    return {"job_id": job_id, "l0_task_id": l0, "l2_task_id": l2,
            "match_id": str(match["match_id"]), "report_id": report["report_id"],
            "publication_id": publication["id"], "report_json": _object(row["report_json"])}


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires explicit opt-in, disposable MySQL schema and dedicated Redis DB")
def test_authenticated_student_report_followup_and_return_journey(monkeypatch, tmp_path):
    import pymysql
    import redis
    import requests
    from werkzeug.serving import make_server

    from app import create_app
    from app.config import Config
    from app.services.account_session_schema import migrate_account_session_schema
    from app.services.db import mysql_client as mysql_module, redis_client as redis_module
    from app.services.db.mysql_client import MySQLClient
    from app.services.db.redis_client import RedisClient
    from app.services import session_manager
    from app.services.agent_runtime import session_coordinator
    from app.utils import llm_client

    nonce = uuid.uuid4().hex[:12]
    database = f"cpa_ask_return_{nonce}"
    redis_url = os.getenv("CPA_TEST_ASK_RETURN_REDIS_URL", "redis://127.0.0.1:16379/11")
    raw_redis = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)
    try:
        raw_redis.ping()
    except redis.RedisError as exc:
        raw_redis.close()
        pytest.skip(f"dedicated Ask-return Redis DB unavailable; not accepted: {exc}")
    if raw_redis.dbsize() != 0:
        raw_redis.close()
        pytest.skip("Ask-return Redis DB must be empty before the journey; not accepted")
    baseline_keys = set(raw_redis.scan_iter())
    redis_lock = f"cpa-ask-return-test-lock:{nonce}"
    if not raw_redis.set(redis_lock, nonce, nx=True, ex=1800):
        raw_redis.close()
        pytest.skip("could not reserve the dedicated Ask-return Redis DB; not accepted")

    admin = mysql = None
    shared_redis = None
    database_created = False
    server = thread = None
    clients = []
    try:
        admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                                user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                                autocommit=True)
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        database_created = True
        backend = Path(__file__).resolve().parents[1]
        subprocess.run(
            [__import__("sys").executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=backend, check=True, capture_output=True, text=True,
        )
        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=5, max_overflow=0)
        monkeypatch.setattr(mysql_module, "_mysql_client", mysql)
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        monkeypatch.setattr(Config, "REDIS_URL", redis_url)
        monkeypatch.setattr(Config, "SESSION_TURN_LOCK_MODE", "redis_required")
        migrate_account_session_schema(mysql)

        shared_redis = RedisClient(redis_url)
        monkeypatch.setattr(redis_module, "_redis_client", shared_redis)
        monkeypatch.setattr(session_manager, "_session_manager", None)
        monkeypatch.setattr(session_coordinator, "_coordinator", None)
        monkeypatch.setenv("CPA_ASK_GROUNDING_REVIEW_ENABLED", "false")
        deterministic_llm = _DeterministicAskLLM(tmp_path / "llm-calls.jsonl")
        monkeypatch.setattr(llm_client, "get_llm_client", lambda: deterministic_llm)

        auth_config = type("AskReturnJourneyConfig", (Config,), {
            "SECRET_KEY": "ask-return-http-test-secret-1234567890-abcdef",
            "DEMO_MODE": False,
            "CPA_AUTH_COOKIE_SECURE": False,
            "REDIS_URL": redis_url,
            "RATELIMIT_KEY_PREFIX": f"ask-return-{nonce}",
            "UPLOAD_FOLDER": str(tmp_path),
            "CORS_ALLOWED_ORIGINS": [],
        })
        app = create_app(auth_config)
        assert app.extensions.get("cpa_account_auth_enabled") is True
        assert app.extensions.get("cpa_account_session_schema_ready") is True
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        transcript = tmp_path / "http-transcript.jsonl"

        def request(client, method, path, *, csrf=None, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            if csrf:
                headers["X-CSRFToken"] = csrf
            response = client.request(method, base + path, headers=headers,
                                      timeout=(3, 25), **kwargs)
            payload = kwargs.get("json")
            if path in {"/api/auth/login", "/api/auth/register"}:
                payload = {"fields": sorted((payload or {}).keys())}
            with transcript.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"method": method, "path": path,
                                         "status": response.status_code,
                                         "request": payload,
                                         "response": response.text}, ensure_ascii=False) + "\n")
            return response

        password = "ask-return-test-password-42"

        def login(client, username, *, register=False):
            csrf_response = request(client, "GET", "/api/auth/csrf")
            assert csrf_response.status_code == 200, csrf_response.text
            csrf = csrf_response.json()["csrfToken"]
            credentials = {"username": username, "password": password}
            if register:
                created = request(client, "POST", "/api/auth/register", csrf=csrf,
                                  json=credentials)
                assert created.status_code == 201, created.text
                account_id = created.json()["user"]["id"]
            else:
                account_id = None
            logged_in = request(client, "POST", "/api/auth/login", csrf=csrf,
                                json=credentials)
            assert logged_in.status_code == 200, logged_in.text
            csrf = request(client, "GET", "/api/auth/csrf").json()["csrfToken"]
            me = request(client, "GET", "/api/auth/me")
            assert me.status_code == 200, me.text
            if account_id is None:
                account_id = me.json()["user"]["id"]
            assert me.json()["user"]["id"] == account_id
            return account_id, csrf

        owner_client = requests.Session()
        stranger_client = requests.Session()
        for client in (owner_client, stranger_client):
            client.trust_env = False
            clients.append(client)
        owner_id, owner_csrf = login(owner_client, f"ask_owner_{nonce}", register=True)
        stranger_id, stranger_csrf = login(stranger_client, f"ask_other_{nonce}", register=True)
        assert owner_id != stranger_id

        from app.pipeline.entity_extractor.matcher import SkillMatcher
        monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda _self: None))
        seeded = _seed_report(mysql, student_id=owner_id, nonce=nonce)
        report_snapshot = mysql.fetch_one(
            "SELECT report_json FROM career_report WHERE report_id=:rid AND student_id=:sid",
            {"rid": seeded["report_id"], "sid": owner_id},
        )
        assert report_snapshot
        report_uri = f"{REPORTS_URI}/{seeded['report_id']}"
        # The browser's “问问这份报告” action sends only the focus report.
        # The server must discover and bind its current resume source itself.
        refs = [{"type": "resource_ref", "uri": report_uri}]

        created = request(owner_client, "POST", "/api/agent/session/create", csrf=owner_csrf,
                          json={})
        assert created.status_code == 200, created.text
        session_id = created.json()["data"]["session_id"]
        first = request(owner_client, "POST", "/api/agent/chat", csrf=owner_csrf, json={
            "session_id": session_id,
            "interaction_mode": "ask",
            "message": "我看完这个岗位报告，不知道应该先补什么，依据是什么？",
            "content_parts": refs,
        })
        assert first.status_code == 200, first.text
        first_data = first.json()["data"]
        first_reply = first_data["reply"]
        assert "不代表你不会" in first_reply
        assert "你目前每周大约能投入多少时间做练习？" in first_reply
        assert "资料依据：" not in first_reply
        first_summary = next(part for part in first_data["parts"]
                             if part["type"] == "evidence_summary")
        assert first_summary["status"] == "partial"
        assert {source["label"] for source in first_summary["sources"]} == {
            "岗位报告", "已确认简历",
        }
        assert "career://" not in json.dumps(first_summary, ensure_ascii=False)
        assert seeded["report_id"] not in json.dumps(first_summary, ensure_ascii=False)
        assert seeded["l0_task_id"] not in json.dumps(first_summary, ensure_ascii=False)
        assert first_data["session_id"] == session_id
        first_answer = [call for call in deterministic_llm.calls if call["stage"] == "answer"][-1]
        first_pack = first_answer["evidence_pack"]
        assert first_pack and first_pack["items"]
        assert any(item["uri"] == RESUME_URI and item["version"] == seeded["l0_task_id"]
                   for item in first_pack["items"])
        report_item = next(item for item in first_pack["items"]
                           if item["uri"] == report_uri)
        report_data = report_item["data"]
        assert report_item["uri"] == report_uri
        assert report_data["position_name"] == "数据分析实习生"
        assert report_data["sources"]["source_confirmation_task_id"] == seeded["l0_task_id"]
        assert report_item["version"]
        assert "total_score" not in json.dumps(first_pack.get("unavailable", []))

        second = request(owner_client, "POST", "/api/agent/chat", csrf=owner_csrf, json={
            "session_id": session_id,
            "interaction_mode": "ask",
            "message": "我每周 5 小时，可以做一点项目。按报告给我一个起步建议。",
            "content_parts": refs,
        })
        assert second.status_code == 200, second.text
        second_data = second.json()["data"]
        assert "每周 5 小时" in second_data["reply"]
        assert "自述" in second_data["reply"] and "不代表录用结论" in second_data["reply"]
        assert second_data["session_id"] == session_id
        second_answer = [call for call in deterministic_llm.calls if call["stage"] == "answer"][-1]
        assert "每周 5 小时" in json.dumps(second_answer["messages"], ensure_ascii=False)
        assert any(item["uri"] == report_uri and item["version"]
                   for item in second_answer["evidence_pack"]["items"])

        # The first account goes away. Clearing only the in-process cache forces
        # the returning HTTP client to recover the conversation from real Redis.
        owner_client.close()
        session_manager.get_session_manager()._cache.clear()
        returning_client = requests.Session()
        returning_client.trust_env = False
        clients.append(returning_client)
        returning_id, returning_csrf = login(returning_client, f"ask_owner_{nonce}")
        assert returning_id == owner_id
        sessions = request(returning_client, "GET", "/api/agent/sessions")
        assert sessions.status_code == 200, sessions.text
        session_rows = sessions.json()["data"]["sessions"]
        own_session = next(row for row in session_rows if row["session_id"] == session_id)
        assert own_session["student_id"] == owner_id and own_session["message_count"] == 4
        history = request(returning_client, "GET", f"/api/agent/session/{session_id}/history")
        assert history.status_code == 200, history.text
        assert [item["role"] for item in history.json()["data"]["messages"]] == [
            "user", "assistant", "user", "assistant",
        ]
        assert history.json()["data"]["messages"][1]["content"] == first_reply
        assert history.json()["data"]["messages"][3]["content"] == second_data["reply"]
        returned_parts = history.json()["data"]["messages"][1]["parts"]
        assert returned_parts == first_data["parts"]
        assert any(part["type"] == "evidence_summary" for part in returned_parts)

        stranger_sessions = request(stranger_client, "GET", "/api/agent/sessions")
        assert stranger_sessions.status_code == 200, stranger_sessions.text
        assert all(row["session_id"] != session_id
                   for row in stranger_sessions.json()["data"]["sessions"])
        foreign_history = request(stranger_client, "GET", f"/api/agent/session/{session_id}/history")
        assert foreign_history.status_code in {403, 404}, foreign_history.text

        # A returned student continues the same conversation while the source
        # is still current; the same report and resume versions remain bound.
        third = request(returning_client, "POST", "/api/agent/chat", csrf=returning_csrf, json={
            "session_id": session_id,
            "interaction_mode": "ask",
            "message": "继续按报告给我一个可以本周完成的下一步。",
            "content_parts": refs,
        })
        assert third.status_code == 200, third.text
        assert third.json()["data"]["session_id"] == session_id
        third_answer = [call for call in deterministic_llm.calls if call["stage"] == "answer"][-1]
        assert any(item["uri"] == report_uri and item["version"]
                   for item in third_answer["evidence_pack"]["items"])

        # On a later leave/return, a withdrawn publication makes the report
        # unavailable before it can be used as current advice.
        returning_client.close()
        mysql.execute("UPDATE job_catalog_publication SET status='revoked' WHERE id=:id",
                      {"id": seeded["publication_id"]}, commit=True)
        session_manager.get_session_manager()._cache.clear()
        final_client = requests.Session()
        final_client.trust_env = False
        clients.append(final_client)
        final_id, final_csrf = login(final_client, f"ask_owner_{nonce}")
        assert final_id == owner_id
        before_calls = len(deterministic_llm.calls)
        stale = request(final_client, "POST", "/api/agent/chat", csrf=final_csrf, json={
            "session_id": session_id,
            "interaction_mode": "ask",
            "message": "我回来后还想接着用之前的报告，应该先做什么？",
            "content_parts": refs,
        })
        assert stale.status_code == 200, stale.text
        assert stale.json()["data"]["session_id"] == session_id
        assert len(deterministic_llm.calls) == before_calls
        stale_reply = stale.json()["data"]["reply"]
        assert "来源已失效" in stale_reply and "新的有效报告" in stale_reply
        assert "39.4" not in stale_reply and "Python" not in stale_reply

        redis_state = json.loads(raw_redis.get(f"cpa:session:{session_id}"))
        assert redis_state["student_id"] == owner_id
        assert len(redis_state["messages"]) == 8
        conversation = mysql.fetch_one(
            "SELECT student_id,message_count FROM conversation WHERE session_id=:sid",
            {"sid": session_id},
        )
        assert conversation == {"student_id": owner_id, "message_count": 8}
        message_rows = mysql.fetch_all(
            "SELECT message_id,role,content FROM conversation_message WHERE session_id=:sid",
            {"sid": session_id},
        )
        mysql_messages = {row["message_id"]: row for row in message_rows}
        assert set(mysql_messages) == {message["message_id"] for message in redis_state["messages"]}
        assert [(mysql_messages[message["message_id"]]["role"],
                 mysql_messages[message["message_id"]]["content"])
                for message in redis_state["messages"]] == [
            (message["role"], message["content"]) for message in redis_state["messages"]
        ]
        report_row = mysql.fetch_one(
            "SELECT report_id,student_id,status FROM career_report WHERE report_id=:rid",
            {"rid": seeded["report_id"]},
        )
        assert report_row == {"report_id": seeded["report_id"],
                              "student_id": owner_id, "status": "partial"}
    finally:
        for client in clients:
            client.close()
        if server:
            server.shutdown()
        if thread:
            thread.join(timeout=5)
        try:
            if mysql:
                mysql.close()
            if database_created and admin:
                with admin.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
                    cursor.execute(
                        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
                        (database,),
                    )
                    assert cursor.fetchone() is None
        finally:
            if admin:
                admin.close()
            try:
                created_keys = list(set(raw_redis.scan_iter()) - baseline_keys)
                if created_keys:
                    raw_redis.delete(*created_keys)
                assert set(raw_redis.scan_iter()) == baseline_keys
            finally:
                if shared_redis:
                    shared_redis.close()
                raw_redis.close()
