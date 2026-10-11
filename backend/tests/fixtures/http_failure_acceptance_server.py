"""Real-socket acceptance server for failure and cross-worker session tests.

The Flask application, agent routes, Redis-backed ``SessionManager``, Redis
lease coordinator, HTTP serialization and public egress are production code.
Only the nondeterministic model and its selected external tool are replaced:

* ``SIDE_EFFECT`` invokes the real PreToolUse/gated-tool wrapper around a
  deterministic mutating tool.  The tool commits one isolated Redis mutation
  and then raises ``TimeoutError``.
* ``SERIAL`` records entry/exit around a deterministic delay so two independent
  OS processes can prove the production Redis lease ordering.

The unrelated MySQL projection/audit paths are disabled.  Redis remains the
authoritative session and side-effect store for these scenarios.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any


BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


def _token(value: str) -> str:
    if not _SAFE_TOKEN.fullmatch(value or ""):
        raise ValueError("invalid acceptance token")
    return value


class DeterministicSupervisor:
    def __init__(self, redis_client: Any, *, worker_label: str):
        self._redis = redis_client
        self._worker_label = worker_label

    def chat(self, *, message: str, session_id: str, student_id: str,
             history: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        fields = str(message or "").split()
        command = fields[0] if fields else ""
        if command == "SIDE_EFFECT" and len(fields) == 2:
            return self._side_effect(
                marker=_token(fields[1]),
                session_id=session_id,
                student_id=student_id,
            )
        if command == "SERIAL" and len(fields) == 4:
            marker = _token(fields[1])
            turn = _token(fields[2])
            delay_ms = max(0, min(int(fields[3]), 3000))
            return self._serial(
                marker=marker,
                turn=turn,
                delay_ms=delay_ms,
                session_id=session_id,
                history=history,
            )
        return {
            "reply": "deterministic acceptance turn",
            "session_id": session_id,
            "interaction_mode": "agent",
            "active_subagent": "qa",
        }

    def _side_effect(self, *, marker: str, session_id: str,
                     student_id: str) -> dict[str, Any]:
        from langchain_core.tools import StructuredTool

        from app.services.agent_runtime.gated_tools import _wrap_one
        from app.services.agent_runtime.modes import InteractionMode
        from app.services.agent_runtime.pre_tool_use import GateContext

        effect_key = f"cpa:acceptance:effect:{marker}"

        def pipeline_run(pipeline: str, params: dict[str, Any],
                         student_id: str | None = None,
                         session_id: str | None = None) -> dict[str, Any]:
            # The mutation is committed before the transport-like timeout.  A
            # retry of this mutating tool would therefore duplicate the effect.
            tx = self._redis.pipeline(transaction=True)
            tx.hincrby(effect_key, "count", 1)
            tx.hset(effect_key, mapping={
                "student_id": str(student_id or ""),
                "session_id": str(session_id or ""),
                "pipeline": str(pipeline or ""),
                "nested_student_id": str((params or {}).get("student_id") or ""),
                "worker": self._worker_label,
            })
            tx.execute()
            raise TimeoutError("private substitute backend detail")

        base = StructuredTool.from_function(
            func=pipeline_run,
            name="pipeline_run",
            description="Acceptance-only deterministic mutating pipeline tool",
        )
        gate = GateContext(
            interaction_mode=InteractionMode.AGENT,
            subagent="profile",
            session_id=session_id,
            student_id=student_id,
        )
        calls: list[dict[str, Any]] = []
        wrapped = _wrap_one(base, gate, calls)
        wrapped.invoke({
            "pipeline": "acceptance_mutation",
            "params": {"student_id": "untrusted-request-owner"},
            "student_id": "untrusted-request-owner",
            "session_id": "untrusted-request-session",
        })
        return {
            # Empty on purpose: a failed tool without a usable answer is a
            # failed turn, rather than a fluent model sentence masking failure.
            "reply": "",
            "session_id": session_id,
            "interaction_mode": "agent",
            "active_subagent": "profile",
            "tool_calls": calls,
            "permission_denials": gate.denials,
            "orchestration": {
                "strategy": "agent_tool",
                "active_subagent": "profile",
            },
        }

    def _serial(self, *, marker: str, turn: str, delay_ms: int,
                session_id: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        event_key = f"cpa:acceptance:serial:{marker}"
        prior_roles = [str(item.get("role") or "") for item in history]
        self._redis.rpush(event_key, json.dumps({
            "phase": "start",
            "turn": turn,
            "worker": self._worker_label,
            "pid": os.getpid(),
            "prior_roles": prior_roles,
        }, ensure_ascii=False))
        time.sleep(delay_ms / 1000.0)
        self._redis.rpush(event_key, json.dumps({
            "phase": "end",
            "turn": turn,
            "worker": self._worker_label,
            "pid": os.getpid(),
        }, ensure_ascii=False))
        return {
            "reply": "deterministic serialized turn",
            "session_id": session_id,
            "interaction_mode": "agent",
            "active_subagent": "qa",
            "orchestration": {"strategy": "agent_direct", "active_subagent": "qa"},
        }


def serve(*, host: str, port: int, redis_url: str, worker_label: str) -> None:
    # Config loads the repository .env with override=True.  Set the runtime
    # attributes explicitly after import so this isolated process cannot drift
    # back to the developer's default Redis DB or preferred-lock mode.
    from app.config import Config

    Config.DEMO_MODE = True
    Config.DEMO_STUDENT_ID = "acceptance_student"
    Config.DEMO_PRINCIPAL_ID = "demo:acceptance_student"
    Config.REDIS_URL = redis_url
    Config.SESSION_TURN_LOCK_MODE = "redis_required"
    Config.SESSION_TURN_LOCK_LEASE_SECONDS = 5.0
    Config.SESSION_TURN_LOCK_WAIT_SECONDS = 5.0

    from app.services.db import redis_client as redis_module
    from app.services.db.redis_client import RedisClient

    isolated_redis = RedisClient(url=redis_url)
    redis_module._redis_client = isolated_redis

    from app.services import session_manager as session_module
    from app.services.session_manager import SessionManager

    manager = SessionManager(redis_client=isolated_redis)
    # MySQL is only a best-effort projection of this slice.  Keeping it out
    # prevents test records escaping the random Redis namespace.
    manager._save_session_to_mysql = lambda _state: None
    manager._save_message_to_mysql = lambda *_args, **_kwargs: None
    manager._update_mysql_title = lambda *_args, **_kwargs: None
    session_module._session_manager = manager

    from app.services.agent_runtime import schema_ensure
    from app.services.agent_runtime import session_coordinator

    schema_ensure.ensure_runtime_schema = lambda: None
    session_coordinator._coordinator = None

    from app import create_app
    import app.services.agent_runtime as agent_runtime
    from app.services.agent_runtime import gated_tools

    # Tool audit persistence is unrelated to public failure settlement; the
    # real gate/wrapper/result-channel path above remains intact.
    gated_tools.enqueue_audit = lambda **_kwargs: "acceptance-audit-disabled"
    agent_runtime.get_supervisor_service = lambda: DeterministicSupervisor(
        isolated_redis.client,
        worker_label=worker_label,
    )
    app = create_app()

    @app.post("/__acceptance__/redis-unreachable")
    def make_redis_unreachable():
        """Test control: next production Redis operation gets connection-refused."""
        from flask import jsonify

        isolated_redis.close()
        isolated_redis.url = "redis://127.0.0.1:1/15"
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
    args = parser.parse_args()
    serve(
        host=args.host,
        port=args.port,
        redis_url=args.redis_url,
        worker_label=_token(args.worker_label),
    )


if __name__ == "__main__":
    main()
