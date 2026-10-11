"""
智能体对话 API 路由
/api/agent — Supervisor 编排多轮对话、会话管理、SSE 流式

Agent 引擎切换:
- mode=supervisor | langgraph (默认): Supervisor + 黑板 + 子 Agent
- mode=langgraph_legacy: 旧单体 LangGraph
- mode=react (fallback): 手写 ReAct 循环
- mode=demo: 演示占位回复
"""

import json
import uuid
import threading
import traceback
from flask import request, jsonify, Response
from ..services.resume_diagnostic_report import ResumeReportStale

from . import agent_bp
from ._helpers import (
    ScopeError,
    api_handler,
    clamp_pagination,
    parse_json_field,
    require_owner,
    require_student_path,
    resolve_request_scope,
    safe_bad_request,
    safe_forbidden,
    safe_error,
    safe_not_found,
    scope_error_response,
)
from ..services.db import get_mysql_client
from ..services.session_manager import get_session_manager, SessionManager
from ..services.react_agent import ReactAgent
from ..services.agent_runtime.egress_pipeline import (
    project_session_context,
    project_session_messages,
    project_turn,
)
from ..services.agent_runtime.artifact_committer import get_artifact_committer
from ..services.agent_runtime.session_coordinator import get_session_coordinator
from ..services.agent_runtime.session_coordinator import (
    SessionCoordinationLost,
    SessionCoordinationUnavailable,
)
from ..services.agent_runtime.turn_idempotency import (
    InvalidIdempotencyKey,
    TurnIdempotencyConflict,
    TurnIdempotencyExpired,
    TurnIdempotencyInProgress,
    TurnIdempotencySettlementLost,
    TurnIdempotencyStorageUnavailable,
    TurnIdempotencyUnknown,
    canonical_request_sha256,
    get_turn_idempotency_ledger,
    validate_idempotency_key,
)
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.agent')

# Q33: 模块常量 — 避免魔法数与硬编码 demo 回复
_MAX_TB_LEN = 2000
_DEMO_REPLY = (
    "演示模式已接收你的问题。当前可展示职业探索、能力画像、"
    "人岗匹配和学习路径规划链路；接入 LLM_API_KEY 后可切换到 langgraph 模式。"
)
_PUBLIC_CONTEXT_KEYS = frozenset({
    "conversation_title",
    "interaction_mode",
    "ui_state",
})

react_agent_instance = None
langgraph_agent_instance = None
_react_agent_lock = threading.Lock()
_langgraph_agent_lock = threading.Lock()


def get_react_agent() -> ReactAgent:
    """获取或创建 ReAct Agent 实例 (fallback, 线程安全)"""
    global react_agent_instance
    if react_agent_instance is None:
        with _react_agent_lock:
            if react_agent_instance is None:
                react_agent_instance = ReactAgent()
    return react_agent_instance


def get_langgraph_agent():
    """获取或创建 LangGraph Agent 服务实例 (线程安全)"""
    global langgraph_agent_instance
    if langgraph_agent_instance is None:
        with _langgraph_agent_lock:
            if langgraph_agent_instance is None:
                # 延迟 import: 避免 langgraph_agent ↔ api 模块循环依赖
                from ..services.langgraph_agent import get_langgraph_agent as _get
                langgraph_agent_instance = _get()
    return langgraph_agent_instance


def _resolve_session(session_mgr, session_id, scope):
    """解析或创建会话, 返回 (session_id, session) 或 (None, None) 表示校验失败"""
    if session_id:
        session = session_mgr.get_session(session_id)
        if not session:
            return None, None  # 404
        require_owner(getattr(session, "student_id", None), scope, resource="会话")
    else:
        session = session_mgr.create_session(student_id=scope.student_id)
        session_id = session.session_id
    return session_id, session


def _resolve_session_for_turn(session_mgr, session_id, scope, *, keyed: bool):
    """Resolve keyed turns from authoritative Redis, never worker cache."""
    if not keyed:
        return _resolve_session(session_mgr, session_id, scope)
    try:
        session = session_mgr.refresh_session(
            session_id,
            require_authoritative=True,
        )
    except Exception as exc:
        raise SessionCoordinationUnavailable("权威会话状态不可用") from exc
    if session is None:
        return session_id, None
    require_owner(getattr(session, "student_id", None), scope, resource="会话")
    return session_id, session


def _verify_idempotent_replay_session(
    session_mgr, *, session_id, scope, expected_incarnation,
):
    """Linearize completed replay with session deletion/recreation."""
    coordinator = get_session_coordinator()
    with coordinator.lock(session_id):
        try:
            session = session_mgr.refresh_session(
                session_id,
                require_authoritative=True,
            )
        except Exception as exc:
            raise SessionCoordinationUnavailable("权威会话状态不可用") from exc
        if session is None:
            raise LookupError("会话不存在")
        require_owner(getattr(session, "student_id", None), scope, resource="会话")
        if getattr(session, "incarnation_id", None) != expected_incarnation:
            raise TurnIdempotencyConflict("会话实例已变化，旧结果不可重放")


def _normalize_mode(mode: str) -> str:
    """Map aliases: langgraph → supervisor (default runtime)."""
    mode = (mode or "supervisor").strip().lower()
    if mode in ("supervisor", "langgraph"):
        return "supervisor"
    if mode in ("langgraph_legacy", "legacy"):
        return "langgraph_legacy"
    if mode in ("react", "demo"):
        return mode
    return "supervisor"


def _idempotency_key_for_mode(mode: str, session_id: str | None) -> str | None:
    """Validate the optional standard header before any turn mutation."""
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    if mode != "supervisor":
        raise InvalidIdempotencyKey("Idempotency-Key 仅支持默认 Supervisor 模式")
    if not session_id:
        raise InvalidIdempotencyKey("使用 Idempotency-Key 前必须先创建并传入 session_id")
    return validate_idempotency_key(value)


