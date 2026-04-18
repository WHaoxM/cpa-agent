"""
智能体对话 API 路由
/api/agent — ReAct Agent 多轮对话、会话管理
"""

import uuid
from flask import request, jsonify

from . import agent_bp
from ..services.session_manager import get_session_manager, SessionManager
from ..services.react_agent import ReactAgent
from ..services.tools.graph_query import GraphQueryTool
from ..services.tools.db_query import DbQueryTool
from ..services.tools.semantic_search import SemanticSearchTool
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.agent')

agent_instance = None


def get_agent() -> ReactAgent:
    """获取或创建 Agent 实例"""
    global agent_instance
    if agent_instance is None:
        agent_instance = ReactAgent()
    return agent_instance


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """智能体对话 (核心接口)"""
    data = request.get_json()
    message = data.get('message', '')
    session_id = data.get('session_id')

    if not message:
        return jsonify({"success": False, "error": "message 不能为空"}), 400

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

    student_context = None
    if session.student_id:
        student_context = {
            "student_id": session.student_id,
            "session_id": session_id,
        }

    try:
        agent = get_agent()
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
            }
        })

    except Exception as e:
        logger.error(f"Agent 对话失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": f"对话处理失败: {str(e)}"
        }), 500


@agent_bp.route('/session/create', methods=['POST'])
def create_session():
    """创建新会话"""
    data = request.get_json() or {}
    student_id = data.get('student_id')

    session_mgr = get_session_manager()
    session = session_mgr.create_session(student_id=student_id)

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
    data = request.get_json()

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
