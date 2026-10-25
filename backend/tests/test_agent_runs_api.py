"""HTTP contracts for owner-bound Agent run persistence-only endpoints."""

from flask import Flask, g, request
import pytest
from sqlalchemy import create_engine, event, text
from types import SimpleNamespace

from app.api import agent_bp
from app.api._helpers import Principal
from app.api import agent_runs
from app.config import Config
from app.services.agent_runtime.agent_run_store import AgentRunRepository
from app.services.agent_runtime import agent_run_jobs


class SQLiteRunStoreDB:
    """Isolated HTTP-test database with the run repository's required tables."""

    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        event.listen(
            self.engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE agent_run (
                    run_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    idempotency_key_sha256 TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    input_snapshot_version INTEGER NOT NULL,
                    input_snapshot_redaction_version INTEGER NOT NULL,
                    input_snapshot_bytes INTEGER NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    input_snapshot_sha256 TEXT NOT NULL,
                    principal_id TEXT,
                    principal_source TEXT,
                    principal_auth_epoch INTEGER,
                    status TEXT NOT NULL,
                    current_plan_version INTEGER NOT NULL DEFAULT 0,
                    last_event_seq INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at DATETIME,
                    final_result_json TEXT,
                    final_result_sha256 TEXT,
                    finalized_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE(student_id, session_id, idempotency_key_sha256),
                    UNIQUE(run_id, student_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_plan (
                    run_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    goal_summary TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    PRIMARY KEY(run_id, plan_version),
                    FOREIGN KEY(run_id) REFERENCES agent_run(run_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_step (
                    run_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    step_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    skill_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    result_sha256 TEXT,
                    error_code TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(run_id, plan_version, step_index),
                    FOREIGN KEY(run_id, plan_version)
                        REFERENCES agent_run_plan(run_id, plan_version)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_event (
                    run_id TEXT NOT NULL,
                    student_id TEXT NOT NULL,
                    event_seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    run_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at DATETIME NOT NULL,
                    PRIMARY KEY(run_id, event_seq),
                    FOREIGN KEY(run_id, student_id) REFERENCES agent_run(run_id, student_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_dispatch (
                    run_id TEXT PRIMARY KEY,
                    dispatch_state TEXT NOT NULL,
                    publish_token TEXT,
                    publish_lease_until DATETIME,
                    publish_attempts INTEGER NOT NULL DEFAULT 0,
                    next_publish_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_enqueued_at DATETIME,
                    claim_token TEXT,
                    lease_until DATETIME,
                    claim_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES agent_run(run_id) ON DELETE CASCADE
                )
            """))

    def fetch_one(self, statement, params=None):
        with self.engine.connect() as conn:
            row = conn.execute(text(statement), params or {}).mappings().first()
            return dict(row) if row is not None else None

    def fetch_all(self, statement, params=None):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(
                text(statement), params or {},
            ).mappings()]


@pytest.fixture
def run_api(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    database = SQLiteRunStoreDB()
    repository = AgentRunRepository(database)
    monkeypatch.setattr(agent_runs, "get_agent_run_repository", lambda: repository)

    sessions = {
        "session-alice": SimpleNamespace(session_id="session-alice", student_id="alice"),
        "session-mallory": SimpleNamespace(session_id="session-mallory", student_id="mallory"),
    }

    class AuthoritativeSessionManager:
        def refresh_session(self, session_id, *, require_authoritative=False):
            assert require_authoritative is True
            return sessions.get(session_id)

    monkeypatch.setattr(agent_runs, "get_session_manager", lambda: AuthoritativeSessionManager())
    monkeypatch.setattr(agent_run_jobs, "dispatch_agent_run", lambda *_a, **_k: "pending")

    app = Flask(__name__)
    app.register_blueprint(agent_bp, url_prefix="/api/agent")

    @app.before_request
    def inject_test_principal():
        owner = request.headers.get("X-Test-Owner")
        if owner:
            g.cpa_principal = Principal(
                principal_id=f"test:{owner}",
                student_id=owner,
                source="test_adapter",
            )

    yield app.test_client(), repository
    database.engine.dispose()


def _submit(client, *, owner="alice", key="run-http-key-01", message="请只读分析下一步", **extra):
    return client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": owner, "Idempotency-Key": key},
        json={"session_id": "session-alice", "message": message, **extra},
    )


def test_submit_is_authenticated_idempotent_persisted_and_reports_pending_transport(run_api):
    client, repository = run_api
    unauthenticated = client.post(
        "/api/agent/runs",
        headers={"Idempotency-Key": "run-http-key-01"},
        json={"session_id": "session-alice", "message": "hello"},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.get_json()["code"] == "AUTH_ERROR"

    created = _submit(client)
    assert created.status_code == 201
    created_data = created.get_json()["data"]
    run = created_data["run"]
    run_id = run["run_id"]
    assert run["student_id"] == "alice"
    assert run["status"] == "queued"
    assert run["last_event_seq"] == 1
    assert created_data["persistence_status"] == "stored"
    assert created_data["dispatch_status"] == "pending"
    assert "尚未确认进入队列" in created.get_json()["message"]

    # The authoritative repository can read the committed input after the
    # HTTP response, while ordinary status projection does not echo it.
    stored = repository.get_for_owner(run_id, student_id="alice")
    assert stored["status"] == "queued"
    assert repository.get_input_snapshot_for_owner(
        run_id, student_id="alice",
    )["message"] == "请只读分析下一步"
    assert "input_snapshot" not in stored

    replay = _submit(client)
    assert replay.status_code == 200
    assert replay.get_json()["data"]["created"] is False
    assert replay.get_json()["data"]["run"]["run_id"] == run_id
    assert repository.get_for_owner(run_id, student_id="alice")["last_event_seq"] == 1

    conflict = _submit(client, message="不同任务")
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "AGENT_RUN_IDEMPOTENCY_CONFLICT"


def test_submit_rejects_missing_key_forged_owner_and_unsupported_envelope(run_api):
    client, _repository = run_api
    no_key = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice"},
        json={"session_id": "session-alice", "message": "hello"},
    )
    assert no_key.status_code == 400

    forged = _submit(client, key="run-http-key-02", student_id="mallory")
    assert forged.status_code == 400

    unsupported = _submit(
        client,
        key="run-http-key-03",
        cookie="session-secret",
    )
    assert unsupported.status_code == 400

    assert _submit(client, key="bad").status_code == 400


def test_explicit_capability_is_allowlisted_mode_bound_owner_bound_and_idempotent(run_api):
    client, repository = run_api

    def run_count():
        return repository.mysql.fetch_one(
            "SELECT COUNT(*) AS count FROM agent_run",
        )["count"]

    initial_count = run_count()
    unknown = _submit(
        client, key="run-capability-unknown",
        interaction_mode="experts", expert_id="career-coach",
        capability_id="not-in-registry",
    )
    assert unknown.status_code == 400
    assert "allowlist" in unknown.get_json()["error"]

    wrong_pair = _submit(
        client, key="run-capability-pair",
        interaction_mode="experts", expert_id="career-advisor",
        capability_id="career-planning",
    )
    assert wrong_pair.status_code == 400

    wrong_mode = _submit(
        client, key="run-capability-mode",
        interaction_mode="agent", expert_id="career-coach",
        capability_id="career-planning",
    )
    assert wrong_mode.status_code == 400

    wrong_owner = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "run-capability-owner"},
        json={
            "session_id": "session-mallory", "message": "请提供职业规划建议",
            "interaction_mode": "experts", "expert_id": "career-coach",
            "capability_id": "career-planning",
        },
    )
    assert wrong_owner.status_code == 403
    assert run_count() == initial_count

    selected = _submit(
        client, key="run-capability-repeat",
        interaction_mode="experts", expert_id="career-coach",
        capability_id="career-planning",
    )
    assert selected.status_code == 201
    run_id = selected.get_json()["data"]["run"]["run_id"]
    snapshot = repository.get_input_snapshot_for_owner(run_id, student_id="alice")
    assert snapshot["interaction_mode"] == "experts"
    assert snapshot["expert_id"] == "career-coach"
    assert snapshot["capability_id"] == "career-planning"

    repeated = _submit(
        client, key="run-capability-repeat",
        interaction_mode="experts", expert_id="career-coach",
        capability_id="career-planning",
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["data"]["created"] is False
    assert repeated.get_json()["data"]["run"]["run_id"] == run_id

    conflict = _submit(
        client, key="run-capability-repeat", message="换一个目标",
        interaction_mode="experts", expert_id="career-coach",
        capability_id="career-planning",
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "AGENT_RUN_IDEMPOTENCY_CONFLICT"
    assert run_count() == initial_count + 1


def test_legacy_experts_run_without_explicit_package_remains_accepted(run_api):
    client, repository = run_api
    response = _submit(
        client, key="run-legacy-experts",
        interaction_mode="experts",
    )
    assert response.status_code == 201
    run_id = response.get_json()["data"]["run"]["run_id"]
    snapshot = repository.get_input_snapshot_for_owner(run_id, student_id="alice")
    assert snapshot["interaction_mode"] == "experts"
    assert snapshot["expert_id"] is None
    assert snapshot["capability_id"] is None


def test_education_screening_package_requires_explicit_opt_in_and_persists_owner_bound_choice(run_api):
    client, repository = run_api
    response = _submit(
        client,
        key="run-education-screening-opt-in",
        message="公告要求硕士及以上；我仅有本科学历。",
        interaction_mode="experts",
        expert_id="career-coach",
        capability_id="education-screening",
    )
    assert response.status_code == 201
    run_id = response.get_json()["data"]["run"]["run_id"]
    snapshot = repository.get_input_snapshot_for_owner(run_id, student_id="alice")
    assert snapshot["capability_id"] == "education-screening"
    assert snapshot["expert_id"] == "career-coach"
    assert client.get(
        f"/api/agent/runs/{run_id}", headers={"X-Test-Owner": "mallory"},
    ).status_code == 404


def test_status_events_and_cancel_are_owner_bound_and_cancel_is_only_a_request(run_api):
    client, repository = run_api
    submitted = _submit(client)
    run_id = submitted.get_json()["data"]["run"]["run_id"]

    assert client.get(
        f"/api/agent/runs/{run_id}", headers={"X-Test-Owner": "mallory"},
    ).status_code == 404
    assert client.get(
        f"/api/agent/runs/{run_id}/events", headers={"X-Test-Owner": "mallory"},
    ).status_code == 404
    assert client.post(
        f"/api/agent/runs/{run_id}/cancel", headers={"X-Test-Owner": "mallory"},
    ).status_code == 404

    status = client.get(
        f"/api/agent/runs/{run_id}", headers={"X-Test-Owner": "alice"},
    )
    assert status.status_code == 200
    assert status.get_json()["data"]["dispatch_status"] == "pending"
    assert status.get_json()["data"]["run"]["status"] == "queued"

    page1 = client.get(
        f"/api/agent/runs/{run_id}/events?after_seq=0&limit=1",
        headers={"X-Test-Owner": "alice"},
    )
    assert page1.status_code == 200
    page1_data = page1.get_json()["data"]
    assert [item["event_seq"] for item in page1_data["events"]] == [1]
    assert page1_data["cursor"] == 1
    assert page1_data["has_more"] is False

    cancel = client.post(
        f"/api/agent/runs/{run_id}/cancel", headers={"X-Test-Owner": "alice"},
    )
    assert cancel.status_code == 200
    cancel_data = cancel.get_json()["data"]
    assert cancel_data["run"]["status"] == "cancelled"
    assert cancel_data["run"]["cancel_requested"] is True
    assert cancel_data["dispatch_status"] == "cancelled"
    assert "安全点生效" in cancel.get_json()["message"]

    # A replayed cancellation is idempotent and does not invent another event.
    again = client.post(
        f"/api/agent/runs/{run_id}/cancel", headers={"X-Test-Owner": "alice"},
    )
    assert again.status_code == 200
    assert again.get_json()["data"]["run"]["last_event_seq"] == 3
    all_events = repository.read_events(run_id, student_id="alice", limit=10)
    assert [event["event_type"] for event in all_events["events"]] == [
        "run.submitted", "run.cancel_requested", "run.cancelled",
    ]


def test_tool_call_event_contract_replays_through_owner_bound_http_cursor(run_api):
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.tool_event_contract import (
        TOOL_SETTLED_EVENT,
        TOOL_STARTED_EVENT,
        ToolEventContext,
        build_tool_event_payload,
        stable_assistant_message_id,
    )

    client, repository = run_api
    created = _submit(client, key="run-http-tool-events-01")
    assert created.status_code == 201
    run_id = created.get_json()["data"]["run"]["run_id"]
    claim = AgentRunDispatchStore(repository).claim_worker(run_id)
    assert claim is not None
    claim_token = claim["claim_token"]
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        goal_summary="Read a synthetic public record",
        steps=[{"role": "qa", "goal": "Read only the synthetic record", "skill_ids": []}],
        claim_token=claim_token,
    )
    repository.start_step(
        run_id, student_id="alice", plan_version=plan["plan_version"],
        step_index=1, claim_token=claim_token,
    )
    context = ToolEventContext(
        run_id=run_id, student_id="alice", session_id="session-alice",
        claim_token=claim_token, plan_version=plan["plan_version"],
        contract_revision=0, step_index=1, attempt=1,
        allowed_tool_names=frozenset({"career_data_read"}),
    )
    message_id = stable_assistant_message_id(
        run_id=run_id, contract_revision=0, plan_version=1,
        step_index=1, attempt=1, model_iteration=1,
    )
    start = build_tool_event_payload(
        context, event_type=TOOL_STARTED_EVENT,
        assistant_message_id=message_id, model_iteration=1,
        tool_call_id="http_call_01",
        tool_name="career_data_read", args={"query": "PRIVATE_QUERY_SENTINEL"},
    )
    start_seq = repository.append_tool_call_event(
        run_id, student_id="alice", session_id="session-alice",
        claim_token=claim_token, plan_version=1, contract_revision=0,
        step_index=1, attempt=1, allowed_tool_names={"career_data_read"},
        event_type=TOOL_STARTED_EVENT, payload=start,
    )
    settled = build_tool_event_payload(
        context, event_type=TOOL_SETTLED_EVENT,
        assistant_message_id=message_id, model_iteration=1,
        tool_call_id="http_call_01",
        tool_name="career_data_read",
        tool_message_content='{"status":"done","success":true,"summary":"PRIVATE_RESULT_SENTINEL"}',
    )
    settled_seq = repository.append_tool_call_event(
        run_id, student_id="alice", session_id="session-alice",
        claim_token=claim_token, plan_version=1, contract_revision=0,
        step_index=1, attempt=1, allowed_tool_names={"career_data_read"},
        event_type=TOOL_SETTLED_EVENT, payload=settled,
    )

    page = client.get(
        f"/api/agent/runs/{run_id}/events?after_seq={start_seq - 1}&limit=1",
        headers={"X-Test-Owner": "alice"},
    )
    assert page.status_code == 200
    page_data = page.get_json()["data"]
    assert page_data["events"][0]["event_type"] == TOOL_STARTED_EVENT
    assert page_data["cursor"] == start_seq
    assert page_data["has_more"] is True

    replay = client.get(
        f"/api/agent/runs/{run_id}/events?after_seq={start_seq}&limit=1",
        headers={"X-Test-Owner": "alice"},
    )
    assert replay.status_code == 200
    replay_data = replay.get_json()["data"]
    assert replay_data["events"][0]["event_type"] == TOOL_SETTLED_EVENT
    assert replay_data["events"][0]["event_seq"] == settled_seq
    assert replay_data["events"][0]["payload"]["assistant_message_id"] == message_id
    assert replay_data["has_more"] is False
    serialized = page.get_data(as_text=True) + replay.get_data(as_text=True)
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "PRIVATE_RESULT_SENTINEL" not in serialized

    wrong_owner = client.get(
        f"/api/agent/runs/{run_id}/events?after_seq=0&limit=20",
        headers={"X-Test-Owner": "mallory"},
    )
    assert wrong_owner.status_code == 404


@pytest.mark.parametrize("fail", [False, True])
def test_toolnode_execution_persists_typed_events_for_http_replay(run_api, monkeypatch, fail):
    import importlib
    import json
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.task_packet import TaskPacket
    from app.services.agent_runtime.tool_event_contract import ToolEventContext

    client, repository = run_api
    created = _submit(client, key=f"run-http-toolnode-{fail}-01")
    assert created.status_code == 201
    run_id = created.get_json()["data"]["run"]["run_id"]
    claim = AgentRunDispatchStore(repository).claim_worker(run_id)
    assert claim is not None
    claim_token = claim["claim_token"]
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        goal_summary="Read a synthetic public record",
        steps=[{"role": "qa", "goal": "Read only the synthetic record", "skill_ids": []}],
        claim_token=claim_token,
    )
    repository.start_step(
        run_id, student_id="alice", plan_version=plan["plan_version"],
        step_index=1, claim_token=claim_token,
    )

    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    executed = []
    model_inputs = []

    def read_synthetic(query: str) -> str:
        executed.append(query)
        if fail:
            raise ValueError("PRIVATE_TOOL_EXCEPTION_SENTINEL")
        return json.dumps({
            "status": "done", "success": True,
            "summary": "PRIVATE_TOOL_RESULT_SENTINEL",
        })

    base_tool = StructuredTool.from_function(
        func=read_synthetic, name="career_data_read", description="read a synthetic record",
    )
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *_args, **_kwargs: [],
    ))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, context, collector: [gated._wrap_one(base_tool, context, collector)],
    )

    class FakeLLM:
        def invoke(self, messages):
            model_inputs.append(list(messages))
            if len(model_inputs) == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read",
                    "args": {"query": "PRIVATE_TOOL_QUERY_SENTINEL"},
                    "id": "http_tool_call_01",
                }])
            return AIMessage(content="Synthetic read completed.")

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_args: FakeLLM())
    context = ToolEventContext(
        run_id=run_id, student_id="alice", session_id="session-alice",
        claim_token=claim_token, plan_version=plan["plan_version"],
        contract_revision=0, step_index=1, attempt=1,
        allowed_tool_names=frozenset({"career_data_read"}),
    )

    def persist_event(event_type, payload):
        return repository.append_tool_call_event(
            run_id, student_id="alice", session_id="session-alice",
            claim_token=claim_token, plan_version=plan["plan_version"],
            contract_revision=0, step_index=1, attempt=1,
            allowed_tool_names={"career_data_read"},
            event_type=event_type, payload=payload,
        )

    result = scoped.scoped_invoke(
        TaskPacket(goal="Read a synthetic record", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(), interaction_mode=InteractionMode.AGENT, student_id="alice",
        plan_tool_allowlist={"career_data_read"}, allow_artifact_commit=False,
        tool_event_context=context, tool_event_sink=persist_event,
    )
    assert executed == ["PRIVATE_TOOL_QUERY_SENTINEL"]
    assert len(model_inputs) == 2
    reinjected = [message for message in model_inputs[1] if isinstance(message, ToolMessage)]
    assert len(reinjected) == 1
    assert reinjected[0].tool_call_id == "http_tool_call_01"

    response = client.get(
        f"/api/agent/runs/{run_id}/events?after_seq=0&limit=200",
        headers={"X-Test-Owner": "alice"},
    )
    assert response.status_code == 200
    events = response.get_json()["data"]["events"]
    tool_events = [event for event in events if event["event_type"].startswith("run.tool_call_")]
    assert [event["event_type"] for event in tool_events] == [
        "run.tool_call_started", "run.tool_call_settled",
    ]
    assert tool_events[0]["payload"]["tool_call_id"] == tool_events[1]["payload"]["tool_call_id"]
    assert tool_events[0]["payload"]["assistant_message_id"] == tool_events[1]["payload"]["assistant_message_id"]
    assert tool_events[1]["payload"]["status"] == ("failed" if fail else "succeeded")
    serialized = response.get_data(as_text=True)
    assert "PRIVATE_TOOL_QUERY_SENTINEL" not in serialized
    assert "PRIVATE_TOOL_RESULT_SENTINEL" not in serialized
    assert "PRIVATE_TOOL_EXCEPTION_SENTINEL" not in serialized
    assert "Synthetic read completed." not in serialized
    assert result["incomplete"] is fail


def test_submit_rejects_unknown_or_other_owners_authoritative_session(run_api):
    client, _repository = run_api
    unknown = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "run-http-key-10"},
        json={"session_id": "missing-session", "message": "hello"},
    )
    assert unknown.status_code == 404
    assert unknown.get_json()["code"] == "AGENT_RUN_SESSION_NOT_FOUND"
    other_owner = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "run-http-key-11"},
        json={"session_id": "session-mallory", "message": "hello"},
    )
    assert other_owner.status_code == 403
    assert other_owner.get_json()["code"] == "PERMISSION_ERROR"


def test_repeat_submit_explicitly_replays_migration_held_run_and_returns_latest_cursor(
    run_api, monkeypatch,
):
    client, repository = run_api
    created = _submit(client)
    run_id = created.get_json()["data"]["run"]["run_id"]
    with repository.mysql.engine.begin() as conn:
        conn.execute(text("""
            UPDATE agent_run_dispatch SET dispatch_state='not_dispatched'
            WHERE run_id=:run_id
        """), {"run_id": run_id})

    called = {}

    def publish(run_id, *, repository, dispatch_store, allow_not_dispatched=False):
        called["allow_not_dispatched"] = allow_not_dispatched
        reservation = dispatch_store.reserve_publish(
            run_id, allow_not_dispatched=allow_not_dispatched,
        )
        assert reservation["reserved"] is True
        return dispatch_store.finish_publish(
            run_id, publish_token=reservation["publish_token"], published=True,
        )

    monkeypatch.setattr(agent_run_jobs, "dispatch_agent_run", publish)
    replay = _submit(client)
    assert replay.status_code == 200
    data = replay.get_json()["data"]
    assert called["allow_not_dispatched"] is True
    assert data["dispatch_status"] == "published"
    assert data["run"]["last_event_seq"] == 3
    assert repository.read_events(run_id, student_id="alice")["cursor"] == 3


@pytest.mark.parametrize("query", ["after_seq=no", "after_seq=-1", "limit=0", "limit=201"])
def test_events_endpoint_rejects_invalid_cursor_and_page_size(run_api, query):
    client, _repository = run_api
    run_id = _submit(client).get_json()["data"]["run"]["run_id"]
    response = client.get(
        f"/api/agent/runs/{run_id}/events?{query}",
        headers={"X-Test-Owner": "alice"},
    )
    assert response.status_code == 400


def test_storage_failure_returns_503_without_claiming_execution(run_api, monkeypatch):
    client, _repository = run_api

    def unavailable():
        raise RuntimeError("synthetic DB offline")

    monkeypatch.setattr(agent_runs, "get_agent_run_repository", unavailable)
    response = _submit(client)
    assert response.status_code == 503
    assert response.get_json()["code"] == "AGENT_RUN_STORAGE_UNAVAILABLE"
    assert "没有启动执行" in response.get_json()["error"]
