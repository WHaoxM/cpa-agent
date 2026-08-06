"""
站内消息 API — /api/messages*
共享运营公告 + 按 user_id 的已读/删除状态
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import jsonify

from . import messages_bp
from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from ..repositories.messages_repo import messages_repo
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.api.messages")


def _fmt_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value)
    if len(text) >= 16:
        return text[:16]
    return text


def _serialize_row(row: dict) -> dict:
    body = str(row.get("body") or "")
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "body": body,
        "content": body,
        "type": str(row.get("type") or ""),
        "read": bool(row.get("is_read")),
        "created_at": _fmt_time(row.get("created_at")),
        "time": _fmt_time(row.get("created_at")),
    }


def _user_id() -> str:
    return resolve_request_scope().student_id


@messages_bp.route("/messages", methods=["GET"])
def list_messages():
    """列出对当前用户可见的公告（排除已删除）。"""
    try:
        user_id = _user_id()
        rows = messages_repo.list_for_user(user_id)
        data = [_serialize_row(r) for r in rows]
        return jsonify({"success": True, "data": data, "count": len(data)})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取消息列表", e)), 500


@messages_bp.route("/messages/<message_id>/read", methods=["POST"])
def mark_message_read(message_id: str):
    """标记已读（按 user_id）。"""
    try:
        user_id = _user_id()
        ok = messages_repo.mark_read(user_id=user_id, message_id=message_id)
        if not ok:
            return jsonify({"success": False, "error": f"消息不存在: {message_id}"}), 404
        return jsonify({"success": True, "data": {"id": message_id, "read": True}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("标记消息已读", e)), 500


@messages_bp.route("/messages/<message_id>", methods=["DELETE"])
def delete_message(message_id: str):
    """软删除（仅对该 user_id 隐藏）。"""
    try:
        user_id = _user_id()
        ok = messages_repo.soft_delete(user_id=user_id, message_id=message_id)
        if not ok:
            return jsonify({"success": False, "error": f"消息不存在: {message_id}"}), 404
        return jsonify({"success": True, "data": {"id": message_id, "deleted": True}})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("删除消息", e)), 500
