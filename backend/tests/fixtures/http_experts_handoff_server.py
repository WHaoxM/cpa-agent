"""Isolated real-socket Experts handoff host; never a production entry point.

Real: Flask routes, Supervisor/ModeRuntime, plan gate, scoped LangGraph and
server-side plan verifier. Substitutes: Lead/model, pipeline writer, MySQL,
Redis. The writer records owner-bound rows only after a scoped tool call.
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
    args = parser.parse_args()
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(CPA_DEMO_MODE="false", CPA_DEV_AUTH="1", PYTHONUTF8="1")

    from app import create_app
    from app.config import Config
    from app.services import db, session_manager
    from app.services.agent_runtime import mode_runtime, schema_ensure, scoped_invoke
    from app.services.agent_runtime.media import file_store, schema
    from app.services.agent_runtime.session_coordinator import SessionCoordinator
    from app.services.db import mysql_client
    from app.services import langgraph_agent
    from app.services.agent_runtime import evidence_retrieval
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    from test_session_turn_coordination import _SharedSessionRedis
    from werkzeug.serving import make_server

    Config.DEMO_MODE = False
    Config.UPLOAD_FOLDER = str(root / "uploads")

    class Redis(_SharedSessionRedis):
        def delete(self, key):
            return self.values.pop(key, None) is not None

        def hdel(self, name, key):
            self.hashes.get(name, {}).pop(key, None)

    class DomainRows:
        def __init__(self):
            self.tasks = {}
            self.rows = {}

        def execute(self, *_a, **_k):
            return 0

        def fetch_one(self, *_a, **_k):
            return None

        def write(self, role, owner):
            task_id = f"http-{role}-{len(self.tasks) + 1}"
            result_id = f"result-{role}-{len(self.tasks) + 1}"
            pipeline = {"profile": "L2_generate_portrait", "match": "L3_run_match",
                        "report": "L4_generate_report"}[role]
            if role == "profile":
                value = {"source_portrait_task_id": task_id, "summary": "confirmed profile"}
                result = {"portrait_id": result_id, "portrait": value}
            elif role == "match":
                from app.services.job_publication import content_sha256
                value = {"source_match_task_id": task_id, "jobs": [{"id": "fixture-job"}]}
                result = {"match_id": result_id, "verified": True,
                          "match_sha256": content_sha256(value)}
            else:
                value = {"report_id": result_id, "student_id": owner,
                         "sources": [{"kind": "fixture", "id": "fixture-job"}]}
                result = {"report_id": result_id, "verified": True, "sources": value["sources"]}
            self.tasks[task_id] = {"task_id": task_id, "student_id": owner,
                                   "pipeline": pipeline, "status": "completed", "result_json": result}
            self.rows[result_id] = (role, owner, value)
            return task_id

        def fetch_all(self, sql, params):
            if "FROM pipeline_task" in sql:
                row = self.tasks.get(params.get("tid"))
                return [row] if row and row["student_id"] == params.get("sid") else []
            rid = params.get("rid")
            item = self.rows.get(rid)
            if item and item[1] == params.get("sid"):
                role, _, value = item
                if role == "profile" and "FROM student_portrait_4d" in sql:
                    return [{"portrait_json": value}]
                if role == "match" and "FROM match_result" in sql:
                    return [{"result_json": value}]
                if role == "report" and "FROM career_report" in sql:
                    return [{"status": "completed", "report_json": value}]
            return []

    mysql = DomainRows()
    db.get_mysql_client = lambda: mysql
    mysql_client.get_mysql_client = lambda: mysql
    schema_ensure.ensure_runtime_schema = lambda: None
    schema.ensure_media_schema = lambda *_a, **_k: False
    file_store.ensure_media_schema = schema.ensure_media_schema
    file_store._svc = file_store.AgentFileService(mysql_client=mysql)
    session_manager._session_manager = session_manager.SessionManager(redis_client=Redis())
    evidence_retrieval.build_evidence_pack = lambda *_a, **_k: {
        "status": "completed", "items": [], "sources": [],
        "coverage": {"complete": True, "returned": 0, "total": 0},
        "has_more": False, "next_offset": None, "unavailable": [], "continuations": [],
    }
    mode_runtime.get_memory_store = lambda: SimpleNamespace(mount_for_supervisor=lambda *_a: "")
    mode_runtime.fork_enqueue = lambda *_a, **_k: "not-dispatched"

    brief = "目标是杭州的数据分析岗位；只考虑本地工作，学习计划限定为六周。"

    def lead(message, **_kwargs):
        scenario = "reject" if "REJECT_PROOF" in message else "success"
        return {"action": "run_plan", "template": "end_to_end", "source": "fixture-lead",
                "user_job": "execute", "goal_summary": brief, "handoff_brief": brief,
                "scenario": scenario, "missing_slots": [], "plan_sketch": []}

    mode_runtime.experts_lead_decide = lead

    def gated_tools(role, gate_ctx, ui_collector):
        @tool("pipeline_run")
        def pipeline_run() -> str:
            """Write one owner-bound deterministic business result for this step."""
            task_id = mysql.write(role, gate_ctx.student_id)
            ui_collector.append({"tool_name": "pipeline_run", "status": "done", "success": True,
                                 "artifact_candidates": [{"resource_type": "pipeline_task",
                                                          "resource_id": task_id}]})
            return json.dumps({"success": True, "status": "completed", "task_id": task_id})

        return [pipeline_run]

    scoped_invoke.build_gated_tools = gated_tools

    class StepModel:
        def invoke(self, messages):
            system = next(m.content for m in messages if isinstance(m, SystemMessage))
            packet = json.loads(system.split("【TaskPacket】\n", 1)[1].split("\n\n", 1)[0])
            role = packet["subagent"]
            user = next(m.content for m in messages if isinstance(m, HumanMessage))
            if any(isinstance(m, ToolMessage) for m in messages):
                return AIMessage(content=f"{role} 已提交可核验业务成果")
            observation = {"role": role, "goal": packet["goal"], "user": user,
                           "scenario": "reject" if "REJECT_PROOF" in user else "success"}
            with (root / "steps.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(observation, ensure_ascii=False) + "\n")
            if "REJECT_PROOF" in user:
                return AIMessage(content='```blackboard\n{"portrait.current":{"verified":true}}\n```')
            return AIMessage(content="", tool_calls=[{"name": "pipeline_run", "args": {},
                                                        "id": f"call-{role}"}])

    langgraph_agent._build_llm_with_tools = lambda *_a: StepModel()
    from app.api import agent
    agent.get_session_coordinator = lambda: SessionCoordinator()
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    (root / "ready.json").write_text(json.dumps({
        "pid": os.getpid(), "base_url": f"http://127.0.0.1:{server.server_port}",
        "real": ["Flask TCP", "Supervisor", "ModeRuntime", "plan", "scoped LangGraph", "plan verifier"],
        "substitutes": ["Lead decision", "step model", "pipeline writer", "MySQL rows", "Redis adapter"],
    }), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
