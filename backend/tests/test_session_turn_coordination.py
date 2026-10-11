"""Cross-worker-equivalent tests for Supervisor session turn admission."""

from __future__ import annotations

import threading
import time
import json
from types import SimpleNamespace

import pytest

from app.services.agent_runtime.session_coordinator import (
    SessionCoordinationUnavailable,
    SessionCoordinationLost,
    SessionCoordinator,
)
from app.services.session_manager import SessionManager


class _SharedRedisLock:
    def __init__(self, mutex: threading.Lock):
        self._mutex = mutex
        self._owned = False
        self.extends = 0
        self.server_owned = True

    def acquire(self, *, blocking=True):
        self._owned = self._mutex.acquire(blocking=blocking)
        return self._owned

    def extend(self, _seconds, *, replace_ttl=True):
        assert self._owned
        assert replace_ttl is True
        self.extends += 1

    def owned(self):
        return self._owned and self.server_owned

    def release(self):
        if self._owned:
            self._owned = False
            self._mutex.release()


class _SharedSessionRedis:
    """Minimal shared Redis shape for proving two SessionManager caches diverge."""

    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.strict_writes_fail = False
        self.strict_fail_on_write = None
        self.strict_write_count = 0

    def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    def set_strict(self, key, value, **_kwargs):
        self.strict_write_count += 1
        if self.strict_writes_fail or self.strict_write_count == self.strict_fail_on_write:
            raise OSError("redis write unavailable")
        self.set(key, value)

    def get(self, key, default=None):
        value = self.values.get(key)
        return json.loads(value) if value is not None else default

    def get_strict(self, key):
        return self.get(key)

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value
        return 1


def test_two_independent_coordinators_serialize_one_shared_session():
    """Two coordinator objects model two Flask workers with separate mutex maps."""
    shared_mutex = threading.Lock()
    made = []

    def factory(_session_id):
        lock = _SharedRedisLock(shared_mutex)
        made.append(lock)
        return lock

    left = SessionCoordinator(redis_lock_factory=factory, redis_required=True, lease_seconds=5)
    right = SessionCoordinator(redis_lock_factory=factory, redis_required=True, lease_seconds=5)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    ordered = []

    def first_worker():
        with left.lock("session_shared") as lease:
            ordered.append("first:start")
            first_entered.set()
            assert release_first.wait(3)
            lease.assert_held()
            ordered.append("first:settled")

    def second_worker():
        assert first_entered.wait(3)
        second_attempted.set()
        with right.lock("session_shared") as lease:
            lease.assert_held()
            ordered.append("second:start")
            ordered.append("second:settled")

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    assert first_entered.wait(3)
    second.start()
    assert second_attempted.wait(3)
    time.sleep(0.08)
    # The second worker has reached admission, but shared Redis ownership keeps
    # it outside the authoritative session settlement lane.
    assert ordered == ["first:start"]
    release_first.set()
    first.join(3)
    second.join(3)
    assert not first.is_alive()
    assert not second.is_alive()
    assert ordered == ["first:start", "first:settled", "second:start", "second:settled"]
    assert len(made) == 2


def test_required_redis_coordination_fails_closed():
    def unavailable(_session_id):
        raise OSError("redis unavailable")

    coordinator = SessionCoordinator(redis_lock_factory=unavailable, redis_required=True)
    with pytest.raises(SessionCoordinationUnavailable):
        with coordinator.lock("session_required"):
            pytest.fail("required coordination must not enter the turn")


def test_preferred_redis_coordination_is_explicitly_local_when_unavailable(caplog):
    def unavailable(_session_id):
        raise OSError("redis unavailable")

    coordinator = SessionCoordinator(redis_lock_factory=unavailable, redis_required=False)
    with coordinator.lock("session_dev") as lease:
        lease.assert_held()
    assert "local-only turn lock" in caplog.text


