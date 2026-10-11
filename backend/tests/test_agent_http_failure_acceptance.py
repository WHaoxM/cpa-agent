"""Outside-in acceptance for tool failure and Redis cross-worker semantics.

These tests use actual listening sockets, two independent Flask OS processes,
the production Agent HTTP routes and a real Redis server.  See the companion
fixture server for the exact deterministic model/tool substitution boundary.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

import pytest
import redis


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "backend" / "tests" / "fixtures" / "http_failure_acceptance_server.py"
REDIS_URL = "redis://127.0.0.1:16379/15"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base_url: str, method: str, path: str,
             payload: dict[str, Any] | None = None,
             *, timeout: float = 10.0) -> tuple[int, dict[str, Any]]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=raw,
        method=method,
        headers={"Content-Type": "application/json"} if raw is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return int(exc.code), json.loads(body)


@dataclass
class _Worker:
    label: str
    base_url: str
    process: subprocess.Popen
    log_path: Path
    log_handle: Any


def _start_worker(tmp_path: Path, *, label: str) -> _Worker:
    port = _free_port()
    log_path = tmp_path / f"http-acceptance-{label}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(SERVER),
            "--port", str(port),
            "--redis-url", REDIS_URL,
            "--worker-label", label,
        ],
        cwd=str(ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    worker = _Worker(label, f"http://127.0.0.1:{port}", process, log_path, log_handle)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_handle.flush()
            pytest.fail(f"worker {label} exited early:\n{log_path.read_text(encoding='utf-8')}")
        try:
            status, body = _request(worker.base_url, "GET", "/health", timeout=0.5)
            if status == 200 and body.get("status") == "ok":
                return worker
        except (URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    log_handle.close()
    pytest.fail(f"worker {label} did not become ready:\n{log_path.read_text(encoding='utf-8')}")


@pytest.fixture(scope="module")
def live_redis() -> redis.Redis:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
    try:
        assert client.ping() is True
    except Exception as exc:
        pytest.skip(f"real Redis acceptance dependency unavailable: {exc}")
    return client


@pytest.fixture
def two_workers(tmp_path: Path, live_redis: redis.Redis):
    workers = [
        _start_worker(tmp_path, label="worker_a"),
        _start_worker(tmp_path, label="worker_b"),
    ]
    try:
        yield workers
    finally:
        for worker in workers:
            if worker.process.poll() is None:
                worker.process.terminate()
        for worker in workers:
            try:
                worker.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait(timeout=3)
            worker.log_handle.close()


def _wait_for_list(client: redis.Redis, key: str, length: int,
                   *, timeout: float = 5.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = client.lrange(key, 0, -1)
        if len(rows) >= length:
            return [json.loads(row) for row in rows]
        time.sleep(0.02)
    return [json.loads(row) for row in client.lrange(key, 0, -1)]


def _cleanup_session(client: redis.Redis, session_id: str) -> None:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    tx = client.pipeline(transaction=True)
    tx.delete(f"cpa:session:{session_id}")
    tx.hdel("cpa:sessions", session_id)
    tx.delete(f"cpa:agent:turn-lock:{digest}")
    tx.execute()


def test_real_http_mutating_tool_failure_reports_final_state_and_single_side_effect(
    two_workers: list[_Worker], live_redis: redis.Redis,
):
    worker = two_workers[0]
    marker = f"tool_{uuid.uuid4().hex}"
    session_id = f"session_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    live_redis.delete(effect_key)
    try:
        status, created = _request(
            worker.base_url,
            "POST",
            "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200
        assert created["success"] is True
        assert created["data"]["session_id"] == session_id

        status, response = _request(
            worker.base_url,
            "POST",
            "/api/agent/chat",
            {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"},
        )
        # HTTP transport succeeded, while the typed turn truth says execution
        # failed.  Do not infer business success from HTTP 200/success=true.
        assert status == 200
        assert response["success"] is True
        turn = response["data"]
        assert turn["outcome"] == {
            "status": "failed",
            "completion_scope": "turn",
            "reason": "execution_failed",
            "next_action": "retry_new_turn",
            "resume_state": "not_available",
        }
        assert len(turn["tool_calls"]) == 1
        call = turn["tool_calls"][0]
        assert call["tool_name"] == "pipeline_run"
        assert call["status"] == "error"
        assert call["error_code"] == "tool_timeout"
        assert call["retryable"] is False
        assert call["attempts"] == 1
        assert turn["permission_denials"] == []
        assert "private substitute backend detail" not in json.dumps(response)

        # Redis is the substitute tool's authoritative target.  The mutation
        # happened once despite the failed turn, and the real gate rewrote both
        # hostile owner values to the trusted HTTP session scope.
        effect = live_redis.hgetall(effect_key)
        assert effect == {
            "count": "1",
            "student_id": "acceptance_student",
            "session_id": session_id,
            "pipeline": "acceptance_mutation",
            "nested_student_id": "acceptance_student",
            "worker": "worker_a",
        }

        history_status, history = _request(
            worker.base_url,
            "GET",
            f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        rows = history["data"]["messages"]
        assert [row["role"] for row in rows] == ["user", "assistant"]
        assert rows[-1]["outcome"]["status"] == "failed"
        assert rows[-1]["outcome"]["reason"] == "execution_failed"
    finally:
        live_redis.delete(effect_key)
        _cleanup_session(live_redis, session_id)


def test_two_real_workers_serialize_one_session_and_fail_closed_when_redis_is_lost(
    two_workers: list[_Worker], live_redis: redis.Redis,
):
    first_worker, second_worker = two_workers
    marker = f"serial_{uuid.uuid4().hex}"
    session_id = f"session_{uuid.uuid4().hex}"
    event_key = f"cpa:acceptance:serial:{marker}"
    live_redis.delete(event_key)
    try:
        status, body = _request(
            first_worker.base_url,
            "POST",
            "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True

        # Populate each process-local cache.  The subsequent production turn
        # must still refresh from authoritative Redis after lease admission.
        for worker in two_workers:
            warm_status, warm = _request(
                worker.base_url,
                "GET",
                f"/api/agent/session/{session_id}/history",
            )
            assert warm_status == 200
            assert warm["data"]["message_count"] == 0

        def chat(worker: _Worker, turn: str, delay_ms: int):
            return _request(
                worker.base_url,
                "POST",
                "/api/agent/chat",
                {"session_id": session_id,
                 "message": f"SERIAL {marker} {turn} {delay_ms}"},
                timeout=12,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(chat, first_worker, "first", 450)
            starts = _wait_for_list(live_redis, event_key, 1)
            assert [(row["phase"], row["turn"]) for row in starts] == [("start", "first")]
            second = pool.submit(chat, second_worker, "second", 0)
            time.sleep(0.12)
            # If workers only used process-local locks, second:start would be
            # visible here before the first turn settled.
            still_one = [json.loads(row) for row in live_redis.lrange(event_key, 0, -1)]
            assert [(row["phase"], row["turn"]) for row in still_one] == [("start", "first")]
            first_status, first_body = first.result(timeout=10)
            second_status, second_body = second.result(timeout=10)

        assert first_status == second_status == 200
        assert first_body["data"]["outcome"]["status"] == "completed"
        assert second_body["data"]["outcome"]["status"] == "completed"
        events = _wait_for_list(live_redis, event_key, 4)
        assert [(row["phase"], row["turn"]) for row in events] == [
            ("start", "first"),
            ("end", "first"),
            ("start", "second"),
            ("end", "second"),
        ]
        assert events[0]["pid"] != events[2]["pid"]
        assert events[0]["worker"] == "worker_a"
        assert events[2]["worker"] == "worker_b"
        assert events[0]["prior_roles"] == []
        assert events[2]["prior_roles"] == ["user", "assistant"]

        persisted = live_redis.get(f"cpa:session:{session_id}")
        authoritative_before = json.loads(persisted)
        assert [row["role"] for row in authoritative_before["messages"]] == [
            "user", "assistant", "user", "assistant",
        ]

        # Make only worker A's Redis client point to a real refused TCP socket.
        # Its session remains warm, ensuring request resolution reaches the
        # redis_required coordinator rather than being mistaken for a 404.
        control_status, control = _request(
            first_worker.base_url,
            "POST",
            "/__acceptance__/redis-unreachable",
            {},
        )
        assert control_status == 200 and control["ok"] is True
        failed_status, failed = chat(first_worker, "redis_lost", 0)
        assert failed_status == 503
        assert failed == {
            "success": False,
            "code": "SESSION_COORDINATION_UNAVAILABLE",
            "error": "当前会话暂无法安全串行处理，请稍后重试。",
        }

        # Fail-closed happens before user STM, model/tool work or a side effect.
        authoritative_after = json.loads(live_redis.get(f"cpa:session:{session_id}"))
        assert authoritative_after == authoritative_before
        events_after_failure = [
            json.loads(row) for row in live_redis.lrange(event_key, 0, -1)
        ]
        assert [(row["phase"], row["turn"]) for row in events_after_failure] == [
            ("start", "first"),
            ("end", "first"),
            ("start", "second"),
            ("end", "second"),
        ]
    finally:
        live_redis.delete(event_key)
        _cleanup_session(live_redis, session_id)
