"""Opt-in, isolated real-model HTTP probe for Agent reply diagnostics.

This fixture compares one clean session with one session containing exactly
one prior user/assistant exchange. It keeps the current user message, owner,
initial session context, mode, prompt registry and model configuration fixed.
The only session-state variable is the prior STM history; captured inputs show
whether the current Agent path actually forwards it into model decisions.

Run from the repository root with the mkg interpreter and --live-model. The
fixture creates and drops a UUID-named MySQL schema, chooses an already-empty
Redis logical DB without flushing it, and deletes its own Redis sessions.
Subagent tools are disabled for safety; any validated non-QA role is stopped
before invocation. This is a test_client HTTP path, not a browser test.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import MethodType
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

SYNTHETIC_RESUME = (
    "合成简历：张同学，某普通本科计算机专业。项目：用 Python 写过一个课程推荐演示，"
    "负责接口和数据清洗；会 SQL、Git。无实习。"
)
QUESTION = (
    "请只读解释这里的学历/学校标签能确认到哪一步，最重要的资料缺口是什么。"
    "只基于这段文本，不要修改资料、评分或生成正式计划；未知就说明未知。"
)
MESSAGE = SYNTHETIC_RESUME + "\n" + QUESTION
OWNER = "diag-synthetic-20261017"
HISTORY = [
    {"role": "user", "content": "之前我们讨论过写一份正式计划。"},
    {"role": "assistant", "content": "好的，后续我会按计划格式汇报。"},
]
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


class UnsafePlan(RuntimeError):
    """A non-QA role was blocked before any child-agent call."""


def _sha(value: object) -> str:
    data = value if isinstance(value, bytes) else _json(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_text(value: object, limit: int = 3000) -> str:
    text = str(value or "")
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = PHONE_RE.sub("[PHONE]", text)
    if len(text) > limit:
        text = text[:limit] + "…[truncated]"
    return text


def _safe_decision(decision: object) -> dict:
    row = decision if isinstance(decision, dict) else {}
    steps = row.get("steps") if isinstance(row.get("steps"), list) else []
    clean_steps = []
    for step in steps[:6]:
        if not isinstance(step, dict):
            continue
        clean_steps.append({
            "role": _safe_text(step.get("role"), 40),
            "goal": _safe_text(step.get("goal"), 500),
            "skill_ids": [str(item)[:80] for item in (step.get("skill_ids") or [])[:8]],
        })
    return {
        "action": _safe_text(row.get("action"), 40),
        "source": _safe_text(row.get("source"), 80),
        "confidence": row.get("confidence"),
        "user_job": _safe_text(row.get("user_job"), 40),
        "goal_summary": _safe_text(row.get("goal_summary"), 500),
        "reply": _safe_text(row.get("reply"), 1200),
        "missing_slots": [str(item)[:80] for item in (row.get("missing_slots") or [])[:8]],
        "plan_sketch": [_safe_text(item, 120) for item in (row.get("plan_sketch") or [])[:6]],
        "steps": clean_steps,
        "template": _safe_text(row.get("template"), 80),
        "risk_if_run": _safe_text(row.get("risk_if_run"), 240),
        "plan_error": _safe_text(row.get("plan_error"), 240),
    }


def _safe_tool_calls(value: object) -> list[dict]:
    calls = value if isinstance(value, list) else []
    result = []
    for call in calls[:8]:
        if not isinstance(call, dict):
            continue
        result.append({
            "tool_name": _safe_text(call.get("tool_name") or call.get("name") or call.get("tool"), 100),
            "status": _safe_text(call.get("status"), 40),
            "success": call.get("success") if isinstance(call.get("success"), bool) else None,
            "summary": _safe_text(call.get("summary") or call.get("result_preview"), 240),
        })
    return result


def _safe_public_progress(orchestration: object) -> dict:
    orch = orchestration if isinstance(orchestration, dict) else {}
    progress = orch.get("progress") if isinstance(orch.get("progress"), dict) else {}
    supervisor = orch.get("supervisor_view") if isinstance(orch.get("supervisor_view"), dict) else {}
    out = {
        "strategy": _safe_text(orch.get("strategy"), 80),
        "active_subagent": _safe_text(orch.get("active_subagent"), 80),
        "progress": {},
    }
    for source in (progress, supervisor):
        for key in ("lead_action", "lead_source", "missing_slots", "plan_sketch", "executed_steps", "path"):
            if key not in source:
                continue
            value = source[key]
            if key in {"missing_slots", "plan_sketch"} and isinstance(value, list):
                out["progress"][key] = [_safe_text(item, 160) for item in value[:8]]
            elif key == "executed_steps" and isinstance(value, list):
                out["progress"][key] = [
                    {
                        "label": _safe_text(item.get("label") or item.get("role"), 100),
                        "status": _safe_text(item.get("status"), 40),
                    }
                    for item in value[:8] if isinstance(item, dict)
                ]
            else:
                out["progress"][key] = _safe_text(value, 160)
    return out


def _ui_projection(data: dict) -> dict:
    """Project only fields consumed by the current Vue workbench components."""
    parts = data.get("parts") if isinstance(data.get("parts"), list) else []
    if not parts:
        # Mirrors publicTurnToMessageParts() fallback in src/api/agent.ts.
        progress = (data.get("orchestration") or {}).get("progress")
        parts = ([{"type": "progress", "title": "本轮进度", "progress": progress}]
                 if isinstance(progress, dict) else [])
        if data.get("reply"):
            parts.append({"type": "text", "text": data["reply"]})

    rendered = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "")
        if kind == "text":
            rendered.append({"type": "text", "text": _safe_text(part.get("text"), 8000)})
        elif kind == "progress":
            progress = part.get("progress") if isinstance(part.get("progress"), dict) else {}
            sketch = progress.get("plan_sketch") if isinstance(progress.get("plan_sketch"), list) else []
            steps = progress.get("executed_steps") if isinstance(progress.get("executed_steps"), list) else []
            slots = progress.get("missing_slots") if isinstance(progress.get("missing_slots"), list) else []
            # PlanProgress.vue prefers executedSteps over planSketch and keeps
            # its <details> card collapsed by default. Keep the API payload
            # and the actually rendered projection distinct: a plan sketch
            # may be present in the JSON but hidden once executed steps exist.
            has_details = bool(sketch or steps or slots or progress.get("filenames")
                               or isinstance(progress.get("document_complete"), bool))
            rendered.append({
                "type": "progress",
                "title": _safe_text(part.get("title") or "本轮进度", 100),
                "expanded_by_default": False,
                "summary_only_fallback": _safe_text(part.get("summary"), 240) if not has_details else "",
                "visible_step_labels": [
                    _safe_text(step.get("label") or "任务步骤", 100)
                    for step in steps[:6] if isinstance(step, dict)
                ],
                "visible_plan_sketch": ([] if steps else
                    [_safe_text(item, 120) for item in sketch[:4]]),
                "missing_slots": [_safe_text(item, 80) for item in slots[:4]],
            })
        elif kind == "tool_call":
            rendered.append({"type": "tool_call", "tool_call": _safe_tool_calls([part.get("tool_call")])[0]})
        else:
            rendered.append({"type": kind})
    outcome = data.get("outcome") if isinstance(data.get("outcome"), dict) else {}
    status = str(outcome.get("status") or "")
    outcome_notice = {
        "needs_input": "还需要一点信息，可以直接在这里继续回答。",
        "running": "任务进行中。",
        "partial": "这轮尚未完整完成，以下内容可能不完整。可以补充信息后重试。",
        "failed": "这轮没有完成，请重试。",
    }.get(status, "")
    thinking = data.get("thinking_steps") if isinstance(data.get("thinking_steps"), list) else []
    # AIAssistant.vue's HTTP path maps thinking to the optional collapsible
    # block; MessagePartsRenderer owns the visible text/progress/status parts.
    return {
        "verified_by": "HTTP payload mapped through current Vue source contract; no browser DOM run",
        "assistant_reply": _safe_text(data.get("reply"), 8000),
        "thinking_block_visible_if_opened": bool([item for item in thinking if str(item).strip()]),
        "outcome_status": status,
        "outcome_notice": outcome_notice,
        "parts_in_render_order": rendered,
    }


def _versions() -> dict:
    names = ("Flask", "SQLAlchemy", "alembic", "celery", "redis", "langgraph")
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not-installed"
    return result


def _isolated_redis_url(base_url: str) -> tuple[str, int]:
    import redis

    parsed = urlsplit(base_url)
    # Use an empty DB; never FLUSHDB/FLUSHALL. Local Redis fixtures normally
    # expose DB 0-15. If none is empty, fail rather than borrow shared state.
    for database in range(15, 0, -1):
        candidate = urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))
        client = redis.Redis.from_url(candidate, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
            if client.dbsize() == 0:
                client.close()
                return candidate, database
        finally:
            client.close()
    raise RuntimeError("No empty Redis logical DB (1-15) is available; no Redis state was changed")


def _create_isolated_mysql_schema() -> tuple[str, object]:
    import pymysql
    from app.config import Config

    schema = "cpa_diag_" + uuid.uuid4().hex[:12]
    admin = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        autocommit=True,
    )
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
        created = True
        result = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={schema}", "upgrade", "head"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode:
            raise RuntimeError("isolated MySQL migration failed; subprocess output omitted")
        return schema, admin
    except Exception:
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        admin.close()
        raise


def _ui_public_data(data: object) -> dict:
    row = data if isinstance(data, dict) else {}
    parts = row.get("parts") if isinstance(row.get("parts"), list) else []
    orchestration = row.get("orchestration") if isinstance(row.get("orchestration"), dict) else {}
    return {
        "reply": _safe_text(row.get("reply"), 8000),
        "outcome": row.get("outcome") if isinstance(row.get("outcome"), dict) else {},
        "thinking_steps": [_safe_text(item, 300) for item in (row.get("thinking_steps") or [])[:8]],
        "parts": [
            ({"type": "text", "text": _safe_text(part.get("text"), 8000)}
             if isinstance(part, dict) and part.get("type") == "text"
             else {"type": "progress", "title": _safe_text(part.get("title"), 100),
                   "summary": _safe_text(part.get("summary"), 240),
                   "progress": _safe_public_progress({"progress": part.get("progress")}).get("progress", {})}
             if isinstance(part, dict) and part.get("type") == "progress"
             else {"type": str(part.get("type") or "unknown")}
             if isinstance(part, dict) else {"type": "unknown"})
            for part in parts[:24]
        ],
        "tool_calls": _safe_tool_calls(row.get("tool_calls")),
        "orchestration": _safe_public_progress(orchestration),
        "usage_ledger": row.get("usage_ledger") if isinstance(row.get("usage_ledger"), dict) else None,
        "active_subagent": _safe_text(row.get("active_subagent"), 80),
        "interaction_mode": _safe_text(row.get("interaction_mode"), 40),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="confirm two real model HTTP calls")
    parser.add_argument("--output", type=Path, help="write the sanitized JSON evidence to this local file")
    args = parser.parse_args()
    if not args.live_model:
        parser.error("must pass --live-model to make the two explicitly requested model calls")

    from app.config import Config

    redis_url, redis_db = _isolated_redis_url(Config.REDIS_URL)
    schema, admin = _create_isolated_mysql_schema()
    cleanup_state = {"done": False}

    def cleanup_schema() -> None:
        if cleanup_state["done"]:
            return
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        except Exception:
            pass
        try:
            admin.close()
        except Exception:
            pass
        cleanup_state["done"] = True

    atexit.register(cleanup_schema)
    Config.MYSQL_DATABASE = schema
    Config.REDIS_URL = redis_url
    Config.DEMO_MODE = False
    Config.SESSION_TURN_LOCK_MODE = "redis_required"

    import app.services.db.mysql_client as mysql_module
    import app.services.db.redis_client as redis_module
    import app.services.session_manager as session_module
    import app.services.agent_runtime.session_coordinator as coordinator_module
    import app.services.agent_runtime.mode_runtime as mode_module
    import app.services.agent_runtime.supervisor as supervisor_module
    mysql_module._mysql_client = None
    redis_module._redis_client = None
    session_module._session_manager = None
    coordinator_module._coordinator = None
    mode_module._runtime = None
    supervisor_module._supervisor = None

    from flask import g, request
    from app import create_app
    from app.api._helpers import Principal
    from app.services.session_manager import get_session_manager, derive_session_title
    from app.services.agent_runtime.mode_runtime import get_mode_runtime
    from app.services.agent_runtime.supervisor import get_supervisor_service
    from app.services.agent_runtime.model_router import route_model
    from app.services.db.mysql_client import get_mysql_client
    from app.services.db.redis_client import get_redis_client

    app = create_app()
    app.testing = True
    owner_principal = Principal(OWNER, OWNER, source="diagnostic_fixture")

    @app.before_request
    def _inject_diag_principal():
        g.cpa_principal = owner_principal

    manager = get_session_manager()
    # Conversation persistence is intentionally not part of this diagnosis.
    manager._save_session_to_mysql = lambda _state: None
    manager._save_message_to_mysql = lambda *_args, **_kwargs: None
    manager._update_mysql_title = lambda *_args, **_kwargs: None

    runtime = get_mode_runtime()
    service = get_supervisor_service()
    if getattr(service, "_runtime", None) is not runtime:
        raise RuntimeError("diagnostic could not prove the API uses the instrumented ModeRuntime")

    cases: dict[str, dict] = {}
    current_case = {"name": None}
    original_chat = runtime.chat
    original_lead = mode_module.experts_lead_decide
    original_invoke = mode_module._invoke_subagent
    original_finalize = runtime._finalize_plan_reply
    original_fork_enqueue = mode_module.fork_enqueue
    session_ids: list[str] = []

    def capture_chat(_self, message, **kwargs):
        case = cases[current_case["name"]]
        history = kwargs.get("history") or []
        ctx = kwargs.get("session_context") if isinstance(kwargs.get("session_context"), dict) else {}
        case["runtime_input"] = {
            "message_sha256": _sha(message),
            "history": [
                {"role": str(row.get("role") or ""), "content": _safe_text(row.get("content"), 1000)}
                for row in history if isinstance(row, dict)
            ],
            "history_count": len(history),
            "conversation_summary_sha256": _sha(kwargs.get("conversation_summary") or ""),
            "session_context_sha256": _sha(ctx),
            "attachment_count": len(kwargs.get("attachment_ids") or []),
            "content_parts_count": len(kwargs.get("content_parts") or []),
            "interaction_mode": _safe_text(kwargs.get("interaction_mode"), 40),
            "plan_mode": bool(kwargs.get("plan_mode")),
        }
        return original_chat(message, **kwargs)

    def capture_lead(*args, **kwargs):
        decision = original_lead(*args, **kwargs)
        case = cases[current_case["name"]]
        blackboard = kwargs.get("blackboard")
        board = blackboard.to_dict() if hasattr(blackboard, "to_dict") else {}
        lead_context = str(kwargs.get("summary") or "")
        case.setdefault("lead_calls", []).append({
            "inputs": {
                "message_sha256": _sha(args[0] if args else ""),
                "card_sha256": _sha(kwargs.get("card") or ""),
                "summary_sha256": _sha(lead_context),
                "recent_history_marker_present": "近期对话历史（不可信" in lead_context,
                "fixture_prior_user_message_present": HISTORY[0]["content"] in lead_context,
                "fixture_prior_assistant_labeled_unverified": (
                    "历史助手（未验证）" in lead_context and HISTORY[1]["content"] in lead_context
                ),
                "memory_digest_sha256": _sha(kwargs.get("memory_digest") or ""),
                "blackboard_sha256": _sha(board),
                "plan_mode": bool(kwargs.get("plan_mode")),
                "interaction_mode": _safe_text(kwargs.get("interaction_mode"), 40),
            },
            "decision": _safe_decision(decision),
        })
        return decision

    def safe_capture_subagent(packet, blackboard, session_id, **kwargs):
        role = str(getattr(packet, "subagent", ""))
        case = cases[current_case["name"]]
        if role != "qa":
            case.setdefault("safety_aborts", []).append({"role": role, "reason": "non-QA role blocked before invocation"})
            raise UnsafePlan("diagnostic probe only permits read-only QA")
        # The same read-only policy applies in both arms; all tools stay off.
        output = original_invoke(
            packet, blackboard, session_id,
            **{**kwargs, "allow_tools": False},
        )
        raw = output if isinstance(output, dict) else {}
        case.setdefault("child_outputs", []).append({
            "role": role,
            "skill_ids": [str(item)[:80] for item in (getattr(packet, "skill_ids", None) or [])[:8]],
            "raw_reply": _safe_text(raw.get("reply"), 3000),
            "incomplete": bool(raw.get("incomplete")),
            "tool_calls": _safe_tool_calls(raw.get("tool_calls")),
            "permission_denials": [
                {"tool": _safe_text(item.get("tool"), 100), "reason": _safe_text(item.get("reason"), 160)}
                for item in (raw.get("permission_denials") or [])[:8] if isinstance(item, dict)
            ],
            "skill_versions": raw.get("skill_versions") if isinstance(raw.get("skill_versions"), list) else [],
        })
        return output

    def capture_finalize(_self, **kwargs):
        reply = original_finalize(**kwargs)
        cases[current_case["name"]]["main_agent_finalizer"] = {
            "steps_sha256": _sha(kwargs.get("steps") or []),
            "raw_reply": _safe_text(reply, 3000),
        }
        return reply

    def suppress_fork_enqueue(kind, session_id, payload=None, **kwargs):
        case = cases.get(current_case["name"])
        if case is not None:
            case.setdefault("suppressed_async_jobs", []).append(str(kind))
        return "diagnostic_rollup_suppressed"

    runtime.chat = MethodType(capture_chat, runtime)
    runtime._finalize_plan_reply = MethodType(capture_finalize, runtime)
    mode_module.experts_lead_decide = capture_lead
    mode_module._invoke_subagent = safe_capture_subagent
    mode_module.fork_enqueue = suppress_fork_enqueue

    try:
        client = get_redis_client().client
        client.ping()
        schema_head = get_mysql_client().execute("SELECT version_num FROM alembic_version")
        if not schema_head or schema_head[0].get("version_num") != "0009_agent_run_dispatch":
            raise RuntimeError("isolated schema is not at expected Alembic head 0009")

        with app.test_client() as test_client:
            for name, with_history in (("clean", False), ("history", True)):
                current_case["name"] = name
                cases[name] = {"history_seeded": with_history, "lead_calls": [], "child_outputs": []}
                session_id = "diag_" + uuid.uuid4().hex
                session_ids.append(session_id)
                state = manager.create_session(student_id=OWNER, session_id=session_id)
                title = derive_session_title([{"role": "user", "content": MESSAGE}])
                if with_history:
                    for item in HISTORY:
                        manager.add_message(session_id, item["role"], item["content"])
                # Equalize server-held context after history seeding. This
                # ensures the history arm differs only in its message list.
                from app.services.session_manager import SessionStatus
                state = manager.get_session(session_id)
                state.status = SessionStatus.ACTIVE
                state.context = {"conversation_title": title}
                manager._save_to_redis(state)
                before = manager.get_session(session_id)
                cases[name]["session_context_before_sha256"] = _sha(before.context or {})
                cases[name]["request"] = {
                    "method": "POST",
                    "path": "/api/agent/chat",
                    "body": {
                        "message_sha256": _sha(MESSAGE),
                        "message": MESSAGE,
                        "mode": "supervisor",
                        "interaction_mode": "agent",
                        "plan_mode": False,
                        "attachment_ids": [],
                        "content_parts": [],
                        "use_memory": False,
                    },
                    "session_id_sha256": _sha(session_id),
                }
                response = test_client.post(
                    "/api/agent/chat",
                    json={
                        "message": MESSAGE,
                        "session_id": session_id,
                        "mode": "supervisor",
                        "interaction_mode": "agent",
                        "plan_mode": False,
                        "attachment_ids": [],
                        "content_parts": [],
                        "use_memory": False,
                    },
                )
                cases[name]["http_status"] = response.status_code
                envelope = response.get_json(silent=True) or {}
                data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
                cases[name]["http_success"] = bool(envelope.get("success"))
                cases[name]["http_error"] = _safe_text(envelope.get("error"), 500) if envelope.get("error") else None
                cases[name]["public_response"] = _ui_public_data(data)
                cases[name]["page_projection"] = _ui_projection(data)
                post = manager.get_session(session_id)
                cases[name]["session_after"] = {
                    "history_messages": [
                        {"role": str(row.get("role") or ""), "content": _safe_text(row.get("content"), 1000)}
                        for row in (post.messages or [])
                    ] if post else [],
                    "redis_deleted_after_capture": False,
                }

        clean = cases.get("clean", {})
        history = cases.get("history", {})
        clean_lead = clean.get("lead_calls") or []
        history_lead = history.get("lead_calls") or []
        def _decision_signature(rows):
            return _sha(rows[0].get("decision") if rows else {})
        output = {"comparison": {
            "same_message_hash": clean.get("request", {}).get("body", {}).get("message_sha256") == history.get("request", {}).get("body", {}).get("message_sha256"),
            "same_initial_context_hash": clean.get("session_context_before_sha256") == history.get("session_context_before_sha256"),
            "runtime_history_counts": [
                (clean.get("runtime_input") or {}).get("history_count"),
                (history.get("runtime_input") or {}).get("history_count"),
            ],
            "same_lead_input_hashes": bool(clean_lead and history_lead) and clean_lead[0]["inputs"] == history_lead[0]["inputs"],
            "same_lead_decision": _decision_signature(clean_lead) == _decision_signature(history_lead),
            "same_child_raw_reply_hashes": [
                [_sha(item.get("raw_reply")) for item in clean.get("child_outputs", [])],
                [_sha(item.get("raw_reply")) for item in history.get("child_outputs", [])],
            ],
            "same_public_reply": clean.get("public_response", {}).get("reply") == history.get("public_response", {}).get("reply"),
            "same_page_parts": clean.get("page_projection", {}).get("parts_in_render_order") == history.get("page_projection", {}).get("parts_in_render_order"),
            "same_outcome_status": clean.get("page_projection", {}).get("outcome_status") == history.get("page_projection", {}).get("outcome_status"),
        }}
        output.update({
            "schema_version": "agent-reply-differential-probe-v1",
            "fixture": str(Path(__file__).resolve().relative_to(ROOT)),
            "timestamp_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
            "versions": _versions(),
            "isolation": {
                "owner": OWNER,
                "mysql_schema": schema,
                "mysql_head": "0009_agent_run_dispatch",
                "mysql_schema_created_and_dropped_by_fixture": True,
                "redis_logical_db": redis_db,
                "redis_flushed": False,
                "redis_sessions_deleted": True,
                "conversation_mysql_writes": "disabled on this SessionManager instance",
                "global_business_data_writes": "no tools permitted; non-QA role aborted before child invocation",
            },
            "fixed_input": {"resume": SYNTHETIC_RESUME, "question": QUESTION, "message": MESSAGE},
            "history_delta": HISTORY,
            "test_path": "Flask test_client -> /api/agent/chat -> ModeRuntime -> public turn; UI projection follows current Vue HTTP mapping/components, without browser DOM",
            "model_route": route_model("lead_classify"),
            "instrumentation": {
                "real_model_calls": True,
                "subagent_tools_forced_off": True,
                "non_qa_child_roles_blocked": True,
                "forked_rollup_jobs_suppressed": True,
                "captures": ["normalized Lead decision", "raw QA reply", "main finalizer reply", "HTTP public response", "workbench part projection"],
            },
            "cases": cases,
        })
        serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            destination = args.output.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(serialized, encoding="utf-8")
            print(f"diagnostic-json-written: {destination}")
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            print(serialized, end="")
        return 0
    finally:
        for session_id in session_ids:
            try:
                manager.delete_session(session_id)
            except Exception:
                pass
        try:
            get_redis_client().close()
        except Exception:
            pass
        try:
            mysql = get_mysql_client()
            mysql.close()
        except Exception:
            pass
        cleanup_schema()
        # Restore module-level monkeypatches if an embedding test imports this fixture.
        runtime.chat = original_chat
        runtime._finalize_plan_reply = original_finalize
        mode_module.experts_lead_decide = original_lead
        mode_module._invoke_subagent = original_invoke
        mode_module.fork_enqueue = original_fork_enqueue


if __name__ == "__main__":
    output: dict = {}
    raise SystemExit(main())
