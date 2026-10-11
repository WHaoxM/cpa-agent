"""Public turn status must reflect runtime evidence, not fluent reply text."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

from app.services.agent_runtime.egress_pipeline import project_session_messages, project_turn
from app.services.agent_runtime.turn_contract import normalize_artifact_ref
from app.services.agent_runtime.tool_result_channels import build_ui_view


def test_answer_only_turn_is_complete_but_only_at_turn_scope():
    turn = project_turn({"reply": "可以先补足项目经历。", "session_id": "s_1"}).to_dict()

    assert turn["outcome"]["status"] == "completed"
    assert turn["outcome"]["completion_scope"] == "turn"
    assert turn["outcome"]["resume_state"] == "not_available"
    assert turn["events"][-1]["outcome"] == turn["outcome"]


def test_artifact_without_authoritative_status_is_not_completed():
    assert normalize_artifact_ref({"artifact_id": "a1", "kind": "portrait"})["status"] == "unverified"

    candidate = normalize_artifact_ref({
        "artifact_id": "a2", "kind": "pipeline_task", "status": "needs_input",
        "preview": {"task_id": "task_confirm123"},
        "actions": [{"id": "confirm_resume", "label": "核对简历"}],
    })
    assert candidate["actions"] == [{"id": "confirm_resume", "label": "核对简历"}]
    assert candidate["preview"]["task_id"] == "task_confirm123"


def test_experts_clarification_is_not_reported_as_completed():
    turn = project_turn({
        "reply": "请补充目标岗位。",
        "orchestration": {
            "strategy": "experts_clarify",
            "supervisor_view": {"lead_action": "clarify", "missing_slots": ["目标岗位"]},
        },
    }).to_dict()

    assert turn["outcome"]["status"] == "needs_input"
    assert turn["outcome"]["next_action"] == "answer_in_session"
    assert turn["outcome"]["resume_state"] == "not_available"

    agent = project_turn({
        "reply": "你想先做画像还是匹配？",
        "orchestration": {"strategy": "agent_clarify"},
    }).to_dict()
    assert agent["outcome"]["status"] == "needs_input"

    blocked = project_turn({
        "reply": "请先补齐简历。",
        "orchestration": {"strategy": "gate_block"},
    }).to_dict()
    assert blocked["outcome"]["status"] == "partial"


def test_partial_document_and_failed_tool_do_not_claim_completion():
    partial = project_turn({
        "reply": "已先分析可读取的部分。",
        "orchestration": {"supervisor_view": {"doc_pack": {"all_complete": False}}},
    }).to_dict()
    assert partial["outcome"]["status"] == "partial"
    assert partial["outcome"]["reason"] == "document_incomplete"

    mount_failed = project_turn({
        "reply": "附件未完整读取。",
        "orchestration": {"mount_errors": ["file unavailable"]},
    }).to_dict()
    assert mount_failed["outcome"]["status"] == "partial"

    failed = project_turn({
        "reply": "",
        "tool_calls": [{"tool_name": "pipeline_run", "status": "failed", "success": False}],
    }).to_dict()
    assert failed["outcome"]["status"] == "failed"

    unavailable = build_ui_view("semantic_search", "[semantic_search] 不可用: 旧向量未隔离")
    assert unavailable["status"] == "partial"  # legacy prose cannot attest a structured result
    turn = project_turn({"reply": "检索暂不可用。", "tool_calls": [unavailable]}).to_dict()
    assert turn["outcome"]["status"] == "partial"

    tool_partial = project_turn({
        "reply": "已有草稿，但没有持久化报告。",
        "tool_calls": [{"tool_name": "report_generate", "status": "partial"}],
    }).to_dict()
    assert tool_partial["outcome"]["status"] == "partial"
    assert tool_partial["events"][-1]["outcome"] == tool_partial["outcome"]


def test_tool_request_for_user_confirmation_is_needs_input_not_partial():
    view = build_ui_view("pipeline_run", {
        "status": "needs_input",
        "task_id": "task_confirm123",
        "summary": "请核对简历候选并确认",
    })
    assert view["status"] == "needs_input"
    turn = project_turn({"reply": "请确认候选资料。", "tool_calls": [view]}).to_dict()
    assert turn["outcome"]["status"] == "needs_input"
    assert turn["outcome"]["next_action"] == "answer_in_session"


def test_running_requires_authoritative_run_reference_and_no_fake_resume():
    untracked = project_turn({"reply": "处理中", "turn_status": "running"}).to_dict()
    assert untracked["outcome"]["status"] == "partial"
    assert untracked["outcome"]["reason"] == "untracked_running"

    tracked = project_turn({
        "reply": "任务已提交。",
        "turn_status": "running",
        "run_id": "task_abc123",
    }).to_dict()
    assert tracked["outcome"]["status"] == "running"
    assert tracked["outcome"]["run_id"] == "task_abc123"
    assert tracked["outcome"]["next_action"] == "poll_run"
    assert tracked["outcome"]["resume_state"] == "not_available"


def test_history_exposes_same_status_without_internal_resume_payload():
    row = {
        "role": "assistant",
        "content": "请补充目标岗位。",
        "metadata": {
            "turn_id": "turn_1",
            "outcome": {
                "status": "needs_input",
                "completion_scope": "turn",
                "next_action": "answer_in_session",
                "resume_state": "not_available",
                "private_checkpoint": "do-not-expose",
            },
        },
    }
    public = project_session_messages([row])[0]
    assert public["outcome"]["status"] == "needs_input"
    assert "private_checkpoint" not in public["outcome"]


def test_chat_json_sse_and_persisted_message_share_outcome(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    class SessionManagerStub:
        def __init__(self):
            self.messages = []

        def add_message(self, **kwargs):
            self.messages.append(kwargs)

    session_mgr = SessionManagerStub()
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: session_mgr)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kwargs: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )
    raw = {"reply": "请补充目标岗位。", "session_id": "s1", "orchestration": {"strategy": "agent_clarify"}}
    monkeypatch.setattr(agent_api, "_run_supervisor", lambda *_args, **_kwargs: (project_turn(raw).to_dict(), raw))

    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "我该怎么办"})
    assert sync.status_code == 200
    expected = sync.get_json()["data"]["outcome"]
    assert expected["status"] == "needs_input"
    assistant_rows = [m for m in session_mgr.messages if m.get("role") == "assistant"]
    assert assistant_rows[-1]["metadata"]["outcome"] == expected

    stream = client.post("/api/agent/chat/stream", json={"message": "我该怎么办"})
    assert stream.status_code == 200
    events = [json.loads(line[6:]) for line in stream.get_data(as_text=True).splitlines() if line.startswith("data: {")]
    assert events[-1]["type"] == "turn_done"
    assert events[-1]["outcome"] == expected


def test_chat_and_sse_share_media_only_turn_admission_and_partial_outcome(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    class SessionManagerStub:
        def __init__(self):
            self.messages = []

        def add_message(self, **kwargs):
            self.messages.append(kwargs)

    session_mgr = SessionManagerStub()
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: session_mgr)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))

    prepare_calls = []
    supervisor_calls = []
    prepared = {
        "visible_text": "[附件] 简历",
        "turn_file_ids": ["file_1"],
        "parts": [{"type": "file", "file_id": "file_1"}],
        "mount_errors": ["文件尚未就绪"],
        "voice_deduped": True,
    }

    def prepare(text, **kwargs):
        prepare_calls.append((text, kwargs))
        return dict(prepared)

    def run_supervisor(message, session_id, session, manager, **kwargs):
        supervisor_calls.append((message, kwargs))
        raw = {
            "reply": "附件暂不可用，请稍后重试。",
            "session_id": session_id,
            "orchestration": {"mount_errors": kwargs["prepared_turn"]["mount_errors"]},
        }
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr("app.services.agent_runtime.media.prepare_turn.prepare_user_turn", prepare)
    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)

    payload = {
        "attachment_ids": ["file_1"],
        "content_parts": [{"type": "file", "file_id": "file_1"}],
        "voice_turn_id": "voice_1",
    }
    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json=payload)
    stream = client.post("/api/agent/chat/stream", json=payload)

    assert sync.status_code == stream.status_code == 200
    sync_outcome = sync.get_json()["data"]["outcome"]
    events = [json.loads(line[6:]) for line in stream.get_data(as_text=True).splitlines() if line.startswith("data: {")]
    assert events[-1]["type"] == "turn_done"
    assert events[-1]["outcome"] == sync_outcome
    assert sync_outcome["status"] == "partial"
    assert sync_outcome["reason"] == "input_or_permission_incomplete"
    assert len(prepare_calls) == 2  # once per request, never again in Supervisor
    assert all(call[0] == "" and call[1]["mount"] is True for call in prepare_calls)
    assert len(supervisor_calls) == 2
    assert all(call[0] == "" for call in supervisor_calls)
    assert all(call[1]["prepared_turn"] == prepared for call in supervisor_calls)
    assert all(call[1]["attachment_ids"] == ["file_1"] for call in supervisor_calls)
    assert all(call[1]["content_parts"] == payload["content_parts"] for call in supervisor_calls)
    assert all(call[1]["voice_turn_id"] is None for call in supervisor_calls)
    users = [m for m in session_mgr.messages if m["role"] == "user"]
    assert len(users) == 2
    assert users[0]["content"] == users[1]["content"] == "[附件] 简历"
    assert users[0]["metadata"] == users[1]["metadata"]


def test_sse_rejects_invalid_media_reference_before_stm_write(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    messages = []
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(add_message=lambda **kw: messages.append(kw)))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("资料不属于当前用户")),
    )

    response = create_app().test_client().post(
        "/api/agent/chat/stream", json={"content_parts": [{"type": "resource_ref", "uri": "resume:other"}]},
    )
    assert response.status_code == 200
    assert '"code": "RESOURCE_NOT_FOUND"' in response.get_data(as_text=True)
    assert messages == []


def test_sse_media_only_request_uses_real_turn_preparation(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    messages = []
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(add_message=lambda **kw: messages.append(kw)))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.resolve_attachment_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("file not ready")),
    )
    monkeypatch.setattr("app.services.agent_runtime.media.context_mount.list_resources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.services.agent_runtime.media.context_mount.get_agent_file_service",
        lambda: SimpleNamespace(get=lambda _fid: {
            "filename": "resume.pdf", "status": "pending", "student_id": "demo_student",
        }),
    )

    observed = []

    def run_supervisor(message, session_id, session, manager, **kwargs):
        observed.append(kwargs["prepared_turn"])
        raw = {
            "reply": "附件未就绪。",
            "session_id": session_id,
            "orchestration": {"mount_errors": kwargs["prepared_turn"]["mount_errors"]},
        }
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)
    response = create_app().test_client().post(
        "/api/agent/chat/stream", json={"attachment_ids": ["file_1"]},
    )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: {")]
    assert events[-1]["outcome"]["status"] == "partial"
    assert observed[0]["turn_file_ids"] == ["file_1"]
    assert observed[0]["mount_errors"] == ["file not ready"]
    assert messages[0]["metadata"]["mount_errors"] == ["file not ready"]


def test_sse_opens_connection_before_supervisor_execution(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(add_message=lambda **_kw: None))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kwargs: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )
    invoked = []

    def run_supervisor(message, session_id, session, manager, **_kwargs):
        invoked.append(message)
        raw = {"reply": "已回答。", "session_id": session_id}
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)
    response = create_app().test_client().post("/api/agent/chat/stream", json={"message": "你好"}, buffered=False)
    assert response.status_code == 200
    assert invoked == []  # first SSE byte/header precedes model/tool work
    first_chunk = next(response.response)
    assert first_chunk == b": connected\n\n"
    assert invoked == []
    assert '"type": "turn_done"' in response.get_data(as_text=True)
    assert invoked == ["你好"]


def test_cross_owner_attachment_is_rejected_before_stm_and_model_card(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    messages = []
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(add_message=lambda **kw: messages.append(kw)))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr("app.services.agent_runtime.media.session_resources.list_resources", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "app.services.agent_runtime.media.session_resources.get_agent_file_service",
        lambda: SimpleNamespace(get=lambda _fid: {
            "student_id": "other_student", "filename": "private-resume.pdf", "status": "pending",
        }),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.build_attachment_mount_card",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not build model card")),
    )

    client = create_app().test_client()
    for path in ("/api/agent/chat", "/api/agent/chat/stream"):
        response = client.post(path, json={"attachment_ids": ["file_foreign"]})
        assert response.status_code == (200 if path.endswith("/stream") else 404)
        if path.endswith("/stream"):
            assert '"code": "RESOURCE_NOT_FOUND"' in response.get_data(as_text=True)
        assert "private-resume.pdf" not in response.get_data(as_text=True)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.session_resources.get_agent_file_service",
        lambda: SimpleNamespace(get=lambda _fid: None),
    )
    for path in ("/api/agent/chat", "/api/agent/chat/stream"):
        response = client.post(path, json={"attachment_ids": ["file_missing"]})
        assert response.status_code == (200 if path.endswith("/stream") else 404)
        if path.endswith("/stream"):
            assert '"code": "RESOURCE_NOT_FOUND"' in response.get_data(as_text=True)
    assert messages == []


def test_voice_turn_owner_and_session_are_checked_before_stm_write(monkeypatch, tmp_path):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config
    from app.services.agent_runtime.media.voice_turn import VoiceTurnService

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    monkeypatch.setattr(
        "app.services.agent_runtime.media.voice_turn._voice_path",
        lambda tid: tmp_path / f"{tid}.json",
    )

    class NoDatabase:
        def execute(self, *_args, **_kwargs):
            return None

    voice = VoiceTurnService(mysql_client=NoDatabase())
    foreign_owner = voice.start(session_id="s1", student_id="other_student")
    foreign_session = voice.start(session_id="other_session", student_id="demo_student")
    for turn in (foreign_owner, foreign_session):
        voice.update(turn["turn_id"], transcript="private transcript", is_final=True)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.get_voice_turn_service", lambda: voice,
    )

    messages = []
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(add_message=lambda **kw: messages.append(kw)))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))

    client = create_app().test_client()
    for turn in (foreign_owner, foreign_session):
        for path in ("/api/agent/chat", "/api/agent/chat/stream"):
            response = client.post(path, json={"voice_turn_id": turn["turn_id"]})
            assert response.status_code == (200 if path.endswith("/stream") else 404)
            if path.endswith("/stream"):
                assert '"code": "RESOURCE_NOT_FOUND"' in response.get_data(as_text=True)
            assert "private transcript" not in response.get_data(as_text=True)
    assert messages == []


def test_voice_turn_start_requires_session_for_later_bound_use():
    from app import create_app

    response = create_app().test_client().post(
        "/api/agent/voice/turns", json={"lang": "zh-CN"},
    )
    assert response.status_code == 400


def test_same_session_turns_serialize_admission_through_assistant_settlement(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import sleep

    from app.api import agent as agent_api
    from app.services.agent_runtime.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator()
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kwargs: {
            "visible_text": text, "turn_file_ids": [], "parts": [],
            "mount_errors": [], "voice_deduped": False,
        },
    )

    class SessionManagerStub:
        def __init__(self):
            self.messages = []

        def get_session(self, _sid):
            return SimpleNamespace(student_id="demo_student", context={})

        def add_message(self, **kwargs):
            self.messages.append((kwargs["role"], kwargs["content"]))

    manager = SessionManagerStub()
    first_running = Event()
    release_first = Event()
    second_attempting = Event()

    def run_supervisor(message, session_id, session, session_mgr, **_kwargs):
        if message == "first":
            first_running.set()
            assert release_first.wait(5)
        raw = {"reply": f"reply {message}", "session_id": session_id}
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)

    def invoke(message):
        if message == "second":
            second_attempting.set()
        return agent_api._run_recorded_supervisor_turn(
            manager, "s1", manager.get_session("s1"), "demo_student", message,
            plan_mode=False, confirmed_constraints=None, interaction_mode=None,
            attachment_ids=[], content_parts=None, voice_turn_id=None,
            use_memory=None, engine="supervisor",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, "first")
        assert first_running.wait(5)
        second = pool.submit(invoke, "second")
        assert second_attempting.wait(5)
        sleep(0.05)
        assert manager.messages == [("user", "first")]
        release_first.set()
        assert first.result(timeout=5)["reply"] == "reply first"
        assert second.result(timeout=5)["reply"] == "reply second"
    assert manager.messages == [
        ("user", "first"), ("assistant", "reply first"),
        ("user", "second"), ("assistant", "reply second"),
    ]