def _turn_request_sha256(*, session_incarnation, mode, message, plan_mode, confirmed_constraints,
                         interaction_mode, attachment_ids, content_parts,
                         voice_turn_id, use_memory) -> str:
    """Hash the semantic fields after route alias/default normalization."""
    from ..services.agent_runtime.fast_path import visible_user_text

    return canonical_request_sha256({
        # A caller-controlled session_id may be deleted and recreated.  The
        # persisted server UUID is its incarnation boundary, preventing
        # an old completed envelope from replaying into a new empty session.
        "session_incarnation": session_incarnation,
        "mode": mode,
        "message": visible_user_text(message) if message else "",
        "plan_mode": bool(plan_mode),
        "confirmed_constraints": confirmed_constraints,
        "interaction_mode": interaction_mode,
        "attachment_ids": attachment_ids or [],
        "content_parts": content_parts,
        "voice_turn_id": voice_turn_id,
        "use_memory": use_memory,
    })


def _admit_idempotent_turn(*, key, student_id, session_id, session_incarnation,
                           mode, message,
                           plan_mode, confirmed_constraints, interaction_mode,
                           attachment_ids, content_parts, voice_turn_id,
                           use_memory):
    if key is None:
        return None, None
    ledger = get_turn_idempotency_ledger()
    admission = ledger.admit(
        student_id=student_id,
        session_id=session_id,
        key=key,
        request_sha256=_turn_request_sha256(
            session_incarnation=session_incarnation,
            mode=mode,
            message=message,
            plan_mode=plan_mode,
            confirmed_constraints=confirmed_constraints,
            interaction_mode=interaction_mode,
            attachment_ids=attachment_ids,
            content_parts=content_parts,
            voice_turn_id=voice_turn_id,
            use_memory=use_memory,
        ),
    )
    return ledger, admission


def _idempotency_error_response(exc):
    if isinstance(exc, InvalidIdempotencyKey):
        return jsonify({
            "success": False,
            "code": "INVALID_IDEMPOTENCY_KEY",
            "error": str(exc),
        }), 400
    if isinstance(exc, TurnIdempotencyConflict):
        return jsonify({
            "success": False,
            "code": "IDEMPOTENCY_KEY_CONFLICT",
            "error": "同一 Idempotency-Key 已用于不同请求。",
        }), 409
    if isinstance(exc, TurnIdempotencyInProgress):
        response = jsonify({
            "success": False,
            "code": "IDEMPOTENCY_IN_PROGRESS",
            "error": "同一请求仍在处理中，请稍后查询原请求结果。",
            "next_action": "retry_same_key_later",
        })
        response.headers["Retry-After"] = "2"
        return response, 409
    if isinstance(exc, TurnIdempotencyUnknown):
        return jsonify({
            "success": False,
            "code": "IDEMPOTENCY_OUTCOME_UNKNOWN",
            "error": "先前执行可能已产生副作用，系统不会自动重试。",
            "next_action": "reconcile_session_and_artifacts",
        }), 409
    if isinstance(exc, TurnIdempotencyExpired):
        return jsonify({
            "success": False,
            "code": "IDEMPOTENCY_RESULT_EXPIRED",
            "error": "该幂等键的结果已过保留期，墓碑仍阻止自动重执行。",
            "next_action": "reconcile_then_use_new_key",
        }), 409
    return jsonify({
        "success": False,
        "code": "IDEMPOTENCY_STORAGE_UNAVAILABLE",
        "error": "当前无法安全确认请求是否已执行，请稍后使用同一幂等键查询。",
    }), 503


def _legacy_engine_allowed() -> bool:
    from ..config import Config

    return bool(Config.DEMO_MODE and Config.ENABLE_LEGACY_AGENT_ENGINES)


def _assistant_message_metadata(data_dict, *, engine: str) -> dict:
    return {
        "tool_calls": data_dict.get("tool_calls", []),
        "thinking_steps": data_dict.get("thinking_steps", []),
        "parts_v1": data_dict.get("parts", []),
        "artifacts": data_dict.get("artifacts", []),
        "turn_id": data_dict.get("turn_id"),
        "outcome": data_dict.get("outcome"),
        "interaction_mode": data_dict.get("interaction_mode"),
        "upgrade_hint": data_dict.get("upgrade_hint"),
        "elapsed_seconds": data_dict.get("elapsed_seconds"),
        "engine": engine,
        "orchestration": data_dict.get("orchestration"),
    }


def _bind_turn_artifacts(data_dict, *, student_id: str, session_id: str) -> None:
    artifact_ids = [
        str(a.get("artifact_id"))
        for a in (data_dict.get("artifacts") or [])
        if isinstance(a, dict) and a.get("artifact_id")
    ]
    if not artifact_ids:
        return
    try:
        get_artifact_committer().bind_turn(
            artifact_ids,
            student_id=student_id,
            session_id=session_id,
            turn_id=data_dict.get("turn_id"),
        )
    except Exception as e:
        logger.warning("绑定 artifact turn 失败: %s", e)


def _run_supervisor(
    message,
    session_id,
    session,
    session_mgr,
    plan_mode=False,
    confirmed_constraints=None,
    interaction_mode=None,
    attachment_ids=None,
    content_parts=None,
    voice_turn_id=None,
    use_memory=None,
    prepared_turn=None,
    defer_context_update=False,
):
    from ..services.agent_runtime import get_supervisor_service

    history = session_mgr.get_history_for_agent(session_id, max_messages=16)
    summary = session_mgr.get_conversation_summary(session_id)
    session_ctx = dict(session.context) if session and isinstance(session.context, dict) else {}

    result = get_supervisor_service().chat(
        message=message,
        session_id=session_id,
        student_id=session.student_id,
        history=history[:-1] if history else [],
        conversation_summary=summary,
        session_context=session_ctx,
        plan_mode=bool(plan_mode),
        confirmed_constraints=confirmed_constraints,
        interaction_mode=interaction_mode,
        attachment_ids=attachment_ids,
        content_parts=content_parts,
        voice_turn_id=voice_turn_id,
        use_memory=use_memory,
        prepared_turn=prepared_turn,
    )

    imode = result.get("interaction_mode") or "ask"
    state = session_mgr.get_session(session_id)
    if state:
        ctx = dict(state.context) if isinstance(state.context, dict) else {}
        if result.get("blackboard") is not None:
            ctx["blackboard"] = result["blackboard"]
        ctx["interaction_mode"] = imode
        # Server-held, owner-bound Ask paging state. Never accept a cursor from
        # request JSON or expose the internal state through the public context.
        ctx["ask_evidence_continuation"] = result.get("ask_evidence_continuation")
        orch = dict(ctx.get("orchestration") or {}) if isinstance(ctx.get("orchestration"), dict) else {}
        orch["interaction_mode"] = imode
        ctx["orchestration"] = orch
        # 保存 context_epoch 用于下轮 prompt 变更检测
        if result.get("context_epoch") is not None:
            ctx["context_epoch"] = result["context_epoch"]
        if defer_context_update:
            # The request lane will atomically persist this with the assistant
            # message only after proving the Redis lease is still owned.
            result["_next_session_context"] = ctx
        else:
            session_mgr.update_session(session_id, context=ctx)

    public = project_turn(result)
    data = public.to_dict()
    return data, result


