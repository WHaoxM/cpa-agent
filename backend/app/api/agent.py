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
        orch = dict(ctx.get("orchestration") or {}) if isinstance(ctx.get("orchestration"), dict) else {}
        orch["interaction_mode"] = imode
        ctx["orchestration"] = orch
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

    if mode == 'demo':
        demo_session_id = session_id or f"demo_{uuid.uuid4().hex[:12]}"
        demo_turn = project_turn({
            "reply": _DEMO_REPLY,
            "tool_calls": [],
            "session_id": demo_session_id,
            "student_id": scope.student_id,
            "elapsed_seconds": 0,
            "active_subagent": "demo_subagent",
            "engine": "demo",
            "interaction_mode": interaction_mode or "ask",
            "source": "demo_fallback",
            "orchestration": {"strategy": "demo", "active_subagent": "demo_subagent"},
        }).to_dict()
        demo_turn["student_id"] = scope.student_id
        demo_turn["source"] = "demo_fallback"
        return jsonify({
            "success": True,
            "data": demo_turn,
        })

    if mode in ("langgraph_legacy", "react") and not _legacy_engine_allowed():
        return jsonify(safe_forbidden("旧 Agent 引擎默认关闭，仅允许 demo 开发开关显式启用")), 403

    session_mgr = get_session_manager()

    try:
        session_id, session = _resolve_session(session_mgr, session_id, scope)
        if session is None:
            return jsonify(safe_not_found("会话不存在")), 404

        # Normalize media BEFORE STM write (Claude/Qoder: parts → visible text)
        from ..services.agent_runtime.fast_path import visible_user_text
        from ..services.agent_runtime.media.prepare_turn import prepare_user_turn

        bound_student = scope.student_id
        raw_message = visible_user_text(message) if message else ""
        prepared = prepare_user_turn(
            raw_message,
            session_id=session_id,
            student_id=bound_student,
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
        )
        # Runtime reuses the prepared turn; STM and model view share one normalization.
        message = raw_message

        if mode == 'supervisor':
            data_dict, result = _run_supervisor(
                message, session_id, session, session_mgr,
                plan_mode=plan_mode,
                confirmed_constraints=confirmed_constraints,
                interaction_mode=interaction_mode,
                attachment_ids=attachment_ids,
                content_parts=content_parts,
                voice_turn_id=None if prepared.get("voice_deduped") else voice_turn_id,
                use_memory=use_memory,
                prepared_turn=prepared,
            )
            if prepared.get("mount_errors"):
                data_dict.setdefault("mount_errors", prepared["mount_errors"])
                orch = data_dict.get("orchestration") or {}
                orch["mount_errors"] = prepared["mount_errors"]
                data_dict["orchestration"] = orch
            _bind_turn_artifacts(data_dict, student_id=scope.student_id, session_id=session_id)
            session_mgr.add_message(
                session_id=session_id,
                role="assistant",
                content=data_dict["reply"],
                metadata=_assistant_message_metadata(data_dict, engine="supervisor"),
            )
            return jsonify({"success": True, "data": data_dict})

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
    use_memory = data.get('use_memory')
    if use_memory is None:
        use_memory = data.get('useMemory')
    if use_memory is not None:
        use_memory = bool(use_memory)

    if not message:
        return jsonify(safe_bad_request("message 不能为空")), 400

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    if mode in ("langgraph_legacy", "react") and not _legacy_engine_allowed():
        return jsonify(safe_forbidden("旧 Agent 引擎默认关闭，仅允许 demo 开发开关显式启用")), 403

    session_mgr = get_session_manager()
    from ..services.agent_runtime.fast_path import visible_user_text
    message = visible_user_text(message)

    try:
        session_id, session = _resolve_session(session_mgr, session_id, scope)
    except ScopeError as e:
        return scope_error_response(e)
    if session is None:
        return jsonify(safe_not_found("会话不存在")), 404

    session_mgr.add_message(
        session_id=session_id,
        role="user",
        content=message,
    )

    def generate_supervisor():
        assistant_content = ""
        final_turn = None
        try:
            data_dict, result = _run_supervisor(
                message, session_id, session, session_mgr,
                plan_mode=plan_mode,
                confirmed_constraints=confirmed_constraints,
                interaction_mode=interaction_mode,
                use_memory=use_memory,
            )
            final_turn = data_dict
            assistant_content = data_dict.get("reply") or ""
            for event in data_dict.get("events") or []:
                sse_data = json.dumps(event, ensure_ascii=False)
                yield f"id: {event.get('event_id')}\nevent: {event.get('type')}\ndata: {sse_data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"SSE Supervisor 流式对话失败: {e}")
            error_data = json.dumps(
                {"type": "error", "content": "对话处理失败，请稍后重试"},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if assistant_content and final_turn:
                _bind_turn_artifacts(final_turn, student_id=scope.student_id, session_id=session_id)
                session_mgr.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    metadata=_assistant_message_metadata(final_turn, engine="supervisor_stream"),
                )

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
            'Connection': 'keep-alive',
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
    """Owner-scoped artifact index lookup; payload stays in domain stores."""
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
    return artifact


@agent_bp.route('/session/<session_id>', methods=['DELETE'])
@api_handler("删除会话")
def delete_session(session_id: str):
    """删除会话"""
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
    success = session_mgr.delete_session(session_id)

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
