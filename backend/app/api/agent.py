"""
智能体对话 API 路由
/api/agent — LangGraph Agent 多轮对话、会话管理、SSE 流式

Agent 引擎切换:
- mode=langgraph (默认): LangGraph StateGraph + ToolNode + PyMySQLSaver
- mode=react (fallback): 手写 ReAct 循环 (兼容旧模式)
"""

import uuid
import threading
from flask import request, jsonify, Response

from . import agent_bp
from ._helpers import parse_json_field
from ..services.session_manager import get_session_manager, SessionManager
from ..services.react_agent import ReactAgent
from ..utils.logger import get_logger
from ..exceptions import ErrorCode

logger = get_logger('cpa-agent.api.agent')

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
                from ..services.langgraph_agent import get_langgraph_agent as _get
                langgraph_agent_instance = _get()
    return langgraph_agent_instance


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """智能体对话 (核心接口, 支持 LangGraph + ReAct fallback)"""
    data = request.get_json() or {}
    message = data.get('message', '')
    session_id = data.get('session_id')
    mode = data.get('mode', 'langgraph')  # langgraph | react

    if not message:
        return jsonify({"success": False, "error": "message 不能为空"}), 400

    if mode == 'demo':
        demo_session_id = session_id or f"demo_{uuid.uuid4().hex[:12]}"
        return jsonify({
            "success": True,
            "data": {
                "reply": (
                    "演示模式已接收你的问题。当前可展示职业探索、能力画像、"
                    "人岗匹配和学习路径规划链路；接入 LLM_API_KEY 后可切换到 langgraph 模式。"
                ),
                "tool_calls": [],
                "session_id": demo_session_id,
                "elapsed_seconds": 0,
                "active_subagent": "demo_subagent",
                "engine": "demo",
                "source": "demo_fallback",
            }
        })

    session_mgr = get_session_manager()

    if session_id:
        session = session_mgr.get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
    else:
        student_id = data.get('student_id')
        session = session_mgr.create_session(student_id=student_id)
        session_id = session.session_id

    session_mgr.add_message(
        session_id=session_id,
        role="user",
        content=message,
    )

    history = session_mgr.get_history_for_agent(session_id, max_messages=20)

    student_context = session_mgr.build_structured_context(
        session_id=session_id,
        student_id=session.student_id,
    )

    try:
        if mode == 'langgraph':
            # ── LangGraph Agent ──
            agent = get_langgraph_agent()
            result = agent.chat(
                message=message,
                session_id=session_id,
                student_context=student_context,
                history=history,
            )

            session_mgr.add_message(
                session_id=session_id,
                role="assistant",
                content=result["reply"],
                metadata={
                    "tool_calls": result.get("tool_calls", []),
                    "engine": "langgraph",
                }
            )

            return jsonify({
                "success": True,
                "data": {
                    "reply": result["reply"],
                    "tool_calls": result.get("tool_calls", []),
                    "session_id": session_id,
                    "elapsed_seconds": result.get("elapsed_seconds", 0),
                    "active_subagent": result.get("active_subagent", "qa_subagent"),
                    "engine": "langgraph",
                }
            })

        else:
            # ── ReAct Agent (fallback) ──
            agent = get_react_agent()
            response = agent.chat(
                message=message,
                history=history,
                student_context=student_context,
            )

            session_mgr.add_message(
                session_id=session_id,
                role="assistant",
                content=response.reply,
                metadata={
                    "tool_calls": [tc.to_dict() for tc in response.tool_calls],
                    "iterations": response.total_iterations,
                    "engine": "react",
                }
            )

            return jsonify({
                "success": True,
                "data": {
                    "reply": response.reply,
                    "tool_calls": [tc.to_dict() for tc in response.tool_calls],
                    "session_id": session_id,
                    "thinking_steps": response.thinking_steps,
                    "iterations": response.total_iterations,
                    "elapsed_seconds": response.elapsed_seconds,
                    "engine": "react",
                }
            })

    except Exception as e:
        logger.error(f"Agent 对话失败: {e}")
        import traceback
        tb = traceback.format_exc()
        MAX_TB_LEN = 2000
        if len(tb) > MAX_TB_LEN:
            tb = tb[:MAX_TB_LEN] + f"\n... (truncated, total {len(tb)} chars)"
        logger.error(f"Agent 对话失败: {e}\n{tb}")
        return jsonify({
            "success": False,
            "error": "对话处理失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@agent_bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """SSE 流式对话 (仅 LangGraph)"""
    data = request.get_json() or {}
    message = data.get('message', '')
    session_id = data.get('session_id')

    if not message:
        return jsonify({"success": False, "error": "message 不能为空"}), 400

    session_mgr = get_session_manager()

    if not session_id:
        student_id = data.get('student_id')
        session = session_mgr.create_session(student_id=student_id)
        session_id = session.session_id

    session = session_mgr.get_session(session_id)
    student_context = session_mgr.build_structured_context(
        session_id=session_id,
        student_id=(session.student_id if session else None),
    )

    history = session_mgr.get_history_for_agent(session_id, max_messages=20)

    # ── D-18: 保存用户消息 ──
    session_mgr.add_message(
        session_id=session_id,
        role="user",
        content=message,
    )

    def generate():
        assistant_content_parts = []
        try:
            agent = get_langgraph_agent()
            for sse_chunk in agent.chat_stream(
                message=message,
                session_id=session_id,
                student_context=student_context,
                history=history,
            ):
                # ── D-18: 收集 assistant 内容片段 ──
                try:
                    import json as _json
                    chunk_data = _json.loads(sse_chunk.replace("data: ", "").strip())
                    if chunk_data.get("type") in ("content", "text", "token"):
                        content = chunk_data.get("content", chunk_data.get("text", ""))
                        if content:
                            assistant_content_parts.append(content)
                except Exception:
                    pass
                yield sse_chunk
        except Exception as e:
            logger.error(f"SSE 流式对话失败: {e}")
            import json
            error_data = json.dumps({"type": "error", "content": "对话处理失败，请稍后重试"}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # ── D-18: 流结束后保存 assistant 完整回复 ──
            if assistant_content_parts:
                session_mgr.add_message(
                    session_id=session_id,
                    role="assistant",
                    content="".join(assistant_content_parts),
                    metadata={
                        "engine": "langgraph_stream",
                        "orchestration": student_context.get("orchestration", {}),
                    },
                )

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@agent_bp.route('/session/create', methods=['POST'])
def create_session():
    """创建新会话（幂等：传入已有 session_id 则返回该会话）"""
    data = request.get_json() or {}
    student_id = data.get('student_id')
    session_id = data.get('session_id')

    session_mgr = get_session_manager()
    session = session_mgr.create_session(student_id=student_id, session_id=session_id)

    return jsonify({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "student_id": session.student_id,
            "created_at": session.created_at,
        }
    })


@agent_bp.route('/session/<session_id>/history', methods=['GET'])
def get_history(session_id: str):
    """获取会话历史"""
    limit = request.args.get('limit', 50, type=int)

    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify({"success": False, "error": "会话不存在"}), 404

    messages = session_mgr.get_messages(session_id, limit=limit)

    return jsonify({
        "success": True,
        "data": {
            "session_id": session_id,
            "student_id": session.student_id,
            "status": session.status.value,
            "messages": messages,
            "message_count": len(messages),
        }
    })


@agent_bp.route('/session/<session_id>', methods=['DELETE'])
def delete_session(session_id: str):
    """删除会话"""
    session_mgr = get_session_manager()
    success = session_mgr.delete_session(session_id)

    if success:
        return jsonify({
            "success": True,
            "message": f"会话 {session_id} 已删除"
        })
    else:
        return jsonify({
            "success": False,
            "error": "会话不存在"
        }), 404


@agent_bp.route('/session/<session_id>/context', methods=['GET'])
def get_session_context(session_id: str):
    """获取会话上下文"""
    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify({"success": False, "error": "会话不存在"}), 404

    return jsonify({
        "success": True,
        "data": {
            "session_id": session_id,
            "student_id": session.student_id,
            "context": session.context,
            "status": session.status.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
    })


@agent_bp.route('/session/<session_id>/context', methods=['PUT'])
def update_session_context(session_id: str):
    """更新会话上下文"""
    data = request.get_json() or {}

    if not data:
        return jsonify({"success": False, "error": "请求体不能为空"}), 400

    session_mgr = get_session_manager()
    session = session_mgr.get_session(session_id)

    if not session:
        return jsonify({"success": False, "error": "会话不存在"}), 404

    for key, value in data.items():
        session_mgr.set_context(session_id, key, value)

    return jsonify({
        "success": True,
        "message": "上下文已更新"
    })


@agent_bp.route('/sessions', methods=['GET'])
def list_sessions():
    """列出会话列表"""
    student_id = request.args.get('student_id')
    status = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)

    session_mgr = get_session_manager()
    sessions = session_mgr.list_sessions(
        student_id=student_id,
        status=status,
        limit=limit,
    )

    return jsonify({
        "success": True,
        "data": [
            {
                "session_id": s.session_id,
                "student_id": s.student_id,
                "status": s.status.value,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": len(s.messages),
            }
            for s in sessions
        ],
        "count": len(sessions),
    })


@agent_bp.route('/stats', methods=['GET'])
def get_agent_stats():
    """获取 Agent 工具执行统计"""
    try:
        from ..services.db import get_mysql_client
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

        tool_stats = {}
        total_tool_calls = 0
        successful_calls = 0

        for row in tool_results:
            tool_calls = row.get('tool_calls_json')
            if tool_calls:
                tool_calls = parse_json_field(tool_calls)
                if not isinstance(tool_calls, list):
                    continue

                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        total_tool_calls += 1
                        tool_name = tc.get('tool', 'unknown')
                        if tool_name not in tool_stats:
                            tool_stats[tool_name] = {'count': 0, 'success': 0}
                        tool_stats[tool_name]['count'] += 1

                        result = tc.get('result')
                        if result and 'error' not in str(result).lower():
                            tool_stats[tool_name]['success'] += 1
                            successful_calls += 1

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
        logger.error(f"获取 Agent 统计失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500
