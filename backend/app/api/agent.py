"""
智能体对话 API 路由
/api/agent — ReAct Agent 多轮对话、会话管理
"""

from flask import request, jsonify

from . import agent_bp
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.agent')


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """智能体对话 (核心接口)"""
    data = request.get_json()
    message = data.get('message', '')
    session_id = data.get('session_id')

    if not message:
        return jsonify({"success": False, "error": "message 不能为空"}), 400

    # TODO: Redis 取 session → ReAct Agent 推理 → Tool 调用 → 回复 + 存储
    return jsonify({
        "success": True,
        "data": {
            "reply": "",
            "tool_calls": [],
            "session_id": session_id or ""
        }
    })


@agent_bp.route('/session/create', methods=['POST'])
def create_session():
    """创建新会话"""
    data = request.get_json() or {}
    student_id = data.get('student_id')

    # TODO: Redis 创建 session, MySQL 创建 conversation_history 记录
    return jsonify({
        "success": True,
        "data": {"session_id": ""}
    })


@agent_bp.route('/session/<session_id>/history', methods=['GET'])
def get_history(session_id: str):
    """获取会话历史"""
    # TODO: 从 MySQL conversation_history 查询
    return jsonify({
        "success": True,
        "data": {"messages": []}
    })


@agent_bp.route('/session/<session_id>', methods=['DELETE'])
def delete_session(session_id: str):
    """删除会话"""
    # TODO: Redis 删除 session, MySQL 标记 conversation 为 archived
    return jsonify({
        "success": True,
        "message": f"会话 {session_id} 已删除"
    })
