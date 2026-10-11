"""角色API

提供专家角色相关的API端点。
"""

from flask import Blueprint, request, jsonify
from ..services.agent_runtime.role_runtime import get_role_runtime
from ._helpers import (
    ScopeError,
    require_owner,
    resolve_request_scope,
    safe_bad_request,
    safe_not_found,
    scope_error_response,
)
from config.role_loader import list_roles, get_role

roles_bp = Blueprint('roles', __name__, url_prefix='/api/roles')


@roles_bp.route('/list', methods=['GET'])
def get_roles():
    """获取所有可用角色"""
    roles = list_roles()
    return jsonify({
        'roles': roles,
        'total': len(roles),
    })


@roles_bp.route('/<role_id>', methods=['GET'])
def get_role_info(role_id: str):
    """获取指定角色详情"""
    role = get_role(role_id)
    if not role:
        return jsonify({'error': f'角色 {role_id} 不存在'}), 404

    return jsonify(role.to_dict())


@roles_bp.route('/<role_id>/greeting', methods=['GET'])
def get_role_greeting(role_id: str):
    """获取角色问候语"""
    role = get_role(role_id)
    if not role:
        return jsonify({'error': f'角色 {role_id} 不存在'}), 404

    role_runtime = get_role_runtime()
    greeting = role_runtime.get_role_greeting(role_id)

    return jsonify({
        'role_id': role_id,
        'greeting': greeting,
    })


@roles_bp.route('/<role_id>/knowledge/<topic>', methods=['GET'])
def get_role_knowledge(role_id: str, topic: str):
    """获取角色知识"""
    role = get_role(role_id)
    if not role:
        return jsonify({'error': f'角色 {role_id} 不存在'}), 404

    role_runtime = get_role_runtime()
    knowledge = role_runtime.get_role_knowledge(role_id, topic)

    if knowledge is None:
        return jsonify({'error': f'角色没有 {topic} 相关知识'}), 404

    return jsonify({
        'role_id': role_id,
        'topic': topic,
        'knowledge': knowledge,
    })


@roles_bp.route('/chat', methods=['POST'])
def chat_with_role():
    """Role chat is not yet wired into the canonical Agent turn runtime."""
    data = request.get_json(silent=True) or {}
    role_id = data.get('role_id')
    message = data.get('message')
    session_id = data.get('session_id')

    if not role_id or not message:
        return jsonify(safe_bad_request('缺少 role_id 或 message')), 400

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as exc:
        return scope_error_response(exc)
    if session_id:
        from ..services.session_manager import get_session_manager

        session = get_session_manager().get_session(session_id)
        if not session:
            return jsonify(safe_not_found('会话不存在')), 404
        try:
            require_owner(getattr(session, 'student_id', None), scope, resource='会话')
        except ScopeError as exc:
            return scope_error_response(exc)
    if not get_role(role_id):
        return jsonify(safe_not_found('角色不存在')), 404

    # RoleRuntime currently passes unsupported system_override/role_context
    # arguments to synchronous ModeRuntime.chat. Returning a typed unavailable
    # result is safer than accepting a client student_id and surfacing HTTP 500
    # or pretending this is a functioning, identity-bound Agent conversation.
    return jsonify({
        'success': False,
        'code': 'ROLE_CHAT_NOT_WIRED',
        'error': '角色对话尚未接入统一 Agent 会话运行时，请使用 /api/agent/chat',
    }), 501
