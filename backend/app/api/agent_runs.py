"""Owner-bound HTTP façade for durable Agent run state and dispatch status.

The run and outbox commit before Celery transport is attempted. A broker
acknowledgement is not an Agent execution result. Only a completed durable
worker run contains a user-visible final result.
"""

from __future__ import annotations

from flask import jsonify, request

from . import agent_bp
from ._helpers import (
    ScopeError,
    require_owner,
    resolve_request_scope,
    safe_bad_request,
    scope_error_response,
)
from ..services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
from ..services.agent_runtime.agent_run_store import (
    AgentRunConflict,
    AgentRunIdempotencyConflict,
    AgentRunNotFound,
    AgentRunStoreError,
    get_agent_run_repository,
)
from ..services.agent_runtime.agent_task_control import (
    ControlIntent,
    parse_control_message,
    StaleTaskRevision,
)
from ..services.session_manager import get_session_manager
from ..utils.logger import get_logger


logger = get_logger("cpa-agent.api.agent_runs")

_SUBMIT_FIELDS = frozenset({
    "session_id", "message", "interaction_mode", "expert_id", "capability_id",
    "attachment_ids",
    "task_context_version",
})


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "code": code, "error": message}), status


def _run_data(run: dict, *, dispatch_status: str, created: bool | None = None) -> dict:
    data = {
        "run": run,
        "persistence_status": "stored",
        "dispatch_status": dispatch_status,
    }
    if created is not None:
        data["created"] = created
    return data


def _store_error_response(exc: Exception):
    from ..services.agent_runtime.task_context import TaskContextConflict
    if isinstance(exc, StaleTaskRevision):
        return _error("AGENT_RUN_STALE_REVISION", str(exc), 409)
    if isinstance(exc, TaskContextConflict):
        return _error('TASK_CONTEXT_CONFLICT', str(exc), 409)
    if isinstance(exc, AgentRunIdempotencyConflict):
        return _error(
            "AGENT_RUN_IDEMPOTENCY_CONFLICT",
            "同一 Idempotency-Key 已用于不同 Agent run 输入。",
            409,
        )
    if isinstance(exc, AgentRunNotFound):
        return _error("AGENT_RUN_NOT_FOUND", "Agent run 不存在。", 404)
    if isinstance(exc, AgentRunConflict):
        return _error("AGENT_RUN_CONFLICT", str(exc), 409)
    if isinstance(exc, ValueError):
        return jsonify(safe_bad_request(str(exc))), 400
    if isinstance(exc, AgentRunStoreError):
        logger.warning("Agent run persistence unavailable: %s", exc)
    else:
        logger.exception("Agent run persistence request failed")
    return _error(
        "AGENT_RUN_STORAGE_UNAVAILABLE",
        "Agent run 持久状态暂不可用；当前没有启动执行。",
        503,
    )