def test_second_worker_refreshes_authoritative_session_after_waiting_for_lease(monkeypatch):
    """A cross-worker lock alone is insufficient if the second cache is stale."""
    store = _SharedSessionRedis()
    first = SessionManager(redis_client=store)
    second = SessionManager(redis_client=store)
    # Keep this focused on Redis session truth, not MySQL replication.
    monkeypatch.setattr(first, "_save_message_to_mysql", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(first, "_update_mysql_title", lambda *_args, **_kwargs: None)

    state = first.create_session(student_id="student_1", session_id="session_shared")
    assert second.get_session(state.session_id).messages == []  # second worker now has a stale cache
    first.add_message(state.session_id, "user", "first turn")

    refreshed = second.refresh_session(state.session_id)
    assert refreshed is not None
    assert [item["content"] for item in refreshed.messages] == ["first turn"]


def test_required_refresh_rejects_stale_cache_on_missing_or_unavailable_redis(monkeypatch):
    store = _SharedSessionRedis()
    manager = SessionManager(redis_client=store)
    monkeypatch.setattr(manager, "_save_session_to_mysql", lambda *_a, **_k: None)
    state = manager.create_session(student_id="student_1", session_id="session_required")
    key = manager._get_redis_key(state.session_id)
    store.values.pop(key)
    assert manager.refresh_session(state.session_id, require_authoritative=True) is None
    assert state.session_id not in manager._cache

    manager._cache[state.session_id] = state
    def unavailable(_key):
        raise OSError("redis down")
    monkeypatch.setattr(store, "get_strict", unavailable)
    with pytest.raises(OSError, match="redis down"):
        manager.refresh_session(state.session_id, require_authoritative=True)


def test_settlement_checks_server_side_lease_ownership_even_before_watchdog_tick():
    lock = _SharedRedisLock(threading.Lock())
    coordinator = SessionCoordinator(redis_lock_factory=lambda _sid: lock, redis_required=True)
    with coordinator.lock("session_lost") as lease:
        lease.assert_held()
        lock.server_owned = False
        with pytest.raises(SessionCoordinationLost):
            lease.assert_held()


def test_coordination_failure_is_safe_for_json_and_keeps_sse_first_comment(monkeypatch):
    """Fail-closed coordination must not regress the previously-fixed SSE connect."""
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    manager = SimpleNamespace()
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(
        agent_api,
        "_resolve_session",
        lambda *_args: ("session_coord", SimpleNamespace(student_id="demo_student")),
    )
    monkeypatch.setattr(
        agent_api,
        "_run_recorded_supervisor_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SessionCoordinationUnavailable("redis down")),
    )

    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "hello"})
    assert sync.status_code == 503
    assert sync.get_json()["code"] == "SESSION_COORDINATION_UNAVAILABLE"

    stream = client.post("/api/agent/chat/stream", json={"message": "hello"})
    assert stream.status_code == 200
    chunks = stream.get_data(as_text=True).splitlines()
    assert chunks[0] == ": connected"
    assert any('"SESSION_COORDINATION_UNAVAILABLE"' in chunk for chunk in chunks)


def test_lost_lease_before_settlement_is_503_in_json_and_error_in_sse(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config
    from app.services.agent_runtime.egress_pipeline import project_turn

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    lock = _SharedRedisLock(threading.Lock())
    coordinator = SessionCoordinator(redis_lock_factory=lambda _sid: lock, redis_required=True)
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: coordinator)
    state = SimpleNamespace(student_id="demo_student")
    messages = []
    manager = SimpleNamespace(
        refresh_session=lambda _sid, **_kw: state,
        add_message=lambda **kw: messages.append(kw),
    )
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("session_lost", state))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kw: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )

    def run_supervisor(_message, session_id, *_args, **_kwargs):
        lock.server_owned = False
        raw = {"reply": "已完成", "session_id": session_id}
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)
    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "hello"})
    assert sync.status_code == 503
    assert sync.get_json()["code"] == "SESSION_COORDINATION_UNAVAILABLE"
    assert not any(item["role"] == "assistant" for item in messages)

    lock.server_owned = True
    stream = client.post("/api/agent/chat/stream", json={"message": "hello"})
    assert stream.status_code == 200
    chunks = stream.get_data(as_text=True).splitlines()
    assert chunks[0] == ": connected"
    assert any('"SESSION_COORDINATION_UNAVAILABLE"' in chunk for chunk in chunks)
    assert not any(item["role"] == "assistant" for item in messages)


