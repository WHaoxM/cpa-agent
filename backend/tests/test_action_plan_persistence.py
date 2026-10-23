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
                    handoff_id TEXT, report_uri TEXT, report_version TEXT,
                    resume_version TEXT, gap_summary TEXT, exploration_round_id TEXT,
                    created_at DATETIME, updated_at DATETIME,
                    UNIQUE(student_id, handoff_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_action_plan_step (
                    step_id TEXT PRIMARY KEY, plan_id TEXT, position INTEGER,
                    title TEXT, detail TEXT, completed INTEGER, completed_at DATETIME,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_action_plan_step_evidence (
                    step_id TEXT PRIMARY KEY, plan_id TEXT, student_id TEXT,
                    summary TEXT, attachment_file_id TEXT,
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
    reply = "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末独立复述并调整下周方向。"

    def invoke(_packet, _blackboard, **_kwargs):
        return {"reply": reply, "tool_calls": [], "permission_denials": []}

    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.experts_lead_decide", lambda *a, **k: {
        "action": "run_plan", "source": "test", "goal_summary": "制定本周学习计划",
        "steps": [{"role": "planner", "goal": "制定本周学习计划", "skill_ids": []}],
    })
    monkeypatch.setattr(
        ModeRuntime, "_finalize_plan_reply",
        lambda _self, **_kwargs: "本轮已根据步骤结果整理计划回复。",
    )
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


def test_structured_plan_progress_is_owner_scoped_idempotent_and_derived(store):
    saved = store.create_from_steps(
        [
            {"title": "整理岗位技能", "detail": "从报告里挑出一项明确要求，并写成自查清单。"},
            {"title": "补一个小练习", "detail": "围绕简历中尚未体现的技能完成一份可检查的小练习。"},
            {"title": "回看并记录", "detail": "用一页记录练习结果和仍需补充的证据。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "a" * 32,
        report_uri="career://me/reports/report_12345678", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="报告指出缺少可验证的数据分析练习。",
    )
    assert saved["status"] == "not_started"
    assert saved["progress"] == {"completed": 0, "total": 3, "status": "not_started"}
    assert len(saved["steps"]) == 3
    same_handoff = store.create_from_steps(
        [
            {"title": "改写后不可覆盖", "detail": "重复消费同一个交接不应产生另一份正式计划。"},
            {"title": "改写后仍不可覆盖", "detail": "第一次持久化的计划步骤和成果标识必须保持不变。"},
            {"title": "不产生重复成果", "detail": "本次重放只应回读原有 owner-bound 计划。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "a" * 32,
        report_uri="career://me/reports/report_12345678", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="新的模型输出不覆盖已提交计划。",
    )
    assert same_handoff["plan_id"] == saved["plan_id"]
    assert same_handoff["artifact"]["artifact_id"] == saved["artifact"]["artifact_id"]
    first_id = saved["steps"][0]["step_id"]

    updated = store.set_step_completed(saved["plan_id"], first_id, True, student_id="owner_1")
    assert updated["progress"] == {"completed": 1, "total": 3, "status": "in_progress"}
    repeated = store.set_step_completed(saved["plan_id"], first_id, True, student_id="owner_1")
    assert repeated["progress"] == updated["progress"]
    assert store.set_step_completed(saved["plan_id"], first_id, False, student_id="owner_2") is None
    still_owner = store.get_for_owner(saved["plan_id"], student_id="owner_1")
    assert still_owner["progress"] == updated["progress"]
    assert sum(step["completed"] for step in still_owner["steps"]) == 1


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


def test_step_progress_http_requires_owner_and_exact_boolean_body(store, monkeypatch):
    import app.api.agent as agent_api

    saved = store.create_from_steps(
        [
            {"title": "列出差距", "detail": "按本人资料列出报告中明确指出的一项能力差距。"},
            {"title": "完成练习", "detail": "围绕差距做一项小练习，并保留可检查的结果。"},
            {"title": "复盘结果", "detail": "记录练习结果和下一步仍需补充的证据。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "b" * 32,
        report_uri="career://me/reports/report_abcdefgh", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="报告指出缺少可验证的项目练习。",
    )
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
    step_id = saved["steps"][0]["step_id"]
    path = f"/api/agent/action-plans/{saved['plan_id']}/steps/{step_id}"

    bad = client.patch(path, json={"completed": True, "student_id": "owner_1"})
    assert bad.status_code == 400
    current_owner[0] = "owner_2"
    assert client.patch(path, json={"completed": True}).status_code == 404
    assert store.get_for_owner(saved["plan_id"], student_id="owner_1")["progress"]["completed"] == 0

    current_owner[0] = "owner_1"
    response = client.patch(path, json={"completed": True})
    assert response.status_code == 200
    assert response.get_json()["data"]["progress"] == {
        "completed": 1, "total": 3, "status": "in_progress",
    }
    repeated = client.patch(path, json={"completed": True})
    assert repeated.status_code == 200
    assert repeated.get_json()["data"]["progress"]["completed"] == 1


def test_student_practice_evidence_is_owner_scoped_updatable_and_independent_of_progress(store):
    saved = store.create_from_steps(
        [
            {"title": "找出差距", "detail": "从报告中挑出一项差距，写成练习目标。"},
            {"title": "完成练习", "detail": "围绕这个目标完成一个可检查的小练习。"},
            {"title": "复盘", "detail": "说明哪里符合预期、哪里还没有被证明。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "e" * 32,
        report_uri="career://me/reports/report_evidence", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="缺少可验证的练习记录。",
    )
    first_step = saved["steps"][0]
    recorded = store.save_step_evidence(
        saved["plan_id"], first_step["step_id"], "我完成了一个合成样例，并记录了输入和结果。",
        None, student_id="owner_1",
    )
    assert recorded["steps"][0]["evidence"]["source"] == "student_submitted"
    assert recorded["steps"][0]["evidence"]["verified"] is False
    assert recorded["progress"]["completed"] == 0
    same = store.save_step_evidence(
        saved["plan_id"], first_step["step_id"], "我完成了一个合成样例，并记录了输入和结果。",
        None, student_id="owner_1",
    )
    assert same["steps"][0]["evidence"]["summary"] == recorded["steps"][0]["evidence"]["summary"]
    updated = store.save_step_evidence(
        saved["plan_id"], first_step["step_id"], "我重新检查后补充了失败样例和复现步骤。",
        None, student_id="owner_1",
    )
    assert updated["steps"][0]["evidence"]["summary"].startswith("我重新检查")
    assert updated["steps"][0]["completed"] is False
    assert updated["body"] == saved["body"]
    assert store.save_step_evidence(
        saved["plan_id"], first_step["step_id"], "越权尝试。", None, student_id="owner_2",
    ) is None
    assert store.save_step_evidence(
        saved["plan_id"], "step_other", "伪造步骤。", None, student_id="owner_1",
    ) is None
    evidence_count = store.mysql.fetch_all(
        "SELECT COUNT(*) AS n FROM agent_action_plan_step_evidence WHERE plan_id = :plan_id",
        {"plan_id": saved["plan_id"]},
    )[0]["n"]
    assert evidence_count == 1


def test_step_evidence_http_strict_body_owner_and_attachment_session(store, monkeypatch):
    import app.api.agent as agent_api
    from app.services.agent_runtime.media import file_store

    saved = store.create_from_steps(
        [
            {"title": "记录目标", "detail": "记录本周练习要验证的问题。"},
            {"title": "动手练习", "detail": "完成一个小规模的样例。"},
            {"title": "做复盘", "detail": "写下结果和仍缺的证据。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "f" * 32,
        report_uri="career://me/reports/report_http_ev", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="练习成果未记录。",
    )
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
    step_id = saved["steps"][0]["step_id"]
    path = f"/api/agent/action-plans/{saved['plan_id']}/steps/{step_id}/evidence"

    assert client.put(path, json={"summary": "记录。", "student_id": "owner_1"}).status_code == 400
    current_owner[0] = "owner_2"
    assert client.put(path, json={"summary": "不应写入的记录。"}).status_code == 404
    current_owner[0] = "owner_1"
    saved_response = client.put(path, json={"summary": "记录一个有输入和输出的合成练习。"})
    assert saved_response.status_code == 200
    assert saved_response.get_json()["data"]["steps"][0]["evidence"]["verified"] is False
    assert "file_id" not in str(saved_response.get_json())
    assert client.put(path, json={"summary": "记录一个有输入和输出的合成练习。"}).status_code == 200
    assert store.get_for_owner(saved["plan_id"], student_id="owner_1")["progress"]["completed"] == 0
    assert store.mysql.fetch_all(
        "SELECT COUNT(*) AS n FROM agent_action_plan_step_evidence WHERE step_id = :step_id",
        {"step_id": step_id},
    )[0]["n"] == 1


def test_step_evidence_rejects_other_owner_or_session_attachment_before_write(store, monkeypatch):
    import app.api.agent as agent_api
    from app.services.agent_runtime.media import file_store

    saved = store.create_from_steps(
        [
            {"title": "明确问题", "detail": "选择一个具体的练习问题。"},
            {"title": "完成练习", "detail": "完成一项可以回看的练习。"},
            {"title": "记录发现", "detail": "记录结果和仍待确认的事项。"},
        ],
        student_id="owner_1", session_id="sess_1", handoff_id="aph_" + "g" * 32,
        report_uri="career://me/reports/report_file_scope", report_version="report_v1",
        resume_version="resume_task_v1", gap_summary="缺少结果记录。",
    )
    committer = ArtifactCommitter(store.mysql)
    monkeypatch.setattr(committer, "_ensure_schema", lambda: None)
    monkeypatch.setattr(agent_api, "get_artifact_committer", lambda: committer)
    monkeypatch.setattr(agent_api, "resolve_request_scope", lambda **_kwargs: RequestScope(
        Principal("test:owner_1", "owner_1", source="test"),
    ))
    meta = {"file_other_owner": {
        "student_id": "owner_2", "session_id": "sess_1", "filename": "other.txt",
    }, "file_other_session": {
        "student_id": "owner_1", "session_id": "sess_2", "filename": "other-session.txt",
    }, "file_valid": {
        "student_id": "owner_1", "session_id": "sess_1", "filename": "practice.txt",
    }}
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: type("Files", (), {
        "get": lambda _self, file_id: meta.get(file_id),
    })())
    app = Flask(__name__)
    app.register_blueprint(agent_bp, url_prefix="/api/agent")
    client = app.test_client()
    step_id = saved["steps"][0]["step_id"]
    path = f"/api/agent/action-plans/{saved['plan_id']}/steps/{step_id}/evidence"
    for file_id in ("file_other_owner", "file_other_session"):
        response = client.put(path, json={"summary": "附件不应越权关联。", "attachment_id": file_id})
        assert response.status_code == 404
    assert store.mysql.fetch_all(
        "SELECT step_id FROM agent_action_plan_step_evidence WHERE plan_id=:plan_id",
        {"plan_id": saved["plan_id"]},
    ) == []
    valid = client.put(path, json={"summary": "我记录了练习步骤。", "attachment_id": "file_valid"})
    assert valid.status_code == 200
    assert valid.get_json()["data"]["steps"][0]["evidence"]["attachment"]["filename"] == "practice.txt"
    assert "file_valid" not in str(valid.get_json())


def test_planner_persistence_failure_is_partial_not_a_completed_artifact(store, monkeypatch):
    def invoke(_packet, _blackboard, **_kwargs):
        return {"reply": "本周学习计划：周一选定主题；周二至周四每天练习三十分钟；周五整理一页成果；周末复盘并调整下周方向。", "tool_calls": []}

    with store.mysql.engine.begin() as conn:
        conn.execute(text("DROP TABLE agent_artifact"))
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.experts_lead_decide", lambda *a, **k: {
        "action": "run_plan", "source": "test", "goal_summary": "制定本周学习计划",
        "steps": [{"role": "planner", "goal": "制定本周学习计划", "skill_ids": []}],
    })
    monkeypatch.setattr(
        ModeRuntime, "_finalize_plan_reply",
        lambda _self, **_kwargs: "本轮有步骤未能完成，未把它当作已交付成果。",
    )
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