@agent_bp.route("/runs", methods=["POST"])
def submit_agent_run():
    """Persist run/outbox first, then attempt replayable Celery transport publish."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    unknown = set(data) - _SUBMIT_FIELDS
    if unknown:
        return jsonify(safe_bad_request(
            f"请求体包含不支持字段：{sorted(map(str, unknown))}"
        )), 400

    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 64:
        return jsonify(safe_bad_request("session_id 必须为 1..64 个字符")), 400
    if session_id != session_id.strip() or "\x00" in session_id:
        return jsonify(safe_bad_request("session_id 格式无效")), 400

    # The session id is a grouping reference, not proof of ownership. Validate
    # against authoritative Redis and never let a later worker resolve it from
    # caller input. The current worker uses only the run's durable input snapshot.
    try:
        session = get_session_manager().refresh_session(
            session_id,
            require_authoritative=True,
        )
    except Exception:
        logger.exception("Agent run session validation unavailable session_id=%s", session_id)
        return _error(
            "AGENT_RUN_SESSION_UNAVAILABLE",
            "无法从权威会话存储核验该会话；任务尚未保存或派发。",
            503,
        )
    if session is None:
        return _error("AGENT_RUN_SESSION_NOT_FOUND", "会话不存在或已过期。", 404)
    try:
        require_owner(getattr(session, "student_id", None), scope, resource="会话")
    except ScopeError as exc:
        return scope_error_response(exc)

    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        return jsonify(safe_bad_request("必须提供 Idempotency-Key")), 400

    payload = {key: value for key, value in data.items() if key != "session_id"}
    try:
        repository = get_agent_run_repository()
        principal_auth_epoch = None
        if scope.principal.source == "account":
            # Freeze the account revocation generation into the durable run.
            # A later worker must re-read active status and match this epoch;
            # Redis/session state is not an authorization authority there.
            from ..services.db.mysql_client import get_mysql_client

            account = get_mysql_client().fetch_one(
                "SELECT id, status, auth_epoch FROM user_account WHERE id=:id LIMIT 1",
                {"id": scope.principal_id},
            )
            if (not account or account.get("id") != scope.principal_id
                    or account.get("status") != "active"
                    or type(account.get("auth_epoch")) is not int):
                return _error(
                    "AGENT_RUN_ACCOUNT_UNAVAILABLE",
                    "当前账号状态无法核验；Agent 任务未保存或派发。",
                    503,
                )
            principal_auth_epoch = int(account["auth_epoch"])
        extra = {}
        if payload.get('expert_id') == 'task-coordinator' and payload.get('capability_id') == 'student-task':
            from ..services.agent_runtime.task_context import TaskContextStore
            context_store = TaskContextStore(repository.mysql)
            extra['creation_callback'] = lambda conn, rid: context_store.bind_run(
                conn, session, scope.student_id, payload.get('task_context_version'), rid, payload.get('message',''))
        result = repository.submit(
            student_id=scope.student_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_payload=payload,
            principal_id=scope.principal_id,
            principal_source=scope.principal.source,
            principal_auth_epoch=principal_auth_epoch,
            **extra,
        )
    except Exception as exc:
        return _store_error_response(exc)

    # submit() commits the run, initial outbox row and run.submitted event in
    # one MySQL transaction. Publishing is a separate, replayable operation.
    dispatch_store = AgentRunDispatchStore(repository)
    try:
        from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

        dispatch_agent_run(
            result["run"]["run_id"],
            repository=repository,
            dispatch_store=dispatch_store,
            # Rows accepted by the pre-outbox endpoint are held by migration.
            # An explicit repeat of the same owner/idempotency request may
            # release that hold; Beat replay alone never dispatches old input.
            allow_not_dispatched=not bool(result["created"]),
        )
    except Exception:
        logger.exception("Agent run publish attempt deferred to MySQL replay run_id=%s",
                         result["run"]["run_id"])
    try:
        # The transport attempt may have appended dispatch events after the
        # initial submit transaction; return the current committed cursor.
        response_run = repository.get_for_owner(
            result["run"]["run_id"], student_id=scope.student_id,
        )
        dispatch_status = dispatch_store.get_for_owner(
            result["run"]["run_id"], student_id=scope.student_id,
        )["status"]
    except Exception:
        # The run has already committed. Do not turn a later status-read error
        # into a false rollback; the caller can retry its owner-bound GET.
        response_run = result["run"]
        dispatch_status = "status_unavailable"

    status = 201 if result["created"] else 200
    return jsonify({
        "success": True,
        "data": _run_data(response_run, dispatch_status=dispatch_status,
                          created=bool(result["created"])),
        "message": _submit_message(dispatch_status),
    }), status


def _submit_message(dispatch_status: str) -> str:
    if dispatch_status == "not_dispatched":
        return "该请求是在持久派发队列上线前保存的旧记录，未自动投递；使用原幂等请求再次提交可显式发起派发。"
    if dispatch_status == "publishing":
        return "请求已持久保存，服务正在尝试向 Celery 投递；这不表示 Agent 已开始执行。"
    if dispatch_status == "published":
        return "请求和派发意图已持久保存，Celery broker 已接受消息；这不表示 Agent 已开始执行。"
    if dispatch_status == "claimed":
        return "worker 已领取运输消息；这只确认派发 claim，Agent 执行器尚未确认开始。"
    if dispatch_status == "waiting_executor":
        return "工作消息已由 worker 接收，但 Agent 执行器尚未接线；当前没有模型或工具执行。"
    if dispatch_status == "cancelled":
        return "请求已持久保存，但取消在安全点先于 worker claim 生效；没有开始执行。"
    if dispatch_status == "needs_reconciliation":
        return "请求已持久保存，但此前执行状态不确定，已阻止自动重跑，需先核对权威状态。"
    if dispatch_status == "terminal":
        return "该幂等请求对应的 run 已进入终态；请读取现有结果，不会重新派发。"
    if dispatch_status == "publishing_unverified":
        return "broker 投递确认的持久状态不确定；系统会依靠 MySQL 租约与 worker claim 去重后再核对。"
    if dispatch_status == "status_unavailable":
        return "请求已持久保存，但暂时无法读取最新派发状态；请稍后按 run_id 查询。"
    return "请求和派发意图已持久保存；消息尚未确认进入队列，MySQL 重放器会在服务可用后重试。"


@agent_bp.route("/runs/<run_id>", methods=["GET"])
def get_agent_run(run_id: str):
    """Read an owner-bound status snapshot, excluding the durable input body."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    try:
        repository = get_agent_run_repository()
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        dispatch_status = AgentRunDispatchStore(repository).get_for_owner(
            run_id, student_id=scope.student_id,
        )["status"]
    except Exception as exc:
        return _store_error_response(exc)
    return jsonify({"success": True,
                    "data": _run_data(run, dispatch_status=dispatch_status)})


