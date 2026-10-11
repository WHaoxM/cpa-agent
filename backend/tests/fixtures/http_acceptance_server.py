"""Loopback-only HTTP acceptance host; never a production entry point.

Real: app factory, routes, dev-auth, session manager/coordinator, TXT ingest,
resource catalogs and public egress. Substitutes: Supervisor, Redis and MySQL.
The Redis substitute reuses the existing session-coordination test adapter.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(CPA_DEMO_MODE="false", CPA_DEV_AUTH="1", PYTHONUTF8="1")

    from app import create_app
    from app.config import Config
    from app.utils.logger import setup_logger
    from app.services import db, session_manager
    from app.services import agent_runtime
    from app.services.agent_runtime import schema_ensure
    from app.services.agent_runtime.media import file_store, schema
    from app.services.agent_runtime.session_coordinator import SessionCoordinator
    from test_session_turn_coordination import _SharedSessionRedis
    from werkzeug.serving import make_server

    setup_logger("cpa-agent", str(root / "logs"))
    Config.DEMO_MODE = False
    Config.UPLOAD_FOLDER = str(root / "uploads")

    class SnapshotRedis(_SharedSessionRedis):
        def set(self, key, value, **kwargs):
            result = super().set(key, value, **kwargs)
            # A test-only observation of the authoritative adapter write,
            # not a snapshot manufactured from the HTTP response/cache.
            if key.startswith("cpa:session:"):
                sid = key.removeprefix("cpa:session:")
                (root / f"{sid}.state.json").write_text(value, encoding="utf-8")
            return result

        def delete(self, key):
            return self.values.pop(key, None) is not None

        def hdel(self, name, key):
            self.hashes.get(name, {}).pop(key, None)

    # No real database connections or schema writes are permitted here.
    mysql = SimpleNamespace(execute=lambda *_a, **_k: 0,
                            fetch_one=lambda *_a, **_k: None,
                            fetch_all=lambda *_a, **_k: [])
    db.get_mysql_client = lambda: mysql
    schema_ensure.ensure_runtime_schema = lambda: None
    schema.ensure_media_schema = lambda *_a, **_k: False
    file_store.ensure_media_schema = schema.ensure_media_schema
    file_store._svc = file_store.AgentFileService(mysql_client=mysql)
    session_manager._session_manager = session_manager.SessionManager(redis_client=SnapshotRedis())

    class DeterministicSupervisor:
        def chat(self, **kwargs):
            history = kwargs["history"]
            prepared = kwargs.get("prepared_turn") or {}
            observation = {
                "session_id": kwargs["session_id"], "message": kwargs["message"],
                "history": history, "session_context": kwargs["session_context"],
                "prepared_turn": prepared,
            }
            with (root / "supervisor-calls.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(observation, ensure_ascii=False) + "\n")
            count = sum(item["role"] == "user" for item in history) + 1
            return {
                "reply": f"deterministic turn {count}",
                "session_id": kwargs["session_id"], "interaction_mode": "ask",
                "blackboard": {"acceptance_turn_count": count},
            }

    agent_runtime.get_supervisor_service = lambda: DeterministicSupervisor()
    from app.api import agent
    coordinator = SessionCoordinator()  # Deliberately single-worker; no Redis claim.
    agent.get_session_coordinator = lambda: coordinator
    app = create_app()
    server = make_server("127.0.0.1", args.port, app, threaded=True)
    # Publish readiness only after imports, app creation and bind succeeded.
    (root / "ready.json").write_text(json.dumps({
        "pid": os.getpid(), "base_url": f"http://127.0.0.1:{server.server_port}",
        "substitutes": ["Supervisor.chat", "Redis adapter", "MySQL adapter"],
        "real": ["Flask TCP server", "session routes", "TXT parser", "disk resources"],
    }), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