def _run_langgraph_legacy(message, session_id, student_context, history):
    """执行旧单体 LangGraph Agent"""
    from ..services.agent_runtime.legacy import chat_legacy

    result = chat_legacy(
        message=message,
        session_id=session_id,
        student_context=student_context,
        history=history,
    )
    result["session_id"] = session_id
    result["engine"] = "langgraph_legacy"
    data = project_turn(result).to_dict()
    return data, result


def _run_react(message, student_context, history):
    """执行 ReAct Agent, 返回 (data, response) — session_id 由调用方填充"""
    agent = get_react_agent()
    response = agent.chat(
        message=message,
        history=history,
        student_context=student_context,
    )
    data = {
        "reply": response.reply,
        "tool_calls": [tc.to_dict() for tc in response.tool_calls],
        "session_id": None,  # 由调用方填充
        "thinking_steps": response.thinking_steps,
        "iterations": response.total_iterations,
        "elapsed_seconds": response.elapsed_seconds,
        "engine": "react",
    }
    return project_turn(data).to_dict(), response


def _parse_sse_content(sse_chunk: str) -> str:
    """从 SSE chunk 中提取 assistant 内容片段, 非 content/text/token 类型返回空串"""
    try:
        chunk_data = json.loads(sse_chunk.replace("data: ", "").strip())
        if chunk_data.get("type") in ("content", "text", "token"):
            return chunk_data.get("content", chunk_data.get("text", "")) or ""
    except Exception:
        pass
    return ""


def _compute_tool_stats(tool_results: list) -> tuple:
    """从 tool_calls_json 行列表聚合工具调用统计, 返回 (tool_stats, total, success)"""
    tool_stats = {}
    total_tool_calls = 0
    successful_calls = 0
    for row in tool_results:
        tool_calls = parse_json_field(row.get('tool_calls_json'))
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            total_tool_calls += 1
            tool_name = tc.get('tool', 'unknown')
            bucket = tool_stats.setdefault(tool_name, {'count': 0, 'success': 0})
            bucket['count'] += 1
            result = tc.get('result')
            if result and 'error' not in str(result).lower():
                bucket['success'] += 1
                successful_calls += 1
    return tool_stats, total_tool_calls, successful_calls


def _prepare_and_record_user_turn(
    session_mgr, *, session_id, student_id, message, attachment_ids,
    content_parts, voice_turn_id, require_authoritative=False,
):
    """Normalize one user turn once for both persisted STM and Supervisor."""
    from ..services.agent_runtime.fast_path import visible_user_text
    from ..services.agent_runtime.media.prepare_turn import prepare_user_turn

    raw_message = visible_user_text(message) if message else ""
    prepared = prepare_user_turn(
        raw_message,
        session_id=session_id,
        student_id=student_id,
        attachment_ids=attachment_ids,
        content_parts=content_parts,
        voice_turn_id=voice_turn_id,
        mount=True,
    )
    stm_text = (prepared.get("visible_text") or raw_message or "").strip() or "[附件/语音回合]"
    session_mgr.add_message(
        session_id=session_id,
        role="user",
        content=stm_text,
        metadata={
            "attachment_ids": prepared.get("turn_file_ids") or [],
            "voice_turn_id": voice_turn_id,
            "content_parts": prepared.get("parts") or [],
            "mount_errors": prepared.get("mount_errors") or [],
        },
        **({"require_authoritative": True} if require_authoritative else {}),
    )
    return raw_message, prepared