@agent_bp.route("/runs/<run_id>/events", methods=["GET"])
def get_agent_run_events(run_id: str):
    """Read committed run events strictly after an integer per-run cursor."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    try:
        after_seq = int(request.args.get("after_seq", "0"))
        limit = int(request.args.get("limit", "100"))
    except (TypeError, ValueError):
        return jsonify(safe_bad_request("after_seq/limit 必须是整数")), 400
    if after_seq < 0 or not 1 <= limit <= 200:
        return jsonify(safe_bad_request("after_seq 必须 >= 0，limit 必须在 1..200")), 400
    try:
        result = get_agent_run_repository().read_events(
            run_id,
            student_id=scope.student_id,
            after_seq=after_seq,
            limit=limit,
        )
    except Exception as exc:
        return _store_error_response(exc)
    try:
        result["dispatch_status"] = AgentRunDispatchStore(
            get_agent_run_repository(),
        ).get_for_owner(run_id, student_id=scope.student_id)["status"]
    except Exception as exc:
        return _store_error_response(exc)
    return jsonify({"success": True, "data": result})


@agent_bp.route("/runs/<run_id>/plans/<int:plan_version>", methods=["GET"])
def get_agent_run_plan_version(run_id: str, plan_version: int):
    """Read one immutable, owner-bound plan version referenced by an artifact."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    try:
        plan = get_agent_run_repository().get_plan_version_for_owner(
            run_id, plan_version, student_id=scope.student_id,
        )
    except Exception as exc:
        return _store_error_response(exc)
    return jsonify({"success": True, "data": plan})


@agent_bp.route("/runs/<run_id>/cancel", methods=["POST"])
def request_agent_run_cancel(run_id: str):
    """Persist a cooperative cancel request; do not claim cancellation occurred."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    try:
        repository = get_agent_run_repository()
        run = repository.request_cancel(
            run_id,
            student_id=scope.student_id,
        )
        dispatch_store = AgentRunDispatchStore(repository)
        run = dispatch_store.cancel_if_safe(run_id, student_id=scope.student_id)
        dispatch_status = dispatch_store.get_for_owner(
            run_id, student_id=scope.student_id,
        )["status"]
    except Exception as exc:
        return _store_error_response(exc)
    return jsonify({
        "success": True,
        "data": _run_data(run, dispatch_status=dispatch_status),
        "message": (
            "取消已在安全点生效；此前已提交步骤成果保留。"
            if run["status"] == "cancelled"
            else "已持久记录取消请求；运行侧会在安全点停止后续步骤，未确认的写入不会自动重跑。"
        ),
    })


@agent_bp.route("/runs/<run_id>/control", methods=["POST"])
def control_agent_run(run_id: str):
    """Apply an explicit owner-bound control intent to the current task."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(safe_bad_request("请求体必须是 JSON 对象")), 400
    try:
        repository = get_agent_run_repository()
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        if data.get("intent") in {
            ControlIntent.SUPPLEMENT.value, ControlIntent.REVISE_GOAL.value,
        }:
            # Let the durable idempotency lookup run before validating against
            # the latest revision. A byte-identical retry of a successful
            # revision is valid even though its submitted revision is now old.
            idempotency_key = request.headers.get("Idempotency-Key")
            if idempotency_key is None:
                return jsonify(safe_bad_request("必须提供 Idempotency-Key")), 400
            changed = repository.revise_task_contract(
                run_id, student_id=scope.student_id,
                control=data, idempotency_key=idempotency_key,
            )
            dispatch_store = AgentRunDispatchStore(repository)
            try:
                from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

                dispatch_agent_run(run_id, repository=repository,
                                   dispatch_store=dispatch_store)
            except Exception:
                logger.exception("Agent run revision dispatch deferred run_id=%s", run_id)
            current = repository.get_for_owner(run_id, student_id=scope.student_id)
            dispatch_status = dispatch_store.get_for_owner(
                run_id, student_id=scope.student_id,
            )["status"]
            return jsonify({
                "success": True,
                "data": _run_data(current, dispatch_status=dispatch_status),
                "created_revision": bool(changed.get("created")),
            })
        contract_record = run.get("task_contract")
        if not isinstance(contract_record, dict):
            return _error("AGENT_RUN_CONTRACT_UNAVAILABLE", "任务契约尚未初始化。", 409)
        from ..services.agent_runtime.agent_task_control import task_contract_from_record

        contract = task_contract_from_record(contract_record)
        allowed = {"intent", "task_id", "expected_revision", "patch"}
        if data.get("intent") == ControlIntent.CLARIFY.value:
            allowed.add("reason")
        if set(data) - allowed:
            return jsonify(safe_bad_request("control 请求包含未知字段")), 400
        control = parse_control_message(
            {key: value for key, value in data.items() if key in {
                "intent", "task_id", "expected_revision", "patch",
            }},
            current=contract,
        )
        if control.intent is ControlIntent.PROGRESS_QUERY:
            dispatch_status = AgentRunDispatchStore(repository).get_for_owner(
                run_id, student_id=scope.student_id,
            )["status"]
            return jsonify({"success": True,
                            "data": _run_data(run, dispatch_status=dispatch_status)})
        if control.intent is ControlIntent.NEW_TASK:
            return _error(
                "AGENT_RUN_NEW_TASK_REQUIRED",
                "新任务请使用 POST /runs 创建独立 run；当前任务未修改。",
                409,
            )
        if control.intent is ControlIntent.PAUSE:
            current = repository.request_pause(run_id, student_id=scope.student_id)
            dispatch_store = AgentRunDispatchStore(repository)
            dispatch_status = dispatch_store.get_for_owner(
                run_id, student_id=scope.student_id,
            )["status"]
            return jsonify({"success": True,
                            "data": _run_data(current, dispatch_status=dispatch_status)})
        if control.intent is ControlIntent.CANCEL:
            current = repository.request_cancel(run_id, student_id=scope.student_id)
            dispatch_store = AgentRunDispatchStore(repository)
            current = dispatch_store.cancel_if_safe(run_id, student_id=scope.student_id)
            dispatch_status = dispatch_store.get_for_owner(
                run_id, student_id=scope.student_id,
            )["status"]
            return jsonify({"success": True,
                            "data": _run_data(current, dispatch_status=dispatch_status)})
        if control.intent is ControlIntent.CLARIFY:
            reason = data.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                return jsonify(safe_bad_request("clarify 需要 1..500 字的 reason")), 400
            current = repository.set_waiting_status(
                run_id, student_id=scope.student_id, status="waiting_input",
                reason=reason,
            )
            dispatch_status = AgentRunDispatchStore(repository).get_for_owner(
                run_id, student_id=scope.student_id,
            )["status"]
            return jsonify({"success": True,
                            "data": _run_data(current, dispatch_status=dispatch_status)})
        return _error("AGENT_RUN_CONTROL_UNSUPPORTED", "该任务控制意图暂不支持。", 400)
    except Exception as exc:
        return _store_error_response(exc)


