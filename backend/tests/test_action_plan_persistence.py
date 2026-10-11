"""Real plan body -> owner store -> blackboard -> public artifact, without a live LLM."""

import pytest
from flask import Flask
from sqlalchemy import create_engine, text

from app.api import agent_bp
from app.api._helpers import Principal, RequestScope
from app.services.agent_runtime.artifact_committer import ArtifactCommitter
from app.services.agent_runtime.action_plan_store import ActionPlanStore
from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.egress_pipeline import project_turn
from app.services.agent_runtime.mode_runtime import ModeRuntime
from app.services.agent_runtime.plan import run_plan
from app.services.agent_runtime.task_packet import build_standard_packet
from app.services.agent_runtime.turn_contract import derive_turn_outcome


class SQLiteMySQL:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE agent_action_plan (
                    plan_id TEXT PRIMARY KEY, student_id TEXT, session_id TEXT,
                    title TEXT, body_text TEXT, body_sha256 TEXT, status TEXT,
                    created_at DATETIME, updated_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_artifact (
                    artifact_id TEXT PRIMARY KEY, student_id TEXT, session_id TEXT,
                    turn_id TEXT, kind TEXT, status TEXT, title TEXT, summary TEXT,
                    preview_json TEXT, actions_json TEXT, resource_type TEXT,
                    resource_id TEXT, version INTEGER, created_at DATETIME, updated_at DATETIME
                )
            """))

    def fetch_all(self, sql, params=None):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]

    def fetch_one(self, sql, params=None):
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None


@pytest.fixture
def store(monkeypatch):
    mysql = SQLiteMySQL()
    store = ActionPlanStore(mysql)
    monkeypatch.setattr(store, "_ensure_schema", lambda: None)
    monkeypatch.setattr(
        "app.services.agent_runtime.action_plan_store.get_action_plan_store", lambda: store,
    )
    monkeypatch.setattr("app.services.db.mysql_client.get_mysql_client", lambda: mysql)
    return store


def test_planner_turn_commits_body_and_artifact_to_owner_scope(store, monkeypatch):
    class RouterLLM:
        def chat_json(self, **_kwargs):
            return {"action": "call", "subagent": "planner", "goal": "制定本周学习计划"}

    reply = "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末独立复述并调整下周方向。"

    def invoke(_packet, _blackboard, **_kwargs):
        return {"reply": reply, "tool_calls": [], "permission_denials": []}

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: RouterLLM())
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.invoke_subagent_graph", invoke)
    result = ModeRuntime().chat(
        "制定本周学习计划", interaction_mode="agent", student_id="owner_1",
        session_id="sess_1", use_memory=False,
    )
    public = project_turn(result)
    pointer = result["blackboard"]["plan.current"]
    artifact = public.artifacts[0]
    saved = store.get_for_owner(pointer["plan_id"], student_id="owner_1", session_id="sess_1")

    assert result["orchestration"]["plan_results"][0]["ok"] is True
    assert derive_turn_outcome(result)["status"] == "completed"
    assert pointer["persisted"] is True
    assert saved and saved["body"] == reply
    assert store.get_for_owner(pointer["plan_id"], student_id="owner_2") is None
    assert artifact["artifact_id"] == pointer["artifact_id"]
    assert artifact["kind"] == "action_plan" and artifact["status"] == "completed"
    assert any(part.get("type") == "artifact_ref" for part in public.parts)


def test_index_failure_rolls_back_domain_plan(store):
    with store.mysql.engine.begin() as conn:
        conn.execute(text("DROP TABLE agent_artifact"))
    with pytest.raises(Exception):
        store.create_from_reply(
            "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末独立复述并调整下周方向。",
            student_id="owner_1", session_id="sess_1",
        )
    rows = store.mysql.fetch_all("SELECT plan_id FROM agent_action_plan")
    assert rows == []


@pytest.mark.parametrize("reply", [
    pytest.param("当前缺少关键输入，无法基于真实缺口制定本周学习计划。我需要至少以下任一信息才能启动规划，请补充后再继续。", id="refusal"),
    pytest.param("本周学习计划：" + "每天练习一小时并产出一页复盘。" * 700, id="oversized"),
])
def test_refusal_or_oversized_reply_cannot_be_claimed_as_saved(store, reply):
    with pytest.raises(ValueError):
        store.create_from_reply(reply, student_id="owner_1", session_id="sess_1")
    assert store.mysql.fetch_all("SELECT plan_id FROM agent_action_plan") == []
    assert store.mysql.fetch_all("SELECT artifact_id FROM agent_artifact") == []


def test_artifact_http_reads_saved_body_only_for_owner_and_existing_domain(store, monkeypatch):
    import app.api.agent as agent_api

    reply = "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末独立复述并调整下周方向。"
    saved = store.create_from_reply(reply, student_id="owner_1", session_id="sess_1")
    committer = ArtifactCommitter(store.mysql)
    monkeypatch.setattr(committer, "_ensure_schema", lambda: None)
    monkeypatch.setattr(agent_api, "get_artifact_committer", lambda: committer)
    current_owner = ["owner_1"]
    monkeypatch.setattr(agent_api, "resolve_request_scope", lambda **_kwargs: RequestScope(
        Principal(f"test:{current_owner[0]}", current_owner[0], source="test"),
    ))
    app = Flask(__name__)
    app.register_blueprint(agent_bp, url_prefix="/api/agent")
    client = app.test_client()
    path = f"/api/agent/artifacts/{saved['artifact_id']}"

    own = client.get(path)
    assert own.status_code == 200
    assert own.get_json()["data"]["content"]["body"] == reply
    assert own.get_json()["data"]["session_id"] == "sess_1"

    current_owner[0] = "owner_2"
    assert client.get(path).status_code == 404

    current_owner[0] = "owner_1"
    with store.mysql.engine.begin() as conn:
        conn.execute(text("DELETE FROM agent_action_plan WHERE plan_id = :plan_id"), {"plan_id": saved["plan_id"]})
    assert client.get(path).status_code == 404


def test_planner_persistence_failure_is_partial_not_a_completed_artifact(store, monkeypatch):
    class RouterLLM:
        def chat_json(self, **_kwargs):
            return {"action": "call", "subagent": "planner", "goal": "制定本周学习计划"}

    def invoke(_packet, _blackboard, **_kwargs):
        return {"reply": "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末复盘并调整下周方向。", "tool_calls": []}

    with store.mysql.engine.begin() as conn:
        conn.execute(text("DROP TABLE agent_artifact"))
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: RouterLLM())
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.invoke_subagent_graph", invoke)
    result = ModeRuntime().chat(
        "制定本周学习计划", interaction_mode="agent", student_id="owner_1",
        session_id="sess_1", use_memory=False,
    )

    assert derive_turn_outcome(result)["status"] == "partial"
    assert result["orchestration"]["plan_results"][0]["ok"] is False
    assert "plan.current" not in result["blackboard"]
    assert project_turn(result).artifacts == []
    assert store.mysql.fetch_all("SELECT plan_id FROM agent_action_plan") == []


def test_experts_planner_step_accepts_only_persisted_pointer():
    packet = build_standard_packet("planner", "制定学习计划")
    persisted = {"plan.current": {
        "plan_id": "plan_1", "resource_id": "plan_1", "artifact_id": "art_1",
        "status": "completed", "persisted": True,
    }}
    results, _ = run_plan(
        [packet], Blackboard(), lambda *_: {"reply": "计划已生成。"},
        verify_updates=lambda *_: persisted,
    )
    assert results[0].ok is True
    assert results[0].blackboard_updates == persisted

    missing, _ = run_plan(
        [packet], Blackboard(), lambda *_: {"reply": "计划已生成。"},
        verify_updates=lambda *_: {},
    )
    assert missing[0].ok is False
