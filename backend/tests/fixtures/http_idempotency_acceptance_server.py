"""Real-socket worker for Agent idempotency crash/isolation acceptance.

Production Flask routes, MySQL ledger, Redis session storage and Redis turn
lease stay in the request path.  Only the model is replaced with the same
deterministic supervisor used by the failure-acceptance suite.  ``CRASH_EFFECT``
adds one deliberate process-crash window after an externally visible mutation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Any


BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent
for path in (BACKEND, FIXTURES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from http_failure_acceptance_server import DeterministicSupervisor


_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_:-]{1,80}$")


def _token(value: str) -> str:
    if not _SAFE_TOKEN.fullmatch(value or ""):
        raise ValueError("invalid acceptance token")
    return value


class CrashSupervisor(DeterministicSupervisor):
    def __init__(self, redis_client: Any, *, worker_label: str,
                 break_redis=None):
        super().__init__(redis_client, worker_label=worker_label)
        self._break_redis = break_redis

    def chat(self, *, message: str, session_id: str, student_id: str,
             history: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        fields = str(message or "").split()
        if len(fields) == 2 and fields[0] == "CRASH_EFFECT":
            marker = _token(fields[1])
            effect_key = f"cpa:acceptance:effect:{marker}"
            tx = self._redis.pipeline(transaction=True)
            tx.hincrby(effect_key, "count", 1)
            tx.hset(effect_key, mapping={
                "student_id": student_id,
                "session_id": session_id,
                "worker": self._worker_label,
            })
            tx.execute()
            # Deliberately bypass Python finally/teardown to model a worker
            # disappearing after a tool-side mutation and before settlement.
            os._exit(23)
        if len(fields) == 2 and fields[0] == "SETTLEMENT_REDIS_DOWN":
            marker = _token(fields[1])
            effect_key = f"cpa:acceptance:effect:{marker}"
            tx = self._redis.pipeline(transaction=True)
            tx.hincrby(effect_key, "count", 1)
            tx.hset(effect_key, mapping={
                "student_id": student_id,
                "session_id": session_id,
                "worker": self._worker_label,
            })
            tx.execute()
            # The user STM and external effect are committed.  Make this
            # worker's production Redis client fail before lease assertion /
            # assistant settlement, without stopping the real Redis server.
            if self._break_redis is None:
                raise RuntimeError("acceptance Redis breaker missing")
            self._break_redis()
            return {
                "reply": "must not settle",
                "session_id": session_id,
                "interaction_mode": "agent",
                "active_subagent": "qa",
            }
        return super().chat(
            message=message,
            session_id=session_id,
            student_id=student_id,
            history=history,
            **kwargs,
        )


def serve(*, host: str, port: int, redis_url: str, worker_label: str,
          student_id: str, mysql_port: int | None, lock_mode: str) -> None:
    from app.config import Config

    Config.DEMO_MODE = True
    Config.DEMO_STUDENT_ID = student_id
    Config.DEMO_PRINCIPAL_ID = f"demo:{student_id}"
    Config.REDIS_URL = redis_url
    Config.SESSION_TURN_LOCK_MODE = lock_mode
    Config.SESSION_TURN_LOCK_LEASE_SECONDS = 5.0
    Config.SESSION_TURN_LOCK_WAIT_SECONDS = 5.0
    if mysql_port is not None:
        Config.MYSQL_PORT = mysql_port

    from app.services.db import mysql_client as mysql_module
    from app.services.db import redis_client as redis_module
    from app.services.db.redis_client import RedisClient

    mysql_module._mysql_client = None
    isolated_redis = RedisClient(url=redis_url)
    redis_module._redis_client = isolated_redis

    from app.services import session_manager as session_module
    from app.services.session_manager import SessionManager

    manager = SessionManager(redis_client=isolated_redis)
    manager._save_session_to_mysql = lambda _state: None
    manager._save_message_to_mysql = lambda *_args, **_kwargs: None
    manager._update_mysql_title = lambda *_args, **_kwargs: None
    session_module._session_manager = manager

    from app.services.agent_runtime import schema_ensure
    from app.services.agent_runtime import session_coordinator
    from app.services.agent_runtime import turn_idempotency

    schema_ensure.ensure_runtime_schema = lambda: None
    session_coordinator._coordinator = None
    turn_idempotency._ledger = None

    from app import create_app
    import app.services.agent_runtime as agent_runtime
    from app.services.agent_runtime import gated_tools

    gated_tools.enqueue_audit = lambda **_kwargs: "acceptance-audit-disabled"
    def break_redis() -> None:
        isolated_redis.close()
        isolated_redis.url = "redis://127.0.0.1:1/15"

    agent_runtime.get_supervisor_service = lambda: CrashSupervisor(
        isolated_redis.client,
        worker_label=worker_label,
        break_redis=break_redis,
    )
    app = create_app()

    @app.post("/__acceptance__/redis-unreachable")
    def make_redis_unreachable():
        from flask import jsonify

        break_redis()
        return jsonify({"ok": True, "worker": worker_label})

    from werkzeug.serving import make_server

    server = make_server(host, port, app, threaded=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--worker-label", required=True)
    parser.add_argument("--student-id", default="acceptance_student")
    parser.add_argument("--mysql-port", type=int)
    parser.add_argument(
        "--lock-mode",
        choices=("redis_required", "redis_preferred"),
        default="redis_required",
    )
    args = parser.parse_args()
    serve(
        host=args.host,
        port=args.port,
        redis_url=args.redis_url,
        worker_label=_token(args.worker_label),
        student_id=_token(args.student_id),
        mysql_port=args.mysql_port,
        lock_mode=args.lock_mode,
    )


if __name__ == "__main__":
    main()