def _assert_current_task_context(repository, run: dict, *, student_id: str) -> None:
    """Bind free-text control to the session's current/last Agent task id."""
    from ..services.agent_runtime.task_context import TaskContextStore

    context = TaskContextStore(repository.mysql).read(run["session_id"], student_id)
    current = context.get("active_run_id") or context.get("last_run_id")
    if current != run["run_id"]:
        raise AgentRunConflict("follow-up does not target this session's current task")


def _project_followup_model_usage(ledger: Any) -> dict:
    """Project provider usage into the bounded durable task-usage shape."""
    if not isinstance(ledger, dict):
        return {"status": "unverified", "reason": "usage_ledger_unavailable",
                "call_count": None, "calls": []}
    raw_calls = ledger.get("calls")
    raw_calls = raw_calls if isinstance(raw_calls, list) else []
    stop = ledger.get("stop_reason")
    budget_stop = ledger.get("budget_stop")
    allowed_stops = {
        "turn_token_cap_exceeded", "model_call_cap_exceeded", "tool_call_cap_exceeded",
    }
    pre_dispatch_stop = (
        not raw_calls and isinstance(budget_stop, dict)
        and budget_stop.get("reason") in allowed_stops and budget_stop.get("reason") == stop
    )
    calls = []
    valid = bool(raw_calls) or pre_dispatch_stop
    for item in raw_calls[:32]:
        usage = item.get("usage") if isinstance(item, dict) else None
        if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            valid = False
            calls.append({"usage": None})
        else:
            calls.append({"usage": {
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
            }})
    if len(raw_calls) > 32:
        valid = False
    reason = stop if isinstance(stop, str) and stop else "provider_usage_observed"
    if pre_dispatch_stop:
        valid = True
    return {
        "status": "observed" if valid else "unverified",
        "reason": reason if valid else (reason or "provider_usage_unverified"),
        "call_count": len(calls),
        "calls": calls,
    }


def _followup_response(repository, run_id: str, student_id: str, operation: dict,
                       *, status: int = 200):
    latest = repository.get_for_owner(run_id, student_id=student_id)
    dispatch_status = AgentRunDispatchStore(repository).get_for_owner(
        run_id, student_id=student_id,
    )["status"]
    return jsonify({"success": True, "data": {
        "operation_status": operation["status"],
        "classification": operation.get("classification"),
        "result": operation.get("result"),
        "run": _run_data(latest, dispatch_status=dispatch_status),
    }}), status