def _run_recorded_supervisor_turn(
    session_mgr, session_id, session, student_id, message, *,
    plan_mode, confirmed_constraints, interaction_mode, attachment_ids,
    content_parts, voice_turn_id, use_memory, engine,
    idempotency_ledger=None, idempotency_claim=None,
    expected_session_incarnation=None,
):
    """Serialize admission, STM, execution and settlement for one session."""
    coordinator = get_session_coordinator()
    with coordinator.lock(session_id) as turn_lease:
        # An explicit durable admission must never settle against a
        # best-effort session projection, even when the deployment-wide
        # coordinator mode is the development ``redis_preferred`` mode.
        # Keyed requests therefore opt into authoritative Redis reads/writes
        # and fail closed if that state surface is unavailable.
        require_authoritative_session = bool(
            idempotency_claim is not None
            or (turn_lease and turn_lease.require_authoritative_session)
        )
        # _resolve_session may have run before another turn settled. Read the
        # latest context only after admission to this session's turn lane.
        # Another worker may have settled while this request waited for the
        # Redis lease.  Refresh before assembling history/context; a worker's
        # in-memory SessionManager cache is not authoritative cross-process.
        refresh_session = getattr(session_mgr, "refresh_session", None)
        if callable(refresh_session):
            try:
                session = refresh_session(
                    session_id,
                    require_authoritative=require_authoritative_session,
                )
            except Exception as exc:
                raise SessionCoordinationUnavailable("权威会话状态不可用") from exc
        else:
            if require_authoritative_session:
                raise SessionCoordinationUnavailable("权威会话刷新不可用")
            get_session = getattr(session_mgr, "get_session", None)
            if callable(get_session):
                session = get_session(session_id)
        if session is not None:
            if getattr(session, "student_id", None) != student_id:
                raise LookupError("会话不存在或无访问权限")
            if (
                expected_session_incarnation is not None
                and getattr(session, "incarnation_id", None)
                != expected_session_incarnation
            ):
                raise TurnIdempotencyConflict("会话实例已变化，旧准入不可执行")
        else:
            raise LookupError("会话不存在")
        if idempotency_claim is not None:
            # Route reservation happens before an SSE Response is constructed;
            # execution starts only here, after the fresh owner session check
            # and immediately before STM/model/tool work.
            idempotency_ledger.start(idempotency_claim)
        message, prepared = _prepare_and_record_user_turn(
            session_mgr, session_id=session_id, student_id=student_id,
            message=message, attachment_ids=attachment_ids,
            content_parts=content_parts, voice_turn_id=voice_turn_id,
            require_authoritative=require_authoritative_session,
        )
        data_dict, _result = _run_supervisor(
            message, session_id, session, session_mgr,
            plan_mode=plan_mode,
            confirmed_constraints=confirmed_constraints,
            interaction_mode=interaction_mode,
            attachment_ids=attachment_ids,
            content_parts=content_parts,
            voice_turn_id=None if prepared.get("voice_deduped") else voice_turn_id,
            use_memory=use_memory,
            prepared_turn=prepared,
            defer_context_update=require_authoritative_session,
        )
        # If a Redis watchdog lost ownership during an LLM/tool call, never
        # settle an assistant message under a lease another worker may hold.
        if turn_lease is not None:
            turn_lease.assert_held()
        if prepared.get("mount_errors"):
            data_dict.setdefault("mount_errors", prepared["mount_errors"])
            orch = data_dict.get("orchestration") or {}
            orch["mount_errors"] = prepared["mount_errors"]
            data_dict["orchestration"] = orch
        _bind_turn_artifacts(data_dict, student_id=student_id, session_id=session_id)
        session_mgr.add_message(
            session_id=session_id,
            role="assistant",
            content=data_dict["reply"],
            metadata=_assistant_message_metadata(data_dict, engine=engine),
            **({
                "require_authoritative": True,
                "context_update": _result.get("_next_session_context")
                    if isinstance(_result, dict) else None,
            } if require_authoritative_session else {}),
        )
        if turn_lease is not None:
            turn_lease.assert_held()
        if idempotency_claim is not None:
            idempotency_ledger.complete(
                idempotency_claim,
                response={"success": True, "data": data_dict},
                http_status=200,
            )
        return data_dict


