"""Outside-in acceptance for explicit Agent turn idempotency.

The HTTP server and mutating tool substitute come from
``http_failure_acceptance_server.py``.  Requests cross a real TCP socket and
the mutation is read back from Redis, while the idempotency ledger itself is
the production MySQL implementation.  The substitute exists only to make a
committed-before-timeout side effect deterministic.
"""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

import pytest
import redis


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "backend" / "tests" / "fixtures" / "http_failure_acceptance_server.py"
IDEMPOTENCY_SERVER = (
    ROOT / "backend" / "tests" / "fixtures" /
    "http_idempotency_acceptance_server.py"
)
REDIS_URL = "redis://127.0.0.1:16379/15"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
    timeout: float = 12.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if raw is not None else {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(base_url + path, data=raw, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body), dict(response.headers.items())
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return int(exc.code), json.loads(body), dict(exc.headers.items())


def _sse_request(
    base_url: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str,
) -> tuple[int, str, dict[str, str]]:
    request = Request(
        base_url + "/api/agent/chat/stream",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            return (
                int(response.status),
                response.read().decode("utf-8"),
                dict(response.headers.items()),
            )
    except HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8"), dict(exc.headers.items())


def _start_worker(
    tmp_path: Path,
    *,
    worker_label: str,
    server: Path = SERVER,
    student_id: str | None = None,
    mysql_port: int | None = None,
    lock_mode: str | None = None,
) -> tuple[str, subprocess.Popen, Any, Path]:
    port = _free_port()
    log_path = tmp_path / f"http-idempotency-{worker_label}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(server),
            "--port", str(port),
            "--redis-url", REDIS_URL,
            "--worker-label", worker_label,
        ]
        + (["--student-id", student_id] if student_id else [])
        + (["--mysql-port", str(mysql_port)] if mysql_port is not None else [])
        + (["--lock-mode", lock_mode] if lock_mode else []),
        cwd=str(ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_handle.flush()
            pytest.fail(log_path.read_text(encoding="utf-8"))
        try:
            status, body, _headers = _request(base_url, "GET", "/health", timeout=0.5)
            if status == 200 and body.get("status") == "ok":
                return base_url, process, log_handle, log_path
        except (URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    log_handle.close()
    pytest.fail(f"worker did not become ready:\n{log_path.read_text(encoding='utf-8')}")


def _cleanup_session(client: redis.Redis, session_id: str) -> None:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    tx = client.pipeline(transaction=True)
    tx.delete(f"cpa:session:{session_id}")
    tx.hdel("cpa:sessions", session_id)
    tx.delete(f"cpa:agent:turn-lock:{digest}")
    tx.execute()


def _stop_worker(worker: tuple[str, subprocess.Popen, Any, Path]) -> None:
    _base_url, process, log_handle, log_path = worker
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    log_handle.close()
    if process.returncode not in (0, -15, 1):
        pytest.fail(log_path.read_text(encoding="utf-8"))


def _real_stores_or_skip():
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1,
    )
    try:
        assert redis_client.ping() is True
    except Exception as exc:
        pytest.skip(f"real Redis acceptance dependency unavailable: {exc}")

    from app.services.agent_runtime.turn_idempotency import (
        install_turn_idempotency_schema,
    )
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("real MySQL idempotency dependency unavailable")
    install_turn_idempotency_schema(mysql)
    return redis_client, mysql


def _delete_ledger(mysql, *, student_id: str, session_id: str) -> None:
    mysql.execute_write(
        "DELETE FROM agent_turn_idempotency "
        "WHERE student_id=:student_id AND session_id=:session_id",
        {"student_id": student_id, "session_id": session_id},
    )


def test_completed_mutating_turn_replays_across_json_and_sse_without_second_effect(
    tmp_path: Path,
):
    redis_client, mysql = _real_stores_or_skip()

    from app.services.agent_runtime.turn_idempotency import (
        scoped_key_sha256,
    )

    worker_a = _start_worker(tmp_path, worker_label="idempotency_a")
    worker_b = _start_worker(tmp_path, worker_label="idempotency_b")
    base_url_a = worker_a[0]
    base_url_b = worker_b[0]
    marker = f"idem_{uuid.uuid4().hex}"
    session_id = f"session_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    redis_client.delete(effect_key)
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    try:
        created_status, created, _ = _request(
            base_url_a,
            "POST",
            "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert created_status == 200 and created["success"] is True

        first_status, first, first_headers = _request(
            base_url_a,
            "POST",
            "/api/agent/chat",
            payload,
            idempotency_key=key,
        )
        assert first_status == 200
        assert first["data"]["outcome"]["status"] == "failed"
        assert first_headers.get("Idempotency-Replayed") is None
        assert redis_client.hget(effect_key, "count") == "1"

        replay_status, replay, replay_headers = _request(
            base_url_b,
            "POST",
            "/api/agent/chat",
            payload,
            idempotency_key=key,
        )
        assert replay_status == 200
        # Authoritative failure before this implementation: the duplicate
        # request executes the mutating tool again and increments to ``2``.
        assert redis_client.hget(effect_key, "count") == "1"
        assert replay == first
        assert replay_headers.get("Idempotency-Replayed") == "true"

        sse_status, sse_body, sse_headers = _sse_request(
            base_url_b,
            payload,
            idempotency_key=key,
        )
        assert sse_status == 200
        assert sse_headers.get("Idempotency-Replayed") == "true"
        assert "event: turn_done" in sse_body
        assert "data: [DONE]" in sse_body
        assert redis_client.hget(effect_key, "count") == "1"

        conflict_status, conflict, _ = _request(
            base_url_b,
            "POST",
            "/api/agent/chat",
            {"session_id": session_id, "message": "different payload"},
            idempotency_key=key,
        )
        assert conflict_status == 409
        assert conflict["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert redis_client.hget(effect_key, "count") == "1"

        history_status, history, _ = _request(
            base_url_b,
            "GET",
            f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert [row["role"] for row in history["data"]["messages"]] == [
            "user",
            "assistant",
        ]

        ledger_row = mysql.fetch_one(
            "SELECT state, request_sha256, response_status, response_json "
            "FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id "
            "AND key_sha256=:key_sha256",
            {
                "student_id": "acceptance_student",
                "session_id": session_id,
                "key_sha256": scoped_key_sha256(
                    "acceptance_student", session_id, key,
                ),
            },
        )
        assert ledger_row is not None
        assert ledger_row["state"] == "completed"
        assert ledger_row["response_status"] == 200
        assert json.loads(ledger_row["response_json"]) == first
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker_a)
        _stop_worker(worker_b)


def test_concurrent_same_key_is_in_progress_then_replays_across_workers(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker_a = _start_worker(tmp_path, worker_label="concurrent_a")
    worker_b = _start_worker(tmp_path, worker_label="concurrent_b")
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"serial_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    event_key = f"cpa:acceptance:serial:{marker}"
    release_key = f"cpa:acceptance:serial:release:{marker}"
    payload = {"session_id": session_id, "message": f"SERIAL {marker} one hold"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(event_key, release_key)
    try:
        status, body, _ = _request(
            worker_a[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True

        first_result: list[tuple[int, dict[str, Any], dict[str, str]]] = []

        def run_first() -> None:
            first_result.append(_request(
                worker_a[0], "POST", "/api/agent/chat", payload,
                idempotency_key=key,
            ))

        thread = threading.Thread(target=run_first, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and redis_client.llen(event_key) < 1:
            time.sleep(0.03)
        assert redis_client.llen(event_key) >= 1

        duplicate_status, duplicate, _ = _request(
            worker_b[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert duplicate_status == 409
        assert duplicate["code"] == "IDEMPOTENCY_IN_PROGRESS"

        redis_client.set(release_key, "1", ex=30)
        thread.join(timeout=8)
        assert not thread.is_alive()
        assert first_result and first_result[0][0] == 200
        replay_status, replay, replay_headers = _request(
            worker_b[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert replay_status == 200
        assert replay == first_result[0][1]
        assert replay_headers.get("Idempotency-Replayed") == "true"
    finally:
        redis_client.delete(event_key, release_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker_a)
        _stop_worker(worker_b)


def test_crash_after_effect_becomes_unknown_and_never_reexecutes(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    crashing = _start_worker(
        tmp_path,
        worker_label="crash_after_effect",
        server=IDEMPOTENCY_SERVER,
    )
    recovery = _start_worker(
        tmp_path,
        worker_label="crash_recovery",
        server=IDEMPOTENCY_SERVER,
    )
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"crash_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"CRASH_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            crashing[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True

        with pytest.raises((URLError, http.client.RemoteDisconnected, OSError)):
            _request(
                crashing[0], "POST", "/api/agent/chat", payload,
                idempotency_key=key,
            )
        crashing[1].wait(timeout=5)
        assert crashing[1].returncode == 23
        assert redis_client.hget(effect_key, "count") == "1"

        running = mysql.fetch_one(
            "SELECT state FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        assert running == {"state": "running"}
        # Simulate the documented stale-window elapsing without a multi-hour
        # sleep.  The next admission must tombstone, never take ownership.
        mysql.execute_write(
            "UPDATE agent_turn_idempotency "
            "SET stale_after='2000-01-01 00:00:00.000000' "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )

        retry_status, retry, _ = _request(
            recovery[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert retry_status == 409
        assert retry["code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"
        assert redis_client.hget(effect_key, "count") == "1"
        unknown = mysql.fetch_one(
            "SELECT state FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        assert unknown == {"state": "unknown"}
        history_status, history, _ = _request(
            recovery[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert [item["role"] for item in history["data"]["messages"]] == ["user"]
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        if crashing[1].poll() is None:
            _stop_worker(crashing)
        else:
            crashing[2].close()
        _stop_worker(recovery)


def test_keyed_request_fails_closed_when_mysql_is_unavailable(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker = _start_worker(
        tmp_path,
        worker_label="mysql_unavailable",
        server=IDEMPOTENCY_SERVER,
        mysql_port=1,
    )
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"no_db_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    key = f"turn_{uuid.uuid4().hex}"
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert status == 503
        assert body["code"] == "IDEMPOTENCY_STORAGE_UNAVAILABLE"
        assert redis_client.exists(effect_key) == 0

        history_status, history, _ = _request(
            worker[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert history["data"]["messages"] == []
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker)


def test_assistant_settlement_failure_marks_unknown_without_second_effect(
    tmp_path: Path,
):
    redis_client, mysql = _real_stores_or_skip()
    failing = _start_worker(
        tmp_path,
        worker_label="settlement_failure",
        server=IDEMPOTENCY_SERVER,
    )
    recovery = _start_worker(
        tmp_path,
        worker_label="settlement_recovery",
        server=IDEMPOTENCY_SERVER,
    )
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"settle_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {
        "session_id": session_id,
        "message": f"SETTLEMENT_REDIS_DOWN {marker}",
    }
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            failing[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        failed_status, failed, _ = _request(
            failing[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert failed_status == 503
        assert failed["code"] == "SESSION_COORDINATION_UNAVAILABLE"
        assert redis_client.hget(effect_key, "count") == "1"

        row = mysql.fetch_one(
            "SELECT state FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        assert row == {"state": "unknown"}

        retry_status, retry, _ = _request(
            recovery[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert retry_status == 409
        assert retry["code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"
        assert redis_client.hget(effect_key, "count") == "1"

        history_status, history, _ = _request(
            recovery[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert [item["role"] for item in history["data"]["messages"]] == ["user"]
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(failing)
        _stop_worker(recovery)


def test_keyed_turn_forces_authoritative_redis_in_preferred_mode(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker = _start_worker(
        tmp_path,
        worker_label="preferred_fail_closed",
        server=IDEMPOTENCY_SERVER,
        lock_mode="redis_preferred",
    )
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"preferred_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        control_status, control, _ = _request(
            worker[0], "POST", "/__acceptance__/redis-unreachable",
        )
        assert control_status == 200 and control["ok"] is True

        failed_status, failed, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert failed_status == 503
        assert failed["code"] == "SESSION_COORDINATION_UNAVAILABLE"
        assert redis_client.exists(effect_key) == 0
        row = mysql.fetch_one(
            "SELECT state FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        # Authoritative session preflight fails before MySQL admission, so no
        # durable claim or model/tool work is created.
        assert row is None
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker)


def test_other_owner_cannot_replay_a_completed_key(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    owner = _start_worker(
        tmp_path,
        worker_label="owner_a",
        server=IDEMPOTENCY_SERVER,
        student_id="acceptance_student",
    )
    other = _start_worker(
        tmp_path,
        worker_label="owner_b",
        server=IDEMPOTENCY_SERVER,
        student_id="other_student",
    )
    session_id = f"session_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    payload = {"session_id": session_id, "message": "owner scoped response"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    _delete_ledger(mysql, student_id="other_student", session_id=session_id)
    try:
        status, body, _ = _request(
            owner[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        first_status, first, _ = _request(
            owner[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert first_status == 200

        other_status, other_body, _ = _request(
            other[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert other_status == 403
        assert other_body["success"] is False

        replay_status, replay, replay_headers = _request(
            owner[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert replay_status == 200
        assert replay == first
        assert replay_headers.get("Idempotency-Replayed") == "true"
    finally:
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _delete_ledger(mysql, student_id="other_student", session_id=session_id)
        _stop_worker(owner)
        _stop_worker(other)


def test_expired_response_becomes_permanent_tombstone_without_reexecution(
    tmp_path: Path,
):
    redis_client, mysql = _real_stores_or_skip()
    worker = _start_worker(tmp_path, worker_label="expired_tombstone")
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"expired_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        first_status, _first, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert first_status == 200
        assert redis_client.hget(effect_key, "count") == "1"

        mysql.execute_write(
            "UPDATE agent_turn_idempotency "
            "SET response_expires_at='2000-01-01 00:00:00.000000' "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        expired_status, expired, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert expired_status == 409
        assert expired["code"] == "IDEMPOTENCY_RESULT_EXPIRED"
        assert redis_client.hget(effect_key, "count") == "1"
        row = mysql.fetch_one(
            "SELECT state, response_status, response_json "
            "FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        assert row == {
            "state": "expired",
            "response_status": None,
            "response_json": None,
        }
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker)


def test_deleted_session_id_cannot_replay_old_incarnation(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker = _start_worker(tmp_path, worker_label="session_incarnation")
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"incarnation_{uuid.uuid4().hex}"
    old_key = f"turn_{uuid.uuid4().hex}"
    new_key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        first_status, first, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=old_key,
        )
        assert first_status == 200
        assert redis_client.hget(effect_key, "count") == "1"

        deleted_status, _deleted, _ = _request(
            worker[0], "DELETE", f"/api/agent/session/{session_id}",
        )
        assert deleted_status == 200
        # Ensure a distinct persisted creation instant on filesystems/hosts
        # with coarse wall-clock resolution.
        time.sleep(0.01)
        recreated_status, recreated, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert recreated_status == 200 and recreated["success"] is True

        stale_status, stale, stale_headers = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=old_key,
        )
        assert stale_status == 409
        assert stale["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert stale_headers.get("Idempotency-Replayed") is None
        assert redis_client.hget(effect_key, "count") == "1"
        history_status, history, _ = _request(
            worker[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert history["data"]["messages"] == []

        fresh_status, fresh, _ = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=new_key,
        )
        assert fresh_status == 200
        assert fresh["data"]["turn_id"] != first["data"]["turn_id"]
        assert redis_client.hget(effect_key, "count") == "2"
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker)


def test_deleted_session_cannot_replay_from_other_workers_stale_cache(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker_a = _start_worker(tmp_path, worker_label="delete_owner")
    worker_b = _start_worker(tmp_path, worker_label="stale_cache")
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"deleted_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker_a[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        first_status, _first, _ = _request(
            worker_a[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert first_status == 200
        warm_status, _warm, _ = _request(
            worker_b[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert warm_status == 200

        deleted_status, _deleted, _ = _request(
            worker_a[0], "DELETE", f"/api/agent/session/{session_id}",
        )
        assert deleted_status == 200
        assert redis_client.exists(f"cpa:session:{session_id}") == 0

        replay_status, replay, replay_headers = _request(
            worker_b[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert replay_status == 404
        assert replay["success"] is False
        assert replay_headers.get("Idempotency-Replayed") is None
        assert redis_client.hget(effect_key, "count") == "1"
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker_a)
        _stop_worker(worker_b)


def test_fresh_sse_keyed_turn_completes_then_json_replays(tmp_path: Path):
    redis_client, mysql = _real_stores_or_skip()
    worker = _start_worker(tmp_path, worker_label="fresh_sse")
    session_id = f"session_{uuid.uuid4().hex}"
    marker = f"fresh_sse_{uuid.uuid4().hex}"
    key = f"turn_{uuid.uuid4().hex}"
    effect_key = f"cpa:acceptance:effect:{marker}"
    payload = {"session_id": session_id, "message": f"SIDE_EFFECT {marker}"}
    _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
    redis_client.delete(effect_key)
    try:
        status, body, _ = _request(
            worker[0], "POST", "/api/agent/session/create",
            {"session_id": session_id},
        )
        assert status == 200 and body["success"] is True
        sse_status, sse_body, sse_headers = _sse_request(
            worker[0], payload, idempotency_key=key,
        )
        assert sse_status == 200
        assert sse_headers.get("Idempotency-Replayed") is None
        assert "event: turn_done" in sse_body
        assert "data: [DONE]" in sse_body
        assert redis_client.hget(effect_key, "count") == "1"

        replay_status, replay, replay_headers = _request(
            worker[0], "POST", "/api/agent/chat", payload,
            idempotency_key=key,
        )
        assert replay_status == 200
        assert replay_headers.get("Idempotency-Replayed") == "true"
        assert replay["data"]["outcome"]["status"] == "failed"
        assert redis_client.hget(effect_key, "count") == "1"
        history_status, history, _ = _request(
            worker[0], "GET", f"/api/agent/session/{session_id}/history",
        )
        assert history_status == 200
        assert [item["role"] for item in history["data"]["messages"]] == [
            "user", "assistant",
        ]
        row = mysql.fetch_one(
            "SELECT state, response_status FROM agent_turn_idempotency "
            "WHERE student_id=:student_id AND session_id=:session_id",
            {"student_id": "acceptance_student", "session_id": session_id},
        )
        assert row == {"state": "completed", "response_status": 200}
    finally:
        redis_client.delete(effect_key)
        _cleanup_session(redis_client, session_id)
        _delete_ledger(mysql, student_id="acceptance_student", session_id=session_id)
        _stop_worker(worker)