def _resume_followup_paused_run(repository, run_id: str, student_id: str,
                                operation: dict) -> Optional[dict]:
    latest = repository.get_for_owner(run_id, student_id=student_id)
    if not operation.get("auto_paused") or latest.get("status") != "paused":
        return latest
    latest = repository.resume_task(
        run_id, student_id=student_id,
        expected_revision=int(operation["contract_revision"]),
    )
    from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

    dispatch_agent_run(
        run_id, repository=repository, dispatch_store=AgentRunDispatchStore(repository),
    )
    return latest


def _apply_task_followup(repository, run_id: str, student_id: str, idempotency_key: str,
                         operation: dict):
    """Apply one persisted classifier result through the existing run state machine."""
    from ..services.agent_runtime.agent_task_followup import FollowupIntent

    if operation.get("status") == "completed":
        return operation
    classification = operation.get("classification")
    if not isinstance(classification, dict):
        raise AgentRunConflict("follow-up has no persisted classification")
    try:
        intent = FollowupIntent(classification.get("intent"))
    except ValueError as exc:
        raise AgentRunStoreError("persisted follow-up intent is invalid") from exc
    request_record = operation.get("request") or {}
    expected_revision = int(operation["contract_revision"])
    current = repository.get_for_owner(run_id, student_id=student_id)
    if current.get("current_contract_revision") != expected_revision:
        # A process can stop after the revision CAS commits but before this
        # follow-up operation records its result. Replay the same idempotent
        # control request to recover that committed revision; never infer that
        # a different newer revision belongs to this follow-up.
        if intent in {FollowupIntent.SUPPLEMENT, FollowupIntent.REVISE_GOAL}:
            control = {
                "intent": intent.value, "task_id": run_id,
                "expected_revision": expected_revision,
                "patch": classification.get("patch"),
            }
            try:
                revised = repository.revise_task_contract(
                    run_id, student_id=student_id, control=control,
                    idempotency_key=idempotency_key,
                )
            except StaleTaskRevision:
                revised = None
            latest = repository.get_for_owner(run_id, student_id=student_id)
            if (revised is not None
                    and revised["contract"]["revision"]
                    == latest.get("current_contract_revision")):
                from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

                dispatch_agent_run(
                    run_id, repository=repository,
                    dispatch_store=AgentRunDispatchStore(repository),
                )
                result = {"action": "contract_revised", "intent": intent.value}
            else:
                result = {"action": "stale_revision", "intent": intent.value}
        else:
            result = {"action": "stale_revision", "intent": intent.value}
        done = repository.complete_task_followup_operation(
            run_id, student_id=student_id, idempotency_key=idempotency_key, result=result,
        )
        return done["operation"]

    if intent in {FollowupIntent.SUPPLEMENT, FollowupIntent.REVISE_GOAL}:
        control = {
            "intent": intent.value, "task_id": run_id,
            "expected_revision": expected_revision,
            "patch": classification.get("patch"),
        }
        repository.revise_task_contract(
            run_id, student_id=student_id, control=control,
            idempotency_key=idempotency_key,
        )
        from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

        dispatch_agent_run(
            run_id, repository=repository,
            dispatch_store=AgentRunDispatchStore(repository),
        )
        result = {"action": "contract_revised", "intent": intent.value}
    elif intent is FollowupIntent.PROGRESS_QUERY:
        _resume_followup_paused_run(repository, run_id, student_id, operation)
        result = {"action": "progress_query", "intent": intent.value}
    elif intent is FollowupIntent.PAUSE:
        repository.request_pause(run_id, student_id=student_id)
        result = {"action": "pause_requested", "intent": intent.value}
    elif intent is FollowupIntent.CANCEL:
        repository.request_cancel(run_id, student_id=student_id)
        AgentRunDispatchStore(repository).cancel_if_safe(run_id, student_id=student_id)
        result = {"action": "cancel_requested", "intent": intent.value}
    elif intent is FollowupIntent.CLARIFY:
        latest = repository.get_for_owner(run_id, student_id=student_id)
        question = str(classification.get("clarification_question") or "").strip()
        if latest.get("status") not in {"completed", "failed", "cancelled", "partial"}:
            repository.set_waiting_status(
                run_id, student_id=student_id, status="waiting_input",
                reason=question or "Please clarify this follow-up.",
            )
        result = {"action": "ask_clarification", "intent": intent.value,
                  "question": question or "你是要补充当前任务、另开新任务，还是查询进度？"}
    elif intent is FollowupIntent.NEW_TASK:
        _resume_followup_paused_run(repository, run_id, student_id, operation)
        result = {"action": "create_new_task", "intent": intent.value,
                  "message": str(request_record.get("message") or "")}
    elif intent is FollowupIntent.SAVE_REQUEST:
        latest = repository.get_for_owner(run_id, student_id=student_id)
        plan = latest.get("current_plan") or {}
        version = int(plan.get("plan_version") or 0)
        requested_version = classification.get("requested_plan_version")
        ready = bool(
            latest.get("status") == "completed" and version > 0
            and version == latest.get("current_plan_version")
            and (plan.get("metadata") or {}).get("task_contract_revision") == expected_revision
            and plan.get("plan_sha256")
        )
        if not ready:
            _resume_followup_paused_run(repository, run_id, student_id, operation)
            result = {"action": "save_not_ready", "intent": intent.value}
        elif requested_version is not None and requested_version != version:
            result = {"action": "ask_clarification", "intent": "clarify",
                      "question": f"当前可保存的是计划版本 {version}。要保存这个版本吗？"}
        else:
            result = {"action": "confirm_save", "intent": intent.value,
                      "contract_revision": expected_revision, "plan_version": version,
                      "plan_sha256": str(plan["plan_sha256"])}
    else:
        raise AgentRunStoreError("persisted follow-up intent is unsupported")

    completed = repository.complete_task_followup_operation(
        run_id, student_id=student_id, idempotency_key=idempotency_key, result=result,
    )
    return completed["operation"]