def _build_demo_turn(*, session_id, student_id, interaction_mode):
    """Build the one public demo result shared by JSON and SSE transports."""
    turn = project_turn({
        "reply": _DEMO_REPLY,
        "tool_calls": [],
        "session_id": session_id or f"demo_{uuid.uuid4().hex[:12]}",
        "student_id": student_id,
        "elapsed_seconds": 0,
        "active_subagent": "demo_subagent",
        "engine": "demo",
        "interaction_mode": interaction_mode or "ask",
        "source": "demo_fallback",
        "orchestration": {"strategy": "demo", "active_subagent": "demo_subagent"},
    }).to_dict()
    turn["student_id"] = student_id
    turn["source"] = "demo_fallback"
    return turn


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """智能体对话 (默认 Supervisor；可回退 legacy / react / demo)"""
    data = request.get_json() or {}
    message = data.get('message', '')
    session_id = data.get('session_id')
    mode = _normalize_mode(data.get('mode', 'supervisor'))
    plan_mode = bool(data.get('plan_mode') or data.get('planMode'))
    confirmed_constraints = data.get('confirmed_constraints') or data.get('constraints')
    interaction_mode = (
        data.get('interaction_mode')
        or data.get('agent_mode')
        or data.get('interactionMode')
    )
    attachment_ids = data.get('attachment_ids') or data.get('attachmentIds') or []
    content_parts = data.get('content_parts') or data.get('contentParts')
    voice_turn_id = data.get('voice_turn_id') or data.get('voiceTurnId')
    use_memory = data.get('use_memory')
    if use_memory is None:
        use_memory = data.get('useMemory')
    if use_memory is not None:
        use_memory = bool(use_memory)

    if not message and not attachment_ids and not content_parts and not voice_turn_id:
        return jsonify(safe_bad_request("message 或附件/语音内容不能同时为空")), 400
    if not message:
        message = ""  # media-only turn; ModeRuntime fills from voice/parts

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    try:
        idempotency_key = _idempotency_key_for_mode(mode, session_id)
    except InvalidIdempotencyKey as e:
        return _idempotency_error_response(e)

    if mode == 'demo':
        demo_turn = _build_demo_turn(
            session_id=session_id,
            student_id=scope.student_id,
            interaction_mode=interaction_mode,
        )
        return jsonify({
            "success": True,
            "data": demo_turn,
        })

    if mode in ("langgraph_legacy", "react") and not _legacy_engine_allowed():
        return jsonify(safe_forbidden("旧 Agent 引擎默认关闭，仅允许 demo 开发开关显式启用")), 403
    if mode in ("langgraph_legacy", "react") and (attachment_ids or content_parts or voice_turn_id):
        return jsonify(safe_bad_request("旧 Agent 引擎不支持附件/语音输入")), 400

    session_mgr = get_session_manager()

    try:
        session_id, session = _resolve_session_for_turn(
            session_mgr,
            session_id,
            scope,
            keyed=idempotency_key is not None,
        )
        if session is None:
            return jsonify(safe_not_found("会话不存在")), 404

        if mode == 'supervisor':
            ledger, admission = _admit_idempotent_turn(
                key=idempotency_key,
                student_id=scope.student_id,
                session_id=session_id,
                session_incarnation=getattr(session, "incarnation_id", None),
                mode=mode,
                message=message,
                plan_mode=plan_mode,
                confirmed_constraints=confirmed_constraints,
                interaction_mode=interaction_mode,
                attachment_ids=attachment_ids,
                content_parts=content_parts,
                voice_turn_id=voice_turn_id,
                use_memory=use_memory,
            )
            if admission is not None and admission.replayed:
                _verify_idempotent_replay_session(
                    session_mgr,
                    session_id=session_id,
                    scope=scope,
                    expected_incarnation=getattr(session, "incarnation_id", None),
                )
                response = jsonify(admission.replay_response)
                response.headers["Idempotency-Replayed"] = "true"
                return response, admission.replay_status
            claim = admission.claim if admission is not None else None
            try:
                data_dict = _run_recorded_supervisor_turn(
                    session_mgr, session_id, session, scope.student_id, message,
                    plan_mode=plan_mode,
                    confirmed_constraints=confirmed_constraints,
                    interaction_mode=interaction_mode,
                    attachment_ids=attachment_ids,
                    content_parts=content_parts,
                    voice_turn_id=voice_turn_id,
                    use_memory=use_memory,
                    engine="supervisor",
                    idempotency_ledger=ledger,
                    idempotency_claim=claim,
                    expected_session_incarnation=getattr(
                        session, "incarnation_id", None
                    ) if claim is not None else None,
                )
            except Exception:
                if claim is not None:
                    ledger.mark_unknown(claim)
                raise
            return jsonify({"success": True, "data": data_dict})

        # Legacy engines retain their existing text-only execution path.
        message, _prepared = _prepare_and_record_user_turn(
            session_mgr, session_id=session_id, student_id=scope.student_id,
            message=message, attachment_ids=attachment_ids,
            content_parts=content_parts, voice_turn_id=voice_turn_id,
        )

        history = session_mgr.get_history_for_agent(session_id, max_messages=16)
        student_context = session_mgr.build_structured_context(
            session_id=session_id,
            student_id=session.student_id,
            include_recent_messages=(mode == 'langgraph_legacy'),
        )

        if mode == 'langgraph_legacy':
            data_dict, result = _run_langgraph_legacy(
                message, session_id, student_context, history,
            )
            session_mgr.add_message(
                session_id=session_id,
                role="assistant",
                content=data_dict["reply"],
                metadata=_assistant_message_metadata(data_dict, engine="langgraph_legacy"),
            )
            return jsonify({"success": True, "data": data_dict})

        # ── ReAct Agent (fallback) ──
        data_dict, response = _run_react(message, student_context, history)
        data_dict["session_id"] = session_id
        _bind_turn_artifacts(data_dict, student_id=scope.student_id, session_id=session_id)
        session_mgr.add_message(
            session_id=session_id,
            role="assistant",
            content=data_dict["reply"],
            metadata=_assistant_message_metadata(data_dict, engine="react"),
        )
        return jsonify({"success": True, "data": data_dict})

    except ResumeReportStale:
        return jsonify({"success": False, "code": "RESOURCE_VERSION_CONFLICT",
                        "error": "引用报告与当前已确认简历版本不一致，请从资料页面引用当前版本或重新生成报告。",
                        "next_action": "refresh_resource_reference"}), 409
    except (
        InvalidIdempotencyKey,
        TurnIdempotencyConflict,
        TurnIdempotencyInProgress,
        TurnIdempotencyUnknown,
        TurnIdempotencyExpired,
        TurnIdempotencyStorageUnavailable,
        TurnIdempotencySettlementLost,
    ) as e:
        # InvalidIdempotencyKey subclasses ValueError, so this must remain
        # above the generic input-validation branch.
        return _idempotency_error_response(e)
    except LookupError as e:
        return jsonify(safe_not_found(str(e))), 404
    except (ValueError, PermissionError) as e:
        return jsonify(safe_bad_request(str(e))), 400
    except ScopeError as e:
        return scope_error_response(e)
    except (SessionCoordinationUnavailable, SessionCoordinationLost):
        return jsonify({"success": False, "code": "SESSION_COORDINATION_UNAVAILABLE",
                        "error": "当前会话暂无法安全串行处理，请稍后重试。"}), 503
    except Exception as e:
        tb = traceback.format_exc()
        if len(tb) > _MAX_TB_LEN:
            tb = tb[:_MAX_TB_LEN] + f"\n... (truncated, total {len(tb)} chars)"
        logger.error(f"Agent 对话失败: {e}\n{tb}")
        return jsonify(safe_error("Agent 对话", e, message="对话处理失败，请稍后重试")), 500


