"""
Agent media API — Claude Files + Qoder session resources + Voice Turn.
"""

from flask import request, jsonify

from . import agent_bp
from ._helpers import (
    ScopeError,
    api_handler,
    require_owner,
    resolve_request_scope,
    safe_bad_request,
    safe_error,
    safe_not_found,
    scope_error_response,
)
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.api.agent_media")


@agent_bp.route("/files", methods=["POST"])
@api_handler("上传会话文件")
def agent_upload_file():
    """multipart: file + optional student_id / session_id / purpose"""
    if "file" not in request.files:
        return jsonify(safe_bad_request("请上传 file")), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify(safe_bad_request("文件名为空")), 400

    session_id = request.form.get("session_id") or request.args.get("session_id")
    purpose = request.form.get("purpose") or "chat_attachment"
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)

    if session_id:
        from ..services.session_manager import get_session_manager

        session = get_session_manager().get_session(session_id)
        if not session:
            return jsonify(safe_not_found("会话不存在")), 404
        try:
            require_owner(session.student_id, scope, resource="会话")
        except ScopeError as e:
            return scope_error_response(e)

    # Import leaf module — avoid pulling LangGraph via agent_runtime package side effects
    from ..services.agent_runtime.media.file_store import get_agent_file_service

    try:
        meta = get_agent_file_service().upload(
            file_storage=f,
            student_id=scope.student_id,
            session_id=session_id,
            purpose=purpose,
        )
    except ValueError as e:
        return jsonify(safe_bad_request(str(e))), 400

    return meta


@agent_bp.route("/files/<file_id>", methods=["GET"])
@api_handler("获取会话文件")
def agent_get_file(file_id: str):
    from ..services.agent_runtime.media.file_store import get_agent_file_service

    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    meta = get_agent_file_service().get(file_id)
    if not meta:
        return jsonify(safe_not_found("文件不存在")), 404
    try:
        require_owner(meta.get("student_id"), scope, resource="文件")
    except ScopeError as e:
        return scope_error_response(e)
    return meta


@agent_bp.route("/session/<session_id>/resources", methods=["GET"])
@api_handler("列出会话挂载资源")
def agent_list_resources(session_id: str):
    from ..services.agent_runtime.media import list_resources
    from ..services.session_manager import get_session_manager

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    session = get_session_manager().get_session(session_id)
    if not session:
        return jsonify(safe_not_found("会话不存在")), 404
    try:
        require_owner(session.student_id, scope, resource="会话")
    except ScopeError as e:
        return scope_error_response(e)

    return {"resources": list_resources(session_id, student_id=scope.student_id)}


@agent_bp.route("/session/<session_id>/resources", methods=["POST"])
@api_handler("挂载会话资源")
def agent_mount_resources(session_id: str):
    data = request.get_json() or {}
    resources = data.get("resources")
    if resources is None and data.get("file_id"):
        resources = [{
            "type": data.get("type") or "file",
            "file_id": data["file_id"],
            "mount_path": data.get("mount_path") or data.get("path"),
        }]
    if not resources:
        return jsonify(safe_bad_request("resources 不能为空")), 400

    from ..services.agent_runtime.media import mount_resources
    from ..services.session_manager import get_session_manager

    try:
        scope = resolve_request_scope(session_id=session_id)
        session = get_session_manager().get_session(session_id)
        if not session:
            return jsonify(safe_not_found("会话不存在")), 404
        require_owner(session.student_id, scope, resource="会话")
        out = mount_resources(session_id, resources, student_id=scope.student_id)
    except ScopeError as e:
        return scope_error_response(e)
    except PermissionError as e:
        return jsonify(safe_bad_request(str(e))), 403
    except ValueError as e:
        return jsonify(safe_bad_request(str(e))), 400
    return {"resources": out}


@agent_bp.route("/voice/turns", methods=["POST"])
@api_handler("创建语音回合")
def agent_voice_turn_start():
    data = request.get_json() or {}
    from ..services.agent_runtime.media import get_voice_turn_service
    from ..services.session_manager import get_session_manager

    session_id = data.get("session_id")
    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    if session_id:
        session = get_session_manager().get_session(session_id)
        if not session:
            return jsonify(safe_not_found("会话不存在")), 404
        try:
            require_owner(session.student_id, scope, resource="会话")
        except ScopeError as e:
            return scope_error_response(e)

    return get_voice_turn_service().start(
        session_id=session_id,
        student_id=scope.student_id,
        lang=data.get("lang") or "zh-CN",
        stt_provider=data.get("stt_provider") or "browser_web_speech",
    )


@agent_bp.route("/voice/turns/<turn_id>", methods=["PATCH", "POST"])
@api_handler("更新语音回合")
def agent_voice_turn_update(turn_id: str):
    data = request.get_json() or {}
    from ..services.agent_runtime.media import get_voice_turn_service

    try:
        scope = resolve_request_scope()
        existing = get_voice_turn_service().get(turn_id)
        if not existing:
            return jsonify(safe_not_found("voice turn 不存在")), 404
        require_owner(existing.get("student_id"), scope, resource="语音回合")
        return get_voice_turn_service().update(
            turn_id,
            interim_transcript=data.get("interim_transcript"),
            transcript=data.get("transcript"),
            is_final=bool(data.get("is_final")),
            confidence=data.get("confidence"),
            audio_file_id=data.get("audio_file_id"),
            status=data.get("status"),
            error=data.get("error"),
            stt_provider=data.get("stt_provider"),
        )
    except ScopeError as e:
        return scope_error_response(e)
    except KeyError:
        return jsonify(safe_not_found("voice turn 不存在")), 404


@agent_bp.route("/voice/turns/<turn_id>", methods=["GET"])
@api_handler("获取语音回合")
def agent_voice_turn_get(turn_id: str):
    from ..services.agent_runtime.media import get_voice_turn_service

    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)
    turn = get_voice_turn_service().get(turn_id)
    if not turn:
        return jsonify(safe_not_found("voice turn 不存在")), 404
    try:
        require_owner(turn.get("student_id"), scope, resource="语音回合")
    except ScopeError as e:
        return scope_error_response(e)
    return turn