@agent_bp.route("/runs/<run_id>/followup", methods=["POST"])
def followup_agent_run(run_id: str):
    """Classify free text for the current owner-bound task, then route safely."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    data = request.get_json(silent=True)
    if (not isinstance(data, dict)
            or set(data) != {"task_id", "expected_revision", "message"}):
        return jsonify(safe_bad_request(
            "follow-up JSON must contain task_id, expected_revision, and message",
        )), 400
    if data.get("task_id") != run_id:
        return _error("AGENT_RUN_TASK_MISMATCH", "task_id must match the URL run_id", 409)
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        return jsonify(safe_bad_request("Idempotency-Key is required")), 400
    try:
        repository = get_agent_run_repository()
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        _assert_current_task_context(repository, run, student_id=scope.student_id)
        contract_record = run.get("task_contract")
        if not isinstance(contract_record, dict):
            return _error("AGENT_RUN_CONTRACT_UNAVAILABLE", "task contract is not initialized", 409)
        from ..services.agent_runtime.agent_task_control import task_contract_from_record
        from ..services.agent_runtime.agent_task_followup import (
            FollowupIntent, FollowupClassifierError, TaskFollowupClassification,
            classify_task_followup,
            quick_control_intent,
        )

        contract = task_contract_from_record(contract_record)
        expected_revision = data.get("expected_revision")
        message = data.get("message")
        quick_intent = quick_control_intent(message)
        deterministic = None
        if quick_intent is not None:
            deterministic = TaskFollowupClassification(
                intent=quick_intent, confidence=1.0, patch=None,
                clarification_question="", requested_plan_version=None,
                reason="explicit_short_control_command",
            ).to_record()
        begun = repository.begin_task_followup_operation(
            run_id, student_id=scope.student_id,
            expected_revision=expected_revision, message=message,
            idempotency_key=idempotency_key,
            model_required=deterministic is None, classification=deterministic,
        )
        operation = begun["operation"]
        if operation["status"] == "completed":
            return _followup_response(repository, run_id, scope.student_id, operation)
        if operation["status"] == "needs_reconciliation":
            return _followup_response(repository, run_id, scope.student_id, operation, status=503)
        if operation["status"] == "waiting_safe_point":
            if begun["run"].get("status") == "pause_requested":
                return _followup_response(
                    repository, run_id, scope.student_id, operation, status=202,
                )
            reserved = repository.reserve_task_followup_model_call(
                run_id, student_id=scope.student_id,
                expected_revision=expected_revision, idempotency_key=idempotency_key,
            )
            operation = reserved["operation"]
            if operation["status"] == "waiting_safe_point":
                return _followup_response(
                    repository, run_id, scope.student_id, operation, status=202,
                )
            if operation["status"] == "completed":
                return _followup_response(repository, run_id, scope.student_id, operation)
            if operation["status"] == "needs_reconciliation":
                return _followup_response(repository, run_id, scope.student_id,
                                          operation, status=503)
            if operation["status"] == "classifying":
                if not reserved.get("classification_claimed"):
                    # Another concurrent retry already owns the reserved model call.
                    # Do not invoke the classifier twice for one durable reservation.
                    return _followup_response(
                        repository, run_id, scope.student_id, operation, status=202,
                    )
                usage = reserved["usage"]
                turn_token_cap = reserved["turn_token_cap"]
                from ..services.agent_runtime.budget import (
                    capture_usage_ledger,
                )
                ledger = None
                try:
                    with capture_usage_ledger(
                        "agent_task_followup", turn_token_cap=turn_token_cap,
                        session_id=str(run.get("session_id") or "") or None,
                        student_id=scope.student_id,
                        model_call_cap=contract.budget.max_model_calls,
                        tool_call_cap=contract.budget.max_tool_calls,
                        model_calls_used=int(usage["model_calls"]),
                        tool_calls_used=int(usage["tool_calls"]),
                    ) as active_ledger:
                        ledger = active_ledger
                        try:
                            classification = classify_task_followup(
                                message, contract=contract, run=run,
                            )
                        except FollowupClassifierError:
                            classification = TaskFollowupClassification(
                                intent=FollowupIntent.CLARIFY, confidence=0.0,
                                patch=None,
                                clarification_question=(
                                    "我没能可靠识别这条跟进。你是要补充当前任务、另开新任务，"
                                    "查询进度，还是请求保存当前版本？"
                                ),
                                requested_plan_version=None,
                                reason="classifier_output_invalid",
                            )
                except Exception as exc:
                    model_usage = _project_followup_model_usage(ledger)
                    if (isinstance(ledger, dict) and not ledger.get("calls")
                            and isinstance(ledger.get("budget_stop"), dict)):
                        blocked = repository.block_task_followup_operation(
                            run_id, student_id=scope.student_id,
                            idempotency_key=idempotency_key,
                            result={"action": "classification_unavailable",
                                    "reason": str(ledger.get("stop_reason") or "budget_stop")},
                        )
                        return _followup_response(repository, run_id, scope.student_id,
                                                  blocked["operation"])
                    repository.mark_task_followup_uncertain(
                        run_id, student_id=scope.student_id,
                        idempotency_key=idempotency_key, model_usage=model_usage,
                    )
                    logger.warning("Agent follow-up classifier outcome uncertain run_id=%s error=%s",
                                   run_id, type(exc).__name__)
                    current = repository.get_task_followup_operation(
                        run_id, student_id=scope.student_id,
                        idempotency_key=idempotency_key,
                    )["operation"]
                    return _followup_response(repository, run_id, scope.student_id,
                                              current, status=503)
                model_usage = _project_followup_model_usage(ledger)
                if model_usage.get("status") != "observed" or not model_usage.get("call_count"):
                    repository.mark_task_followup_uncertain(
                        run_id, student_id=scope.student_id,
                        idempotency_key=idempotency_key, model_usage=model_usage,
                    )
                    current = repository.get_task_followup_operation(
                        run_id, student_id=scope.student_id,
                        idempotency_key=idempotency_key,
                    )["operation"]
                    return _followup_response(repository, run_id, scope.student_id,
                                              current, status=503)
                stored = repository.store_task_followup_classification(
                    run_id, student_id=scope.student_id,
                    idempotency_key=idempotency_key,
                    classification=classification.to_record(), model_usage=model_usage,
                )
                operation = stored["operation"]
        if operation["status"] == "classifying":
            return _followup_response(
                repository, run_id, scope.student_id, operation, status=202,
            )
        if operation["status"] == "classified":
            operation = _apply_task_followup(
                repository, run_id, scope.student_id, idempotency_key, operation,
            )
        return _followup_response(repository, run_id, scope.student_id, operation)
    except Exception as exc:
        return _store_error_response(exc)


@agent_bp.route("/runs/<run_id>/followup", methods=["GET"])
def get_agent_run_followup(run_id: str):
    """Read the durable state for one idempotent natural-language follow-up."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        return jsonify(safe_bad_request("Idempotency-Key is required")), 400
    try:
        repository = get_agent_run_repository()
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        _assert_current_task_context(repository, run, student_id=scope.student_id)
        operation = repository.get_task_followup_operation(
            run_id, student_id=scope.student_id, idempotency_key=idempotency_key,
        )["operation"]
        return _followup_response(repository, run_id, scope.student_id, operation,
                                  status=202 if operation["status"] in {
                                      "waiting_safe_point", "classifying", "classified",
                                  } else 200)
    except Exception as exc:
        return _store_error_response(exc)