def test_required_session_write_failure_does_not_finish_json_or_sse_turn(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    store = _SharedSessionRedis()
    manager = SessionManager(redis_client=store)
    monkeypatch.setattr(manager, "_save_session_to_mysql", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_save_message_to_mysql", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_update_mysql_title", lambda *_a, **_k: None)
    state = manager.create_session(student_id="demo_student", session_id="session_write_fail")
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_a: (state.session_id, state))
    coordinator = SessionCoordinator(
        redis_lock_factory=lambda _sid: _SharedRedisLock(threading.Lock()),
        redis_required=True,
    )
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kw: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )
    model_calls = []
    monkeypatch.setattr(agent_api, "_run_supervisor", lambda *_a, **_k: model_calls.append(1))
    store.strict_writes_fail = True
    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "hello"})
    assert sync.status_code == 503
    assert sync.get_json()["code"] == "SESSION_COORDINATION_UNAVAILABLE"
    stream = client.post("/api/agent/chat/stream", json={"message": "hello"})
    assert stream.status_code == 200
    chunks = stream.get_data(as_text=True).splitlines()
    assert chunks[0] == ": connected"
    assert any('"SESSION_COORDINATION_UNAVAILABLE"' in chunk for chunk in chunks)
    assert model_calls == []
    assert manager.get_session(state.session_id).messages == []
    assert manager.refresh_session(state.session_id, require_authoritative=True).messages == []
    assert manager.get_session(state.session_id).messages == []


def test_required_assistant_write_failure_keeps_only_recorded_user_turn(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config
    import app.services.agent_runtime as agent_runtime

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    store = _SharedSessionRedis()
    manager = SessionManager(redis_client=store)
    monkeypatch.setattr(manager, "_save_session_to_mysql", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_save_message_to_mysql", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_update_mysql_title", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "get_history_for_agent", lambda *_a, **_k: [{"role": "user", "content": "hello"}])
    monkeypatch.setattr(manager, "get_conversation_summary", lambda *_a, **_k: "")
    state = manager.create_session(student_id="demo_student", session_id="session_assistant_fail")
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_a: (state.session_id, state))
    coordinator = SessionCoordinator(
        redis_lock_factory=lambda _sid: _SharedRedisLock(threading.Lock()), redis_required=True,
    )
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kw: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )
    monkeypatch.setattr(agent_runtime, "get_supervisor_service", lambda: SimpleNamespace(
        chat=lambda **_kw: {
            "reply": "已完成", "session_id": state.session_id,
            "interaction_mode": "agent", "blackboard": {"step": "finished"},
        },
    ))
    store.strict_fail_on_write = 2

    response = create_app().test_client().post("/api/agent/chat", json={"message": "hello"})
    assert response.status_code == 503
    assert response.get_json()["code"] == "SESSION_COORDINATION_UNAVAILABLE"
    assert [item["role"] for item in manager.get_session(state.session_id).messages] == ["user"]
    authoritative = manager.refresh_session(state.session_id, require_authoritative=True)
    assert [item["role"] for item in authoritative.messages] == ["user"]
    assert [item["role"] for item in manager.get_session(state.session_id).messages] == ["user"]
    assert authoritative.context == {"conversation_title": "hello"}

    # Retry is a new turn, not exactly-once replay of the failed assistant.
    store.strict_fail_on_write = None
    retry = create_app().test_client().post("/api/agent/chat", json={"message": "hello"})
    assert retry.status_code == 200
    settled = manager.refresh_session(state.session_id, require_authoritative=True)
    assert [item["role"] for item in settled.messages] == ["user", "user", "assistant"]
    assert settled.context["blackboard"] == {"step": "finished"}
