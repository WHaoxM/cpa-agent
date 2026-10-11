"""Real-socket RAG/evidence acceptance host with deterministic domain fixtures.

The HTTP routes, Agent turn preparation, resource/evidence adapters, budget,
ModeRuntime, public egress and session persistence are production code. Only
the backing MySQL repository, session Redis adapter and LLM are deterministic
test fixtures; this host never reaches external providers or shared databases.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]


class EvidenceRepository:
    """Small owner-scoped fake for the confirmed-profile read contract."""

    owner = "rag_http_owner"
    task_id = "task_resume_rag_http"

    def __init__(self):
        self.skills = [f"RAG-SKILL-{index:02d}" for index in range(12)]
        self.candidate = {
            "name": "RAG HTTP 合成用户",
            "school": "测试大学",
            "major": "计算机",
            "skills": self.skills,
            "projects": [],
            "competitions": [],
        }
        self.profile = {
            **self.candidate,
            "_resume_confirmation": {
                "task_id": self.task_id,
                "student_id": self.owner,
                "source_evidence": {"page_count": 1, "observed_pages": [1]},
            },
        }

    def fetch_all(self, sql, params=None):
        params = params or {}
        normalized = " ".join(str(sql).split())
        owner = params.get("sid") or params.get("student_id")
        if "FROM student_profile" in normalized:
            if owner == self.owner:
                return [{"profile_json": json.dumps(self.profile, ensure_ascii=False)}]
            return []
        if "JSON_UNQUOTE" in normalized:
            # There is intentionally no published L2 portrait in this fixture.
            return []
        if "FROM pipeline_task" in normalized and params.get("task_id") == self.task_id:
            return [{
                "task_id": self.task_id,
                "student_id": self.owner,
                "pipeline": "L0_parse_resume",
                "status": "completed",
                "result_json": json.dumps({
                    "confirmed": True,
                    "candidate_profile": self.candidate,
                }, ensure_ascii=False),
            }]
        return []

    def fetch_one(self, *_args, **_kwargs):
        return None

    def execute(self, *_args, **_kwargs):
        return 0


class DeterministicEvidenceLLM:
    """Structured selector and answer fixture; no provider call is possible."""

    def __init__(self, root: Path):
        self.root = root

    def chat(self, *, messages, **_kwargs):
        system = str(messages[0].get("content") or "") if messages else ""
        joined = "\n".join(str(message.get("content") or "") for message in messages)
        if "只负责为用户当前问题选择最小必要的只读证据" in system:
            stage = "evidence_selection"
            response = json.dumps({
                "needed": True,
                "answer_kind": "analysis",
                "reads": [{"id": "r0", "section": "skills"}],
            }, ensure_ascii=False)
        elif "只判断用户本轮话语是否要求继续读取" in system:
            stage = "continuation_intent"
            response = '{"continue_previous_page":true}'
        else:
            stage = "answer"
            response = "我依据本轮服务端选中的技能条目作答，并会遵守资料覆盖范围。"

        record = {
            "stage": stage,
            "messages": messages,
            "evidence_skill_ids": [
                skill for skill in EvidenceRepository().skills if skill in joined
            ],
        }
        evidence_start = '<SERVER_EVIDENCE kind="EvidencePack">\n'
        offset = joined.find(evidence_start)
        if offset >= 0:
            offset += len(evidence_start)
            evidence_end = joined.find("\n</SERVER_EVIDENCE>", offset)
            if evidence_end > offset:
                try:
                    record["evidence_pack"] = json.loads(joined[offset:evidence_end])
                except (TypeError, ValueError):
                    record["evidence_pack"] = {"parse_error": True}
        with (self.root / "llm-calls.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        CPA_DEMO_MODE="false",
        CPA_DEV_AUTH="1",
        CPA_SESSION_TURN_LOCK_MODE="local",
        PYTHONUTF8="1",
    )

    from app import create_app
    from app.config import Config
    from app.utils.logger import setup_logger
    from app.services import db, session_manager
    from app.services.agent_runtime import schema_ensure
    from app.services.agent_runtime.media import file_store, schema
    from app.services.agent_runtime.session_coordinator import SessionCoordinator
    from app.services.session_manager import SessionManager
    from test_session_turn_coordination import _SharedSessionRedis
    from werkzeug.serving import make_server

    setup_logger("cpa-agent", str(root / "logs"))
    Config.DEMO_MODE = False
    Config.UPLOAD_FOLDER = str(root / "uploads")
    mysql = EvidenceRepository()
    db.get_mysql_client = lambda: mysql
    schema_ensure.ensure_runtime_schema = lambda: None
    schema.ensure_media_schema = lambda *_a, **_k: False
    file_store.ensure_media_schema = schema.ensure_media_schema
    file_store._svc = file_store.AgentFileService(mysql_client=mysql)

    class SnapshotRedis(_SharedSessionRedis):
        def set(self, key, value, **kwargs):
            result = super().set(key, value, **kwargs)
            if key.startswith("cpa:session:"):
                sid = key.removeprefix("cpa:session:")
                (root / f"{sid}.state.json").write_text(value, encoding="utf-8")
            return result

        def delete(self, key):
            return self.values.pop(key, None) is not None

        def hdel(self, name, key):
            self.hashes.get(name, {}).pop(key, None)

    session_manager._session_manager = SessionManager(redis_client=SnapshotRedis())

    from app.utils import llm_client
    llm_client.get_llm_client = lambda: DeterministicEvidenceLLM(root)

    from app.api import agent
    agent.get_session_coordinator = lambda: SessionCoordinator()
    app = create_app()
    server = make_server("127.0.0.1", args.port, app, threaded=True)
    (root / "ready.json").write_text(json.dumps({
        "pid": os.getpid(),
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "substitutes": ["EvidenceRepository/MySQL", "Session Redis adapter", "LLM"],
        "real": [
            "Flask TCP server", "Agent API", "SessionManager", "resource catalog",
            "confirmed-profile owner checks", "EvidencePack selection/budget/paging",
            "ModeRuntime Ask", "public JSON/SSE egress",
        ],
    }), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