@agent_bp.route("/runs/<run_id>/resume", methods=["POST"])
def resume_agent_run(run_id: str):
    """Resume a paused task only against the contract revision the user saw."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"expected_revision"}:
        return jsonify(safe_bad_request(
            "请求体必须且只能包含 expected_revision",
        )), 400
    try:
        repository = get_agent_run_repository()
        run = repository.resume_task(
            run_id, student_id=scope.student_id,
            expected_revision=data["expected_revision"],
        )
        dispatch_store = AgentRunDispatchStore(repository)
        try:
            from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run

            dispatch_agent_run(run_id, repository=repository,
                               dispatch_store=dispatch_store)
        except Exception:
            logger.exception("Agent run resume dispatch deferred run_id=%s", run_id)
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        dispatch_status = dispatch_store.get_for_owner(
            run_id, student_id=scope.student_id,
        )["status"]
        return jsonify({"success": True,
                        "data": _run_data(run, dispatch_status=dispatch_status)})
    except Exception as exc:
        return _store_error_response(exc)


def _artifact_matches_task(artifact, *, run_id: str, confirmation: dict,
                           expected_preview: dict) -> bool:
    if not isinstance(artifact, dict):
        return False
    preview = artifact.get("preview")
    return bool(
        artifact.get("artifact_id") == confirmation["artifact_id"]
        and artifact.get("kind") == "action_plan"
        and artifact.get("status") == "completed"
        and artifact.get("resource_type") == "agent_run_plan"
        and artifact.get("resource_id") == f"{run_id}:plan:{confirmation['plan_version']}"
        and artifact.get("version") == confirmation["plan_version"]
        and preview == expected_preview
    )


@agent_bp.route("/runs/<run_id>/save", methods=["POST"])
def save_agent_run_artifact(run_id: str):
    """Save and owner-read back the exact version the user confirmed."""
    try:
        scope = resolve_request_scope()
    except ScopeError as exc:
        return scope_error_response(exc)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"contract_revision", "plan_version"}:
        return jsonify(safe_bad_request(
            "请求体必须且只能包含 contract_revision 与 plan_version",
        )), 400
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        return jsonify(safe_bad_request("必须提供 Idempotency-Key")), 400
    try:
        repository = get_agent_run_repository()
        confirmation = repository.confirm_artifact_save(
            run_id, student_id=scope.student_id,
            contract_revision=data["contract_revision"],
            plan_version=data["plan_version"],
            idempotency_key=idempotency_key,
        )
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        plan = repository.get_plan_version_for_owner(
            run_id, confirmation["plan_version"], student_id=scope.student_id,
        )
        if plan.get("plan_sha256") != confirmation["plan_sha256"]:
            raise AgentRunConflict("confirmed plan digest differs from the owner-read version")
        from ..services.agent_runtime.artifact_committer import get_artifact_committer

        committer = get_artifact_committer()
        artifact_id = confirmation["artifact_id"]
        final = run.get("final_result") or {}
        reply = str(final.get("reply") or "")
        expected_preview = {
            "type": "plan_preview",
            "steps": len(plan.get("steps") or []),
            "completed": sum(
                1 for item in (plan.get("steps") or [])
                if item.get("status") == "completed"
            ),
        }
        artifact = committer.get_for_owner(artifact_id, student_id=scope.student_id)
        expected_resource_id = f"{run_id}:plan:{confirmation['plan_version']}"
        if artifact is not None and not _artifact_matches_task(
            artifact, run_id=run_id, confirmation=confirmation,
            expected_preview=expected_preview,
        ):
            raise AgentRunConflict("artifact id is already bound to different content")
        if artifact is None:
            candidate = {
                "artifact_id": artifact_id,
                "kind": "action_plan",
                "status": "completed",
                "verified": True,
                "title": str(plan.get("goal_summary") or "Agent 任务成果")[:120],
                "summary": reply[:500] or str(plan.get("goal_summary") or "")[:500],
                "preview": expected_preview,
                "resource_type": "agent_run_plan",
                "resource_id": expected_resource_id,
                "version": confirmation["plan_version"],
                "actions": [],
            }
            committer.commit_candidates(
                [candidate], student_id=scope.student_id,
                session_id=run.get("session_id"), turn_id=None,
            )
            # commit_candidates intentionally absorbs persistence errors. Only
            # a fresh owner-bound read can establish that the write happened.
            artifact = committer.get_for_owner(artifact_id, student_id=scope.student_id)
        if not _artifact_matches_task(
            artifact, run_id=run_id, confirmation=confirmation,
            expected_preview=expected_preview,
        ):
            return _error(
                "AGENT_RUN_ARTIFACT_UNVERIFIED",
                "成果保存结果暂不可核验；请按相同 Idempotency-Key 查询后再重试。",
                503,
            )
        confirmation = repository.mark_artifact_save_verified(
            run_id, student_id=scope.student_id,
            confirmation_id=confirmation["confirmation_id"],
            artifact_id=artifact_id,
        )
        run = repository.get_for_owner(run_id, student_id=scope.student_id)
        dispatch_status = AgentRunDispatchStore(repository).get_for_owner(
            run_id, student_id=scope.student_id,
        )["status"]
        return jsonify({
            "success": True,
            "data": {
                "run": _run_data(run, dispatch_status=dispatch_status),
                "confirmation": confirmation,
                "artifact": artifact,
            },
            "message": "已保存并按当前账号回读核验指定的成果版本。",
        })
    except Exception as exc:
        return _store_error_response(exc)