@agent_bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """SSE 流式对话 (默认 Supervisor；mode=langgraph_legacy 走旧流式)"""
    data = request.get_json() or {}
    message = data.get('message', '')
    session_id = data.get('session_id')
    mode = _normalize_mode(data.get('mode', 'supervisor'))
    plan_mode = bool(data.get('plan_mode') or data.get('planMode'))
    confirmed_constraints = data.get('confirmed_constraints') or data.get('constraints')
    interaction_mode = (
        data.get('interaction_mode')
        or data.get('agent_mode')
        or data.get('interactionMode')
    )
    attachment_ids = data.get('attachment_ids') or data.get('attachmentIds') or []
    content_parts = data.get('content_parts') or data.get('contentParts')
    voice_turn_id = data.get('voice_turn_id') or data.get('voiceTurnId')
    use_memory = data.get('use_memory')
    if use_memory is None:
        use_memory = data.get('useMemory')
    if use_memory is not None:
        use_memory = bool(use_memory)

    if not message and not attachment_ids and not content_parts and not voice_turn_id:
        return jsonify(safe_bad_request("message 或附件/语音内容不能同时为空")), 400
    if not message:
        message = ""

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    try:
        idempotency_key = _idempotency_key_for_mode(mode, session_id)
    except InvalidIdempotencyKey as e:
        return _idempotency_error_response(e)

    if mode == "demo":
        demo_turn = _build_demo_turn(
            session_id=session_id,
            student_id=scope.student_id,
            interaction_mode=interaction_mode,
        )

        def generate_demo():
            yield ": connected\n\n"
            for event in demo_turn.get("events") or []:
                sse_data = json.dumps(event, ensure_ascii=False)
                yield f"id: {event.get('event_id')}\nevent: {event.get('type')}\ndata: {sse_data}\n\n"
            yield "data: [DONE]\n\n"

        return Response(
            generate_demo(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        )

    if mode in ("langgraph_legacy", "react") and not _legacy_engine_allowed():
        return jsonify(safe_forbidden("旧 Agent 引擎默认关闭，仅允许 demo 开发开关显式启用")), 403
    if mode in ("langgraph_legacy", "react") and (attachment_ids or content_parts or voice_turn_id):
        return jsonify(safe_bad_request("旧 Agent 引擎不支持附件/语音输入")), 400

    session_mgr = get_session_manager()
    try:
        session_id, session = _resolve_session_for_turn(
            session_mgr,
            session_id,
            scope,
            keyed=idempotency_key is not None,
        )
    except ScopeError as e:
        return scope_error_response(e)
    except (SessionCoordinationUnavailable, SessionCoordinationLost):
        return jsonify({
            "success": False,
            "code": "SESSION_COORDINATION_UNAVAILABLE",
            "error": "当前会话暂无法安全核验，请稍后重试。",
        }), 503
    if session is None:
        return jsonify(safe_not_found("会话不存在")), 404

    ledger = None
    admission = None
    if mode == "supervisor":
        try:
            ledger, admission = _admit_idempotent_turn(
                key=idempotency_key,
                student_id=scope.student_id,
                session_id=session_id,
                session_incarnation=getattr(session, "incarnation_id", None),
                mode=mode,
                message=message,
                plan_mode=plan_mode,
                confirmed_constraints=confirmed_constraints,
                interaction_mode=interaction_mode,
                attachment_ids=attachment_ids,
                content_parts=content_parts,
                voice_turn_id=voice_turn_id,
                use_memory=use_memory,
            )
        except (
            InvalidIdempotencyKey,
            TurnIdempotencyConflict,
            TurnIdempotencyInProgress,
            TurnIdempotencyUnknown,
            TurnIdempotencyExpired,
            TurnIdempotencyStorageUnavailable,
            TurnIdempotencySettlementLost,
        ) as e:
            return _idempotency_error_response(e)
        if admission is not None and admission.replayed:
            try:
                _verify_idempotent_replay_session(
                    session_mgr,
                    session_id=session_id,
                    scope=scope,
                    expected_incarnation=getattr(session, "incarnation_id", None),
                )
            except ScopeError as e:
                return scope_error_response(e)
            except LookupError as e:
                return jsonify(safe_not_found(str(e))), 404
            except TurnIdempotencyConflict as e:
                return _idempotency_error_response(e)
            except (SessionCoordinationUnavailable, SessionCoordinationLost):
                return jsonify({
                    "success": False,
                    "code": "SESSION_COORDINATION_UNAVAILABLE",
                    "error": "当前会话暂无法安全核验，请稍后重试。",
                }), 503
            replay_turn = (admission.replay_response or {}).get("data") or {}

            def generate_replay():
                yield ": connected\n\n"
                for event in replay_turn.get("events") or []:
                    sse_data = json.dumps(event, ensure_ascii=False)
                    yield (
                        f"id: {event.get('event_id')}\n"
                        f"event: {event.get('type')}\n"
                        f"data: {sse_data}\n\n"
                    )
                yield "data: [DONE]\n\n"

            response = Response(
                generate_replay(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no',
                    'Idempotency-Replayed': 'true',
                },
            )
            return response, admission.replay_status

    if mode == "langgraph_legacy":
        message, _prepared = _prepare_and_record_user_turn(
            session_mgr, session_id=session_id, student_id=scope.student_id,
            message=message, attachment_ids=[], content_parts=None, voice_turn_id=None,
        )

    def generate_supervisor():
        # Establish SSE before model/tool work. This is a connection comment,
        # not a fabricated turn_start or claim of incremental progress.
        settled = False
        claim = admission.claim if admission is not None else None
        try:
            yield ": connected\n\n"
            try:
                supervisor_turn = _run_recorded_supervisor_turn(
                    session_mgr, session_id, session, scope.student_id, message,
                    plan_mode=plan_mode,
                    confirmed_constraints=confirmed_constraints,
                    interaction_mode=interaction_mode,
                    attachment_ids=attachment_ids,
                    content_parts=content_parts,
                    voice_turn_id=voice_turn_id,
                    use_memory=use_memory,
                    engine="supervisor_stream",
                    idempotency_ledger=ledger,
                    idempotency_claim=claim,
                    expected_session_incarnation=getattr(
                        session, "incarnation_id", None
                    ) if claim is not None else None,
                )
                settled = True
                for event in supervisor_turn.get("events") or []:
                    sse_data = json.dumps(event, ensure_ascii=False)
                    yield f"id: {event.get('event_id')}\nevent: {event.get('type')}\ndata: {sse_data}\n\n"
            except ResumeReportStale:
                error_data = {"type": "error", "code": "RESOURCE_VERSION_CONFLICT",
                              "content": "引用报告与当前已确认简历版本不一致，请重新引用当前版本。"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except (LookupError, PermissionError, ScopeError):
                # Admission failed before STM write. Do not reflect a private ID,
                # filename or voice transcript back through the error event.
                error_data = {"type": "error", "code": "RESOURCE_NOT_FOUND",
                              "content": "输入资料不可用或无访问权限。"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except ValueError:
                error_data = {"type": "error", "code": "INVALID_INPUT",
                              "content": "输入格式无效或资料尚未就绪。"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except (SessionCoordinationUnavailable, SessionCoordinationLost):
                error_data = {"type": "error", "code": "SESSION_COORDINATION_UNAVAILABLE",
                              "content": "当前会话暂无法安全串行处理，请稍后重试。"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except (
                TurnIdempotencyStorageUnavailable,
                TurnIdempotencySettlementLost,
            ):
                error_data = {"type": "error", "code": "IDEMPOTENCY_OUTCOME_UNKNOWN",
                              "content": "执行结果无法安全结算，请核对会话与成果状态。"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error(f"SSE Supervisor 流式对话失败: {e}")
                error_data = {"type": "error", "code": "RUNTIME_ERROR",
                              "content": "对话处理失败，请稍后重试"}
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
            finally:
                if claim is not None and not settled:
                    ledger.mark_unknown(claim)
            yield "data: [DONE]\n\n"
        finally:
            # Closing a consumed generator before execution settles is also an
            # unknown outcome.  An entirely unconsumed reservation becomes
            # unknown when its stale window is next inspected.
            if claim is not None and not settled:
                ledger.mark_unknown(claim)

    def generate_legacy():
        student_context = session_mgr.build_structured_context(
            session_id=session_id,
            student_id=session.student_id,
            include_recent_messages=True,
        )
        history = session_mgr.get_history_for_agent(session_id, max_messages=16)
        assistant_content_parts = []
        try:
            from ..services.agent_runtime.legacy import chat_stream_legacy

            for sse_chunk in chat_stream_legacy(
                message=message,
                session_id=session_id,
                student_context=student_context,
                history=history,
            ):
                content = _parse_sse_content(sse_chunk)
                if content:
                    assistant_content_parts.append(content)
                yield sse_chunk
        except Exception as e:
            logger.error(f"SSE 流式对话失败: {e}")
            error_data = json.dumps(
                {"type": "error", "content": "对话处理失败，请稍后重试"},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if assistant_content_parts:
                session_mgr.add_message(
                    session_id=session_id,
                    role="assistant",
                    content="".join(assistant_content_parts),
                    metadata={"engine": "langgraph_legacy_stream"},
                )

    gen = generate_legacy if mode == "langgraph_legacy" else generate_supervisor
    return Response(
        gen(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@agent_bp.route('/session/create', methods=['POST'])
@api_handler("创建会话")
def create_session():
    """创建新会话（幂等：传入已有 session_id 则返回该会话）"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    session_mgr = get_session_manager()
    if session_id:
        existing = session_mgr.get_session(session_id)
        if existing:
            try:
                require_owner(existing.student_id, scope, resource="会话")
            except ScopeError as e:
                return scope_error_response(e)
            session = existing
        else:
            session = session_mgr.create_session(
                student_id=scope.student_id,
                session_id=session_id,
            )
    else:
        session = session_mgr.create_session(student_id=scope.student_id)

    return {
        "session_id": session.session_id,
        "student_id": session.student_id,
        "created_at": session.created_at,
    }


@agent_bp.route('/session/<session_id>/history', methods=['GET'])
@api_handler("获取会话历史")
def get_history(session_id: str):
    """获取会话历史"""
    limit, _ = clamp_pagination(request.args.get('limit', type=int), None, default_limit=50)
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify(safe_not_found("会话不存在")), 404
    try:
        require_owner(session.student_id, scope, resource="会话")
    except ScopeError as e:
        return scope_error_response(e)

    messages = project_session_messages(session_mgr.get_messages(session_id, limit=limit))

    return {
        "session_id": session_id,
        "student_id": session.student_id,
        "status": session.status.value,
        "messages": messages,
        "message_count": len(messages),
    }


@agent_bp.route('/artifacts/<artifact_id>', methods=['GET'])
@api_handler("获取 Agent 成果物索引")
def get_artifact(artifact_id: str):
    """Owner-scoped artifact lookup; resolve supported domain payloads."""
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    artifact = get_artifact_committer().get_for_owner(
        artifact_id,
        student_id=scope.student_id,
    )
    if not artifact:
        return jsonify(safe_not_found("成果物不存在")), 404
    if artifact.get("kind") == "action_plan":
        if artifact.get("resource_type") != "agent_action_plan":
            return jsonify(safe_not_found("学习计划记录不存在")), 404
        from ..services.agent_runtime.action_plan_store import get_action_plan_store

        source_session_id = str(artifact.get("session_id") or "")
        if not source_session_id:
            return jsonify(safe_not_found("学习计划来源会话不存在")), 404
        plan = get_action_plan_store().get_for_owner(
            str(artifact.get("resource_id") or ""),
            student_id=scope.student_id,
            session_id=source_session_id,
        )
        if not plan:
            return jsonify(safe_not_found("学习计划记录不存在")), 404
        artifact["content"] = {"body": plan["body"], "status": plan["status"]}
    return artifact


@agent_bp.route('/session/<session_id>', methods=['DELETE'])
@api_handler("删除会话")
def delete_session(session_id: str):
    """删除会话"""
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    try:
        session_mgr = get_session_manager()
        with get_session_coordinator().lock(session_id) as turn_lease:
            refresh_session = getattr(session_mgr, "refresh_session", None)
            if callable(refresh_session):
                try:
                    session = refresh_session(
                        session_id,
                        require_authoritative=bool(
                            turn_lease and turn_lease.require_authoritative_session
                        ),
                    )
                except Exception as exc:
                    raise SessionCoordinationUnavailable(
                        "权威会话状态不可用"
                    ) from exc
            else:
                session = session_mgr.get_session(session_id)
            if not session:
                return jsonify(safe_not_found("会话不存在")), 404
            require_owner(session.student_id, scope, resource="会话")
            success = session_mgr.delete_session(session_id)
    except ScopeError as e:
        return scope_error_response(e)
    except (SessionCoordinationUnavailable, SessionCoordinationLost):
        return jsonify({
            "success": False,
            "code": "SESSION_COORDINATION_UNAVAILABLE",
            "error": "当前会话暂无法安全删除，请稍后重试。",
        }), 503

    if not success:
        return jsonify(safe_not_found("会话不存在")), 404

    return {"message": f"会话 {session_id} 已删除"}


@agent_bp.route('/session/<session_id>/context', methods=['GET'])
@api_handler("获取会话上下文")
def get_session_context(session_id: str):
    """获取会话上下文"""
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify(safe_not_found("会话不存在")), 404
    try:
        require_owner(session.student_id, scope, resource="会话")
    except ScopeError as e:
        return scope_error_response(e)

    return {
        "session_id": session_id,
        "student_id": session.student_id,
        "context": project_session_context(session.context),
        "status": session.status.value,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@agent_bp.route('/session/<session_id>/context', methods=['PUT'])
@api_handler("更新会话上下文")
def update_session_context(session_id: str):
    """更新会话上下文"""
    data = request.get_json() or {}

    if not data:
        return jsonify(safe_bad_request("请求体不能为空")), 400

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify(safe_not_found("会话不存在")), 404
    try:
        require_owner(session.student_id, scope, resource="会话")
    except ScopeError as e:
        return scope_error_response(e)

    blocked = [key for key in data if key not in _PUBLIC_CONTEXT_KEYS]
    if blocked:
        return jsonify(safe_forbidden(f"不允许通过公开 API 更新上下文字段: {', '.join(blocked)}")), 403

    for key, value in data.items():
        session_mgr.set_context(session_id, key, value)

    return {"message": "上下文已更新"}


@agent_bp.route('/session/<session_id>/archive', methods=['POST'])
@api_handler("归档会话并 Reflection")
def archive_session(session_id: str):
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)
    if not session:
        return jsonify(safe_not_found("会话不存在")), 404
    try:
        require_owner(session.student_id, scope, resource="会话")
    except ScopeError as e:
        return scope_error_response(e)
    ok = session_mgr.archive_session(session_id)
    return {"session_id": session_id, "archived": ok}


@agent_bp.route('/memory/<student_id>', methods=['GET'])
@api_handler("导出学生 Memory Store Markdown")
def export_memory(student_id: str):
    from ..services.agent_runtime.memory_store import get_memory_store

    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
    except ScopeError as e:
        return scope_error_response(e)

    files = get_memory_store().export_markdown(student_id)
    return {"student_id": student_id, "files": files}


@agent_bp.route('/memory/<student_id>/USER.md', methods=['PUT'])
@api_handler("更新 USER.md")
def put_user_memory(student_id: str):
    from ..services.agent_runtime.memory_store import get_memory_store

    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
    except ScopeError as e:
        return scope_error_response(e)

    data = request.get_json() or {}
    body = data.get("body_md") or data.get("content") or ""
    if data.get("constraints"):
        result = get_memory_store().write_user_constraints(student_id, str(data["constraints"]))
    else:
        result = get_memory_store().write_entry(student_id, "USER.md", body, source="api")
    return result


@agent_bp.route('/memory/<student_id>/rollback', methods=['POST'])
@api_handler("回滚记忆 path")
def rollback_memory(student_id: str):
    from ..services.agent_runtime.memory_store import get_memory_store

    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
    except ScopeError as e:
        return scope_error_response(e)

    data = request.get_json() or {}
    path = data.get("path") or "MEMORY.md"
    result = get_memory_store().rollback(student_id, path)
    if not result:
        return jsonify(safe_not_found("无可回滚版本")), 404
    return result


@agent_bp.route('/sessions', methods=['GET'])
@api_handler("列出会话列表")
def list_sessions():
    """列出会话列表"""
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    student_id = scope.student_id
    status = request.args.get('status')
    limit, _ = clamp_pagination(request.args.get('limit', type=int), None, default_limit=50)

    session_mgr = get_session_manager()
    sessions = session_mgr.list_sessions(
        student_id=student_id,
        status=status,
        limit=limit,
    )

    from ..services.session_manager import derive_session_title

    items = []
    for s in sessions:
        ctx = s.context if isinstance(s.context, dict) else {}
        title = (
            str(ctx.get("conversation_title") or "").strip()
            or derive_session_title(s.messages)
        )
        preview = ""
        for m in reversed(s.messages or []):
            if m.get("role") in ("user", "assistant") and m.get("content"):
                preview = str(m["content"]).strip().replace("\n", " ")[:72]
                break
        items.append(
            {
                "session_id": s.session_id,
                "student_id": s.student_id,
                "status": s.status.value,
                "title": title,
                "preview": preview,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": len(s.messages),
            }
        )

    return {
        "sessions": items,
        "count": len(items),
    }


@agent_bp.route('/stats', methods=['GET'])
def get_agent_stats():
    """获取 Agent 工具执行统计"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            COUNT(*) as total_messages,
            COUNT(tool_calls_json) as messages_with_tools,
            AVG(elapsed_seconds) as avg_elapsed,
            MIN(elapsed_seconds) as min_elapsed,
            MAX(elapsed_seconds) as max_elapsed
        FROM conversation_message
        WHERE role = 'assistant'
        """

        results = mysql.execute(sql)
        stats = results[0] if results else {}

        tool_calls_sql = """
        SELECT tool_calls_json
        FROM conversation_message
        WHERE role = 'assistant' AND tool_calls_json IS NOT NULL
        """
        tool_results = mysql.execute(tool_calls_sql)

        tool_stats, total_tool_calls, successful_calls = _compute_tool_stats(tool_results)
        success_rate = (successful_calls / total_tool_calls * 100) if total_tool_calls > 0 else 0

        return jsonify({
            "success": True,
            "data": {
                "total_messages": stats.get('total_messages', 0),
                "messages_with_tools": stats.get('messages_with_tools', 0),
                "avg_elapsed_seconds": round(float(stats.get('avg_elapsed', 0) or 0), 2),
                "min_elapsed_seconds": round(float(stats.get('min_elapsed', 0) or 0), 2),
                "max_elapsed_seconds": round(float(stats.get('max_elapsed', 0) or 0), 2),
                "total_tool_calls": total_tool_calls,
                "successful_tool_calls": successful_calls,
                "tool_success_rate": round(success_rate, 2),
                "tool_breakdown": tool_stats,
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取 Agent 统计", e, message="查询失败，请稍后重试")), 500
