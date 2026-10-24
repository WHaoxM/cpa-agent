"""Durable, one-phase-at-a-time executor for the owner-bound Agent run outbox.

MySQL run/plan/step rows are the business checkpoint. A small LangGraph is
used as the restartable phase driver; its state contains only run identity,
and it must use the MySQL saver (never MemorySaver). The first connected
executor intentionally accepts read-only QA plans only. Existing business
write roles need idempotent/reconciliation adapters before they may run here.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from typing import Any, Dict, Iterator, Optional, TypedDict

from .agent_run_dispatch import AgentRunDispatchStore
from .agent_run_store import AgentRunConflict, AgentRunRepository
from .agent_task_control import TaskContract
from .blackboard import Blackboard
from .run_failure import failure_reply


_MAX_ADVANCES = 10
_SUPPORTED_ROLES = frozenset({"qa"})
_TASK_BUDGET_STOP_REASONS = frozenset({
    "turn_token_cap_exceeded", "model_call_cap_exceeded", "tool_call_cap_exceeded",
})
# Do not inherit the broad legacy QA allowlist wholesale: semantic vectors are
# not owner-partitioned, normalization may register OOV entities, and attachment
# reads are not yet bound to durable run input references.
_RUN_QA_TOOL_ALLOWLIST = frozenset({"career_data_read", "graph_query", "db_query"})
_UNSUPPORTED_CAPABILITY_REPLY = (
    "本轮任务尚未启动：请求中的专家/能力包选择不在当前服务端白名单内，"
    "或与当前交互模式不兼容。请切换到已支持的能力包或普通 Agent 后重试。"
)
_UNSUPPORTED_ATTACHMENT_REPLY = (
    "本轮任务尚未启动：提交的附件引用还没有接入持久执行器的 owner 校验读取路径。"
    "请先在工作台中完成附件读取，或暂时移除附件后重试。"
)
_WRITE_ROLE_REPLY = (
    "任务计划已保存，但当前持久执行器只开放已验证的只读问答步骤。"
    "含画像、匹配、报告或计划写入的步骤尚未启动；请待写入幂等和核对流程接通后重试。"
)


class _RunGraphState(TypedDict, total=False):
    run_id: str
    student_id: str
    done: bool


class AgentRunExecutor:
    """Advance one durable phase per LangGraph node from authoritative MySQL."""

    def __init__(
        self,
        repository: AgentRunRepository,
        dispatch_store: AgentRunDispatchStore,
    ):
        self.repository = repository
        self.dispatch_store = dispatch_store

    def execute_claim(self, run_id: str, *, claim_token: str, student_id: str) -> Dict[str, Any]:
        """Run a claimed delivery to a durable terminal/safe state."""
        claim_binding = self.dispatch_store.get_claim_binding(run_id, claim_token)
        if not claim_binding:
            raise AgentRunConflict("worker claim is stale or no longer owned")
        claimed_student_id = str(claim_binding.get("student_id") or "")
        if not claimed_student_id or claimed_student_id != student_id:
            raise AgentRunConflict("worker claim owner binding mismatch")
        binding = self.repository.get_auth_binding_for_owner(
            run_id, student_id=claimed_student_id,
        )
        if any(binding.get(key) != claim_binding.get(key) for key in (
            "principal_id", "principal_source", "principal_auth_epoch",
        )):
            raise AgentRunConflict("worker account binding changed during claim")
        if not self._has_current_account(binding):
            return self.repository.fail_run(
                run_id,
                student_id=claimed_student_id,
                reply=("任务没有启动：提交任务时绑定的账号已停用或授权版本已变化。"
                       "请重新登录并重新提交。"),
                error_code="account_epoch_invalid",
                claim_token=claim_token,
            )

        from ...api._helpers import trusted_agent_worker_scope

        with trusted_agent_worker_scope(
            run_id=run_id,
            claim_token=claim_token,
            load_claim_binding=self.dispatch_store.get_claim_binding,
            scope_is_current=lambda rid, token: self._claim_and_account_current(rid, token),
        ):
            current_run = self.repository.get_for_owner(
                run_id, student_id=claimed_student_id,
            )
            current_contract = self._task_contract(current_run)
            with self._durable_checkpointer() as checkpointer:
                graph = self._build_graph(checkpointer, run_id, student_id, claim_token)
                config = {"configurable": {"thread_id": _checkpoint_thread_id(
                    run_id,
                    student_id,
                    contract_revision=(current_contract.revision if current_contract else None),
                )}}
                state = graph.get_state(config)
                if state.values:
                    if (state.values.get("run_id") != run_id
                            or state.values.get("student_id") != student_id):
                        raise AgentRunConflict("LangGraph checkpoint owner/run binding mismatch")
                    run = self.repository.get_for_owner(run_id, student_id=student_id)
                    if run["status"] in {
                        "completed", "failed", "cancelled", "partial",
                        "needs_reconciliation", "paused", "waiting_input",
                        "waiting_authorization",
                    }:
                        return run
                    if not state.next:
                        raise AgentRunConflict("LangGraph checkpoint ended before MySQL run terminal state")
                    result = graph.invoke(None, config=config)
                else:
                    result = graph.invoke(
                        {"run_id": run_id, "student_id": student_id, "done": False},
                        config=config,
                    )
                final = self.repository.get_for_owner(run_id, student_id=student_id)
                if final["status"] not in {
                    "completed", "failed", "cancelled", "partial", "needs_reconciliation",
                    "paused", "waiting_input", "waiting_authorization", "queued",
                }:
                    raise RuntimeError(
                        f"Agent run executor returned before durable settlement: {final['status']}"
                    )
                return final

    def _build_graph(self, checkpointer, run_id: str, student_id: str, claim_token: str):
        from langgraph.graph import END, START, StateGraph

        advances = {"count": 0}

        def advance(state: _RunGraphState) -> Dict[str, Any]:
            advances["count"] += 1
            if advances["count"] > _MAX_ADVANCES:
                raise RuntimeError("Agent run exceeded the bounded phase count")
            done = self._advance_once(run_id, student_id, claim_token)
            return {"run_id": run_id, "student_id": student_id, "done": done}

        def route(state: _RunGraphState) -> str:
            return END if state.get("done") is True else "advance"

        builder = StateGraph(_RunGraphState)
        builder.add_node("advance", advance)
        builder.add_edge(START, "advance")
        builder.add_conditional_edges("advance", route, {"advance": "advance", END: END})
        return builder.compile(checkpointer=checkpointer)

    @staticmethod
    def _task_contract(run: Dict[str, Any]) -> Optional[TaskContract]:
        record = run.get("task_contract")
        if not isinstance(record, dict):
            return None
        from .agent_task_control import task_contract_from_record

        return task_contract_from_record(record)

    @staticmethod
    def _budget_status(contract: TaskContract, usage: Dict[str, Any]) -> Dict[str, Any]:
        if not usage.get("verified"):
            return {"reason": "task_usage_unverified", "remaining_tokens": 0}
        budget = contract.budget
        if (usage.get("model_calls", 0) >= budget.max_model_calls
                or usage.get("tool_calls", 0) > budget.max_tool_calls
                or usage.get("input_tokens", 0) >= budget.max_input_tokens
                or usage.get("output_tokens", 0) >= budget.max_output_tokens):
            return {"reason": "task_budget_exhausted", "remaining_tokens": 0}
        remaining_tokens = min(
            budget.max_input_tokens - usage.get("input_tokens", 0)
            + budget.max_output_tokens - usage.get("output_tokens", 0),
            budget.max_input_tokens + budget.max_output_tokens
            - usage.get("total_tokens", 0),
        )
        if remaining_tokens <= 0:
            return {"reason": "task_budget_exhausted", "remaining_tokens": 0}
        return {"reason": None, "remaining_tokens": remaining_tokens}

    @staticmethod
    def _task_usage_ledger_options(
        contract: Optional[TaskContract], usage: Optional[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Seed a fresh phase ledger from durable totals and immutable task caps."""
        if contract is None:
            return {}
        if not isinstance(usage, dict) or usage.get("verified") is not True:
            raise RuntimeError("task_usage_unverified")
        model_calls = usage.get("model_calls")
        tool_calls = usage.get("tool_calls")
        if (type(model_calls) is not int or model_calls < 0
                or type(tool_calls) is not int or tool_calls < 0):
            raise RuntimeError("task_usage_unverified")
        return {
            "model_call_cap": contract.budget.max_model_calls,
            "tool_call_cap": contract.budget.max_tool_calls,
            "model_calls_used": model_calls,
            "tool_calls_used": tool_calls,
        }

    def _settle_partial_for_budget(
        self, run_id: str, student_id: str, claim_token: str, run: Dict[str, Any],
        contract: TaskContract, usage: Dict[str, Any], reason: str,
    ) -> bool:
        plan = run.get("current_plan") or {}
        steps = plan.get("steps") if isinstance(plan, dict) else []
        completed = [step.get("goal", "") for step in steps or []
                     if step.get("status") == "completed"]
        pending = [step.get("goal", "") for step in steps or []
                   if step.get("status") not in {"completed", "superseded"}]
        if reason == "task_usage_unverified":
            lead = "已停止继续执行：前序模型或工具用量无法核验，不能安全判断剩余预算。"
        else:
            lead = "任务达到已配置的执行预算，以下为当前可核验的部分结果。"
        lines = [lead, f"目标：{contract.goal}"]
        if completed:
            lines.append("已完成步骤：" + "；".join(completed[:12]))
        if pending:
            lines.append("未完成步骤：" + "；".join(pending[:12]))
        lines.append(
            ("已核验用量：" if usage.get("verified") else "至少已核验用量（总量不完整）：")
            + "模型调用 {model_calls}，工具调用 {tool_calls}，输入 {input_tokens}，"
            "输出 {output_tokens}，合计 {total_tokens} token。".format(**{
                key: int(usage.get(key, 0) or 0) for key in (
                    "model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens",
                )
            })
        )
        self.repository.mark_partial(
            run_id, student_id=student_id,
            final_result={"reply": "\n".join(lines), "format": "text"},
            reason=reason, claim_token=claim_token,
        )
        return True

    def _advance_once(self, run_id: str, student_id: str, claim_token: str) -> bool:
        self._renew_or_stop(run_id, claim_token)
        if not self.dispatch_store.claim_is_current(run_id, claim_token):
            raise AgentRunConflict("worker claim is no longer current")
        binding = self.dispatch_store.get_claim_binding(run_id, claim_token)
        if not binding:
            raise AgentRunConflict("worker claim owner binding is unavailable")
        if not self._has_current_account(binding):
            self.repository.fail_run(
                run_id,
                student_id=student_id,
                reply=("任务已停止：提交任务时绑定的账号已停用或授权版本已变化。"
                       "请重新登录并重新提交。"),
                error_code="account_epoch_invalid",
                claim_token=claim_token,
            )
            return True
        run = self.repository.get_for_owner(run_id, student_id=student_id)
        if run["status"] in {
            "completed", "failed", "cancelled", "partial", "needs_reconciliation",
            "paused", "waiting_input", "waiting_authorization",
        }:
            return True
        if run["status"] == "pause_requested":
            self.repository.mark_paused_at_safe_point(
                run_id, student_id=student_id, claim_token=claim_token,
            )
            return True
        if run.get("cancel_requested"):
            self.repository.mark_cancelled_at_safe_point(
                run_id, student_id=student_id, claim_token=claim_token,
            )
            return True

        ensure_contract = getattr(self.repository, "ensure_initial_task_contract", None)
        contract = (
            ensure_contract(run_id, student_id=student_id, claim_token=claim_token)
            if callable(ensure_contract) else None
        )
        run = self.repository.get_for_owner(run_id, student_id=student_id)
        contract = self._task_contract(run)
        if (run["status"] == "finalizing"
                and int(run.get("current_plan_version") or 0) == 0
                and run.get("final_result_checkpoint")):
            if not self._claim_and_account_current(run_id, claim_token):
                raise AgentRunConflict("worker claim or account auth epoch changed before direct answer settlement")
            self.repository.complete_direct_run(
                run_id, student_id=student_id, final_result=run["final_result"],
                expected_contract_revision=(contract.revision if contract else None),
                claim_token=claim_token,
            )
            return True
        if contract is not None:
            usage = self.repository.task_usage_for_owner(run_id, student_id=student_id)
            budget_status = self._budget_status(contract, usage)
            if budget_status["reason"]:
                return self._settle_partial_for_budget(
                    run_id, student_id, claim_token, run, contract, usage,
                    budget_status["reason"],
                )

        snapshot = self.repository.get_input_snapshot_for_owner(run_id, student_id=student_id)
        from .expert_capabilities import (
            CapabilitySelectionError,
            resolve_explicit_capability,
        )

        try:
            capability = resolve_explicit_capability(snapshot)
        except CapabilitySelectionError:
            self.repository.fail_run(
                run_id, student_id=student_id, reply=_UNSUPPORTED_CAPABILITY_REPLY,
                error_code="capability_selection_invalid", claim_token=claim_token,
            )
            return True
        from .resource_access_policy import (
            explicitly_forbids_personal_reads,
            no_read_clarification,
            validate_no_read_plan,
        )

        task_constraints = contract.constraints if contract is not None else ()
        no_read_safe_mode = explicitly_forbids_personal_reads(
            snapshot.get("message"), task_constraints,
        )
        if no_read_safe_mode:
            clarification = no_read_clarification(
                snapshot.get("message"), task_constraints,
            )
            if clarification:
                self.repository.set_waiting_status(
                    run_id, student_id=student_id, status="waiting_input",
                    reason=clarification, claim_token=claim_token,
                )
                return True
        if snapshot.get("attachment_ids") and not (capability is not None and capability.execution_pipeline == 'student_task_v1'):
            self.repository.fail_run(
                run_id,
                student_id=student_id,
                reply=(
                    _capability_switch_reply()
                    if capability else _UNSUPPORTED_ATTACHMENT_REPLY
                ),
                error_code=(
                    "capability_attachment_unsupported"
                    if capability else "attachment_reader_unavailable"
                ),
                claim_token=claim_token,
            )
            return True

        plan = run.get("current_plan") or {}
        plan_contract_revision = (plan.get("metadata") or {}).get("task_contract_revision")
        if (run["current_plan_version"] == 0
                or (contract is not None and plan_contract_revision != contract.revision)):
            return self._plan(
                run_id, student_id, claim_token, snapshot, run,
                capability=capability, contract=contract,
            )

        metadata = plan.get("metadata") or {}
        release = metadata.get("runtime_release") if isinstance(metadata, dict) else None
        if not isinstance(release, dict):
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply="本轮任务没有可验证的 Prompt／Skill 发布快照，已停止执行。请重新提交任务。",
                error_code="runtime_release_missing", claim_token=claim_token,
            )
            return True
        capability_manifest = metadata.get("capability_binding") if isinstance(metadata, dict) else None
        if capability_manifest != (capability.manifest() if capability else None):
            self.repository.fail_run(
                run_id,
                student_id=student_id,
                reply=(
                    _capability_switch_reply()
                    if capability else
                    "本轮任务的能力包绑定与提交时输入不一致，已停止执行。请重新提交。"
                ),
                error_code="capability_binding_changed",
                claim_token=claim_token,
            )
            return True
        if capability is not None or no_read_safe_mode:
            try:
                if no_read_safe_mode:
                    if not validate_no_read_plan(capability, plan["steps"]):
                        raise ValueError("no_read_capability_plan_invalid")
                else:
                    capability.validate_plan(plan["steps"])
            except Exception:
                self.repository.fail_run(
                    run_id,
                    student_id=student_id,
                    reply=_capability_switch_reply(),
                    error_code="capability_plan_mismatch",
                    claim_token=claim_token,
                )
                return True
        if not self._release_matches(
            release, plan["steps"], student_id, capability,
            snapshot.get("interaction_mode"),
        ):
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply="本轮等待期间 Prompt、Skill、模型路由或院校来源版本发生变化，已停止混用新旧版本。请重新提交任务。",
                error_code="runtime_release_changed", claim_token=claim_token,
            )
            return True

        if any(step["role"] not in _SUPPORTED_ROLES for step in plan["steps"]):
            self.repository.fail_run(
                run_id, student_id=student_id, reply=_WRITE_ROLE_REPLY,
                error_code="write_role_reconciliation_unavailable", claim_token=claim_token,
            )
            return True

        board = self._restore_board(plan["steps"])
        next_step = next((step for step in plan["steps"] if step["status"] == "pending"), None)
        if next_step is not None:
            token_cap = None
            if contract is not None:
                usage = self.repository.task_usage_for_owner(run_id, student_id=student_id)
                budget_status = self._budget_status(contract, usage)
                if budget_status["reason"]:
                    return self._settle_partial_for_budget(
                        run_id, student_id, claim_token, run, contract, usage,
                        budget_status["reason"],
                    )
                token_cap = budget_status["remaining_tokens"]
            return self._execute_step(
                run_id, student_id, claim_token, snapshot, plan, next_step, board,
                session_id=str(run.get("session_id") or ""),
                task_contract=contract, task_token_cap=token_cap,
                task_usage=usage if contract is not None else None,
            )
        if any(step["status"] != "completed" for step in plan["steps"]):
            raise AgentRunConflict("plan has an active, failed, or uncertain step")
        if run["status"] == "steps_completed":
            self.repository.begin_finalizing(
                run_id, student_id=student_id, claim_token=claim_token,
            )
            return False
        if run["status"] == "finalizing":
            finalizer_checkpoint = bool(run.get("final_result_checkpoint"))
            if contract is not None and not finalizer_checkpoint:
                usage = self.repository.task_usage_for_owner(run_id, student_id=student_id)
                budget_status = self._budget_status(contract, usage)
                if budget_status["reason"]:
                    return self._settle_partial_for_budget(
                        run_id, student_id, claim_token, run, contract, usage,
                        budget_status["reason"],
                    )
            finalizer_usage = None
            finalizer_usage_before = None
            finalizer_error = None
            if finalizer_checkpoint:
                checkpoint_result = run.get("final_result") or {}
                reply = str(checkpoint_result.get("reply") or "")
                if not reply.strip():
                    raise AgentRunConflict("paused finalizer checkpoint has no reply")
                finalizer_usage = checkpoint_result.get("finalizer_model_usage")
            elif contract is not None and capability is None and not no_read_safe_mode:
                finalizer_usage_before = self.repository.task_usage_for_owner(
                    run_id, student_id=student_id,
                )
                finalizer_budget = self._budget_status(contract, finalizer_usage_before)
                if finalizer_budget["reason"]:
                    return self._settle_partial_for_budget(
                        run_id, student_id, claim_token, run, contract,
                        finalizer_usage_before, finalizer_budget["reason"],
                    )
                finalizer_cap = min(
                    finalizer_budget["remaining_tokens"],
                    contract.budget.max_call_input_tokens
                    + contract.budget.max_call_output_tokens,
                )
                from .budget import capture_usage_ledger

                try:
                    with capture_usage_ledger(
                        "agent_run_finalize", turn_token_cap=finalizer_cap,
                        session_id=str(run.get("session_id") or "") or None,
                        student_id=student_id,
                        **self._task_usage_ledger_options(
                            contract, finalizer_usage_before,
                        ),
                    ) as finalizer_ledger:
                        reply = self._finalize(
                            snapshot["message"], plan["steps"],
                            snapshot["interaction_mode"], capability=capability,
                            no_read_safe_mode=no_read_safe_mode,
                        )
                except Exception as exc:
                    finalizer_error = _safe_error_code(exc)
                finalizer_usage = _project_step_model_usage(finalizer_ledger)
            else:
                reply = self._finalize(
                    snapshot["message"], plan["steps"], snapshot["interaction_mode"],
                    capability=capability, no_read_safe_mode=no_read_safe_mode,
                )
            if not self._claim_and_account_current(run_id, claim_token):
                raise AgentRunConflict("worker claim or account auth epoch changed before final settlement")
            completion = {}
            if (not no_read_safe_mode and capability is not None
                    and capability.execution_pipeline == 'career_exploration_companion_v1'):
                from ..career_exploration_analysis import settle_analysis
                completion['completion_callback'] = lambda conn: settle_analysis(conn, run_id, student_id)
            elif (not no_read_safe_mode and capability is not None
                  and capability.execution_pipeline == 'student_task_v1'):
                from .student_task import settle
                completion['completion_callback'] = lambda conn: settle(self.repository.mysql, conn, session_id=str(run['session_id']), owner=student_id, run_id=run_id)
            final_result = (
                dict(checkpoint_result) if finalizer_checkpoint
                else {"reply": reply, "format": "text"}
            )
            if finalizer_usage is not None and not finalizer_checkpoint:
                final_result["finalizer_model_usage"] = finalizer_usage
            if finalizer_error or (finalizer_usage is not None
                                   and (finalizer_usage.get("status") != "observed"
                                        or finalizer_usage.get("reason")
                                        in _TASK_BUDGET_STOP_REASONS)):
                final_result = {
                    "reply": "最终整理阶段的模型用量无法核验，任务已按部分完成保存。",
                    "format": "text",
                    "candidate_reply_unverified": str(locals().get("reply") or "")[:4000],
                    "finalizer_model_usage": finalizer_usage,
                    "finalizer_error_code": finalizer_error,
                }
                self.repository.mark_partial(
                    run_id, student_id=student_id, final_result=final_result,
                    reason=(
                        "task_budget_exhausted"
                        if finalizer_usage is not None
                        and finalizer_usage.get("reason") in _TASK_BUDGET_STOP_REASONS
                        else "finalizer_usage_unverified"
                    ),
                    claim_token=claim_token,
                )
                return True
            if finalizer_usage is not None and finalizer_usage_before is not None:
                final_calls = finalizer_usage.get("calls") or []
                exceeded = (
                    finalizer_usage_before["model_calls"] + len(final_calls)
                        > contract.budget.max_model_calls
                    or finalizer_usage_before["input_tokens"] + sum(
                        call["usage"]["prompt_tokens"] for call in final_calls
                    ) > contract.budget.max_input_tokens
                    or finalizer_usage_before["output_tokens"] + sum(
                        call["usage"]["completion_tokens"] for call in final_calls
                    ) > contract.budget.max_output_tokens
                    or finalizer_usage_before["total_tokens"]
                        + int(finalizer_usage.get("reported_total_tokens") or 0)
                        > contract.budget.max_input_tokens + contract.budget.max_output_tokens
                )
                if exceeded:
                    self.repository.mark_partial(
                        run_id, student_id=student_id,
                        final_result={
                            **final_result,
                            "reply": "任务达到已配置预算；以下为当前可核验的部分答复。\n\n"
                                     + str(reply)[:8000],
                        },
                        reason="task_budget_exhausted", claim_token=claim_token,
                    )
                    return True
            self.repository.complete_run(
                run_id,
                student_id=student_id,
                final_result=final_result,
                expected_contract_revision=(contract.revision if contract else None),
                claim_token=claim_token,
                **completion,
            )
            return True
        raise AgentRunConflict(f"run status {run['status']} has no valid next phase")

    def _plan(self, run_id: str, student_id: str, claim_token: str,
              snapshot: Dict[str, Any], run: Dict[str, Any], *,
              capability=None, contract: Optional[TaskContract] = None) -> bool:
        from .experts_lead import experts_lead_decide
        from .modes import InteractionMode

        self.repository.begin_planning(
            run_id, student_id=student_id, claim_token=claim_token,
        )
        mode = InteractionMode(snapshot["interaction_mode"])
        from .resource_access_policy import (
            explicitly_forbids_personal_reads,
            no_read_safe_goal,
            no_read_clarification,
            no_read_safe_skill_ids,
            validate_no_read_plan,
        )

        contract_constraints = contract.constraints if contract is not None else ()
        user_message = str(snapshot.get("message") or "")
        if explicitly_forbids_personal_reads(user_message, contract_constraints):
            clarification = no_read_clarification(user_message, contract_constraints)
            if clarification:
                self.repository.set_waiting_status(
                    run_id, student_id=student_id, status="waiting_input",
                    reason=clarification, claim_token=claim_token,
                )
                return True
        baseline = _base_runtime_release()
        planning_usage = None
        if explicitly_forbids_personal_reads(user_message, contract_constraints):
            # A no-read turn uses one server-owned generic QA step for every
            # capability. Reviewed method Skills may remain; owner-bound Skills
            # and all specialized personal-data pipelines are excluded.
            decision = {
                'action': 'run_plan', 'capability_fit': 'supported',
                'goal_summary': user_message[:1000] or '依据一般知识回答本轮问题',
                'steps': [{
                    'role': capability.role if capability is not None else 'qa',
                    'goal': no_read_safe_goal(),
                    'skill_ids': list(no_read_safe_skill_ids(capability)),
                }],
            }
        elif capability is not None and capability.execution_pipeline in {'career_exploration_companion_v1', 'student_task_v1'}:
            # This page already selected a bounded read-only capability. Do not
            # let the generic Lead turn a plan proposal into a write-role task.
            decision = {'action': 'run_plan', 'capability_fit': 'supported',
                        'goal_summary': '依据本轮真实资料陪学生比较、澄清和复盘',
                        'steps': [{'role': 'qa', 'goal': '读取绑定的探索资料并形成待确认建议', 'skill_ids': []}]}
        else:
            planner_kwargs = {
                "card": Blackboard().capability_card(filled_only=True),
                "summary": "",
                "memory_digest": "",
                "plan_mode": (mode == InteractionMode.EXPERTS),
                "blackboard": Blackboard(),
                "interaction_mode": mode.value,
                "selected_capability": (capability.manifest() if capability else None),
            }
            if contract is None:
                # Compatibility for pre-migration direct callers and isolated
                # unit fixtures. Production MySQL requires migration 0021 and
                # therefore always takes the budget-recorded branch below.
                decision = experts_lead_decide(snapshot["message"], **planner_kwargs)
            else:
                from .budget import capture_usage_ledger

                # Planning is a billable model phase. Persist its observed usage
                # in the immutable plan version so revisions do not reset task
                # accounting and callbacks remain auditable.
                usage_before = self.repository.task_usage_for_owner(
                    run_id, student_id=student_id,
                )
                budget_status = self._budget_status(contract, usage_before)
                if budget_status["reason"]:
                    return self._settle_partial_for_budget(
                        run_id, student_id, claim_token, run, contract,
                        usage_before, budget_status["reason"],
                    )
                call_cap = min(
                    budget_status["remaining_tokens"],
                    contract.budget.max_call_input_tokens
                    + contract.budget.max_call_output_tokens,
                )
                user_request = _task_planning_request(snapshot["message"], contract)
                with capture_usage_ledger(
                    "agent_run_planning", turn_token_cap=call_cap,
                    session_id=str(run.get("session_id") or "") or None,
                    student_id=student_id,
                    **self._task_usage_ledger_options(contract, usage_before),
                ) as planning_ledger:
                    decision = experts_lead_decide(
                        user_request,
                        **planner_kwargs,
                    )
                planning_usage = _project_step_model_usage(planning_ledger)
                if planning_usage.get("reason") in _TASK_BUDGET_STOP_REASONS:
                    self.repository.mark_partial(
                        run_id, student_id=student_id,
                        final_result={
                            "reply": "�滮�׶δﵽ������Ԥ�㣬δ��ɵĲ��轫��Ϊ���ֽ�����",
                            "format": "text",
                            "planner_model_usage": planning_usage,
                        },
                        reason="task_budget_exhausted", claim_token=claim_token,
                    )
                    return True
                if planning_usage["status"] != "observed":
                    if planning_usage.get("reason") == "turn_token_cap_exceeded":
                        self.repository.mark_partial(
                            run_id, student_id=student_id,
                            final_result={
                                "reply": "规划阶段达到剩余 token 预算；尚无可核验计划，任务已部分完成。",
                                "format": "text",
                                "planner_model_usage": planning_usage,
                            },
                            reason="task_budget_exhausted", claim_token=claim_token,
                        )
                        return True
                    self.repository.fail_run(
                        run_id, student_id=student_id,
                        reply=("规划阶段的模型用量无法核验，任务已停止；"
                               "系统不会继续执行或声称预算已核实。"),
                        error_code="planning_usage_unverified", claim_token=claim_token,
                    )
                    return True
                if (
                    usage_before["model_calls"] + len(planning_usage["calls"])
                        > contract.budget.max_model_calls
                    or planning_usage["reported_total_tokens"] > call_cap
                    or sum(call["usage"]["prompt_tokens"] for call in planning_usage["calls"])
                        > contract.budget.max_input_tokens - usage_before["input_tokens"]
                    or sum(call["usage"]["completion_tokens"] for call in planning_usage["calls"])
                        > contract.budget.max_output_tokens - usage_before["output_tokens"]
                ):
                    self.repository.mark_partial(
                        run_id, student_id=student_id,
                        final_result={
                            "reply": "规划调用达到任务剩余预算；尚无完整计划，任务已部分完成。",
                            "format": "text",
                            "planner_model_usage": planning_usage,
                        },
                        reason="task_budget_exhausted", claim_token=claim_token,
                    )
                    return True
        if baseline != _base_runtime_release():
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply="本轮规划期间的 Prompt 或模型配置发生变化，已停止混用版本。请重新提交任务。",
                error_code="planning_release_changed", claim_token=claim_token,
            )
            return True
        if (capability is None
                and decision.get("action") in {
                    "respond", "answer_direct", "direct_answer",
                }):
            reply = decision.get("reply") or decision.get("answer")
            if not isinstance(reply, str) or not reply.strip():
                self.repository.set_waiting_status(
                    run_id, student_id=student_id, status="waiting_input",
                    reason="直接答复缺少有效内容，请补充或重新提交目标。",
                    claim_token=claim_token,
                )
                return True
            self.repository.complete_direct_run(
                run_id, student_id=student_id,
                final_result={
                    "reply": reply[:8000], "format": "text",
                    **({"planner_model_usage": planning_usage}
                       if planning_usage is not None else {}),
                },
                expected_contract_revision=(contract.revision if contract else None),
                claim_token=claim_token,
            )
            return True
        if (decision.get("action") in {"clarify", "ask", "needs_input"}
                and not (capability is not None
                         and decision.get("capability_fit") == "unsupported")):
            self.repository.set_waiting_status(
                run_id, student_id=student_id, status="waiting_input",
                reason=str(decision.get("reply") or "任务缺少必要信息")[:500],
                claim_token=claim_token,
            )
            return True
        if decision.get("action") != "run_plan":
            if capability is not None and decision.get("capability_fit") == "unsupported":
                reply = _capability_switch_reply()
                error_code = "capability_not_applicable"
            else:
                reply = str(
                    decision.get("reply")
                    or "本轮尚未形成可执行的服务端计划，请补充目标后重试。"
                )
                error_code = "plan_requires_clarification"
            self.repository.fail_run(
                run_id, student_id=student_id, reply=reply,
                error_code=error_code, claim_token=claim_token,
            )
            return True
        if capability is not None and decision.get("capability_fit") != "supported":
            self.repository.fail_run(
                run_id,
                student_id=student_id,
                reply=(
                    _capability_switch_reply()
                    if decision.get("capability_fit") == "unsupported"
                    else "本轮没有获得 Lead 对所选能力包适用性的明确确认，计划未执行。请补充后重试或切换能力包。"
                ),
                error_code="capability_fit_unconfirmed",
                claim_token=claim_token,
            )
            return True
        steps = decision.get("steps") or []
        goal_summary = str(decision.get("goal_summary") or "执行用户请求")[:1000]
        handoff_brief = str(decision.get("handoff_brief") or "")
        try:
            from .plan import validate_plan_steps

            steps = validate_plan_steps(
                steps,
                capability_id=_capability_skill_registry_id(capability),
            )
            if capability is not None:
                if explicitly_forbids_personal_reads(user_message, contract_constraints):
                    if not validate_no_read_plan(capability, steps):
                        raise ValueError("no_read_capability_plan_invalid")
                else:
                    capability.validate_plan(steps)
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_v1"):
                runtime_release = _runtime_release(
                    steps, student_id, capability=capability,
                    interaction_mode=mode.value,
                )
            elif (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v2"):
                runtime_release = _runtime_release(
                    steps, student_id, capability=capability,
                    interaction_mode=mode.value,
                )
            else:
                runtime_release = _runtime_release(
                    steps, student_id, capability=capability,
                )
        except Exception:
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply=(
                    _capability_switch_reply()
                    if capability else
                    "本轮计划或 Skill 发布快照未通过服务端校验，任务尚未执行。请重试。"
                ),
                error_code=(
                    "capability_plan_mismatch"
                    if capability else "plan_release_invalid"
                ),
                claim_token=claim_token,
            )
            return True
        if runtime_release["base"] != baseline:
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply="本轮规划期间的 Prompt 或模型配置发生变化，已停止混用版本。请重新提交任务。",
                error_code="planning_release_changed", claim_token=claim_token,
            )
            return True
        self.repository.persist_plan(
            run_id,
            student_id=student_id,
            expected_plan_version=int(run["current_plan_version"]),
            goal_summary=goal_summary[:1000],
            steps=steps,
            plan_metadata={
                "executor_contract": 1,
                "handoff_brief": handoff_brief[:1000],
                **({"task_contract_revision": contract.revision}
                   if contract is not None else {}),
                **({"task_model_usage": planning_usage}
                   if planning_usage is not None else {}),
                "capability_binding": (
                    capability.manifest() if capability is not None else None
                ),
                **({"lead_decision": _controlled_lead_decision(decision)}
                   if capability is not None else {}),
                "runtime_release": runtime_release,
                "release_sha256": runtime_release["release_sha256"],
            },
            expected_contract_revision=(contract.revision if contract else None),
            claim_token=claim_token,
        )
        # Plans are persisted as an auditable contract before checking whether
        # this executor has safe adapters for every selected role.
        if any(step["role"] not in _SUPPORTED_ROLES for step in steps):
            self.repository.fail_run(
                run_id, student_id=student_id, reply=_WRITE_ROLE_REPLY,
                error_code="write_role_reconciliation_unavailable", claim_token=claim_token,
            )
            return True
        return False

    def _execute_step(self, run_id, student_id, claim_token, snapshot, plan, step, board,
                      *, session_id: str, task_contract: Optional[TaskContract] = None,
                      task_token_cap: Optional[int] = None,
                      task_usage: Optional[Dict[str, Any]] = None) -> bool:
        from .modes import InteractionMode
        from .plan import build_plan_packets

        interaction_mode = InteractionMode(snapshot["interaction_mode"])
        from .expert_capabilities import resolve_explicit_capability
        from .resource_access_policy import (
            explicitly_forbids_personal_reads,
            has_verified_read_tool_result,
            no_read_safe_goal,
            no_read_clarification,
            no_read_safe_skill_ids,
            step_requires_resource_read,
            validate_no_read_plan,
        )

        capability = resolve_explicit_capability(snapshot)
        task_constraints = task_contract.constraints if task_contract is not None else ()
        read_prohibited = explicitly_forbids_personal_reads(
            snapshot.get("message"), task_constraints,
        )
        no_personal_reads = read_prohibited
        if no_personal_reads and not validate_no_read_plan(capability, plan.get("steps")):
            self.repository.fail_run(
                run_id, student_id=student_id,
                reply="本轮禁止读取资料，但已保存计划不符合只读边界；任务已停止。请重新提交安全范围明确的请求。",
                error_code="no_read_plan_mismatch", claim_token=claim_token,
            )
            return True
        packets = build_plan_packets(
            [{"role": item["role"], "goal": item["goal"],
              "skill_ids": item["skill_ids"]} for item in plan["steps"]],
            snapshot["message"],
            handoff_brief=str(plan.get("metadata", {}).get("handoff_brief") or ""),
            goal_summary=str(plan["goal_summary"]),
            capability_id=_capability_skill_registry_id(capability),
        )
        packet = packets[int(step["step_index"]) - 1]
        self.repository.start_step(
            run_id,
            student_id=student_id,
            plan_version=int(plan["plan_version"]),
            step_index=int(step["step_index"]),
            claim_token=claim_token,
        )
        self._renew_or_stop(run_id, claim_token)
        usage_ledger = None
        education_conditions_record = None
        education_finalizer = None
        education_structured_record = None
        education_structured_finalizer = None
        education_structured_v3_record = None
        education_structured_v3_finalizer = None
        education_structured_v4_record = None
        education_structured_v4_finalizer = None
        education_structured_v5_record = None
        education_structured_v5_finalizer = None
        exploration_analysis = None
        student_task_result = None
        runtime_release = (plan.get("metadata") or {}).get("runtime_release") or {}
        education_pipeline_release = runtime_release.get("education_pipeline")
        education_structured_release = runtime_release.get("education_screening_structured_v2")
        education_structured_v3_release = runtime_release.get("education_screening_structured_v3")
        education_structured_v4_release = runtime_release.get("education_screening_structured_v4")
        education_structured_v5_release = runtime_release.get("education_screening_structured_v5")
        try:
            from .budget import capture_usage_ledger
            from .mode_runtime import ModeRuntime, _invoke_subagent

            # The persisted intent is the replay boundary. Recompute the full
            # selected release after it is durable and before the first model
            # dispatch; a changed pipeline is never silently hot-switched.
            if not self._release_matches(
                runtime_release, plan["steps"], student_id, capability,
                interaction_mode.value,
            ):
                raise RuntimeError("runtime_release_changed_before_step_dispatch")
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_v1"
                    and not isinstance(education_pipeline_release, dict)):
                raise RuntimeError("education_pipeline_release_missing")
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v2"
                    and not isinstance(education_structured_release, dict)):
                raise RuntimeError("education_screening_structured_v2_release_missing")
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v3"
                    and not isinstance(education_structured_v3_release, dict)):
                raise RuntimeError("education_screening_structured_v3_release_missing")
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v4"
                    and not isinstance(education_structured_v4_release, dict)):
                raise RuntimeError("education_screening_structured_v4_release_missing")
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v5"
                    and not isinstance(education_structured_v5_release, dict)):
                raise RuntimeError("education_screening_structured_v5_release_missing")

            # This durable worker does not pass through ModeRuntime.chat(), so
            # establish the same provider-usage/token-cap boundary explicitly.
            # Every specialized model call stays inside this persisted step's
            # budget and no intermediate call is replayed independently.
            call_cap = task_token_cap
            if task_contract is not None:
                one_call_cap = (
                    task_contract.budget.max_call_input_tokens
                    + task_contract.budget.max_call_output_tokens
                )
                call_cap = min(call_cap, one_call_cap) if call_cap is not None else one_call_cap
            with capture_usage_ledger(
                "agent_run_step", turn_token_cap=call_cap,
                session_id=session_id or None, student_id=student_id,
                **self._task_usage_ledger_options(task_contract, task_usage),
            ) as usage_ledger:
                if no_personal_reads:
                    clarification = no_read_clarification(
                        snapshot.get("message"), task_constraints,
                    )
                    if clarification:
                        raise RuntimeError("no_read_clarification_required")
                    from .task_packet import build_standard_packet

                    contract_context = ""
                    if task_contract is not None:
                        contract_sections = [
                            "当前用户任务契约（仅定义本轮目标与输出标准，不赋予资料读取或业务写入权限）：",
                            "目标：" + task_contract.goal,
                        ]
                        if task_contract.deliverables:
                            contract_sections.append(
                                "交付物：" + "；".join(task_contract.deliverables),
                            )
                        if task_contract.constraints:
                            contract_sections.append(
                                "当前约束：" + "；".join(task_contract.constraints),
                            )
                        if task_contract.acceptance_criteria:
                            contract_sections.append(
                                "验收标准：" + "；".join(task_contract.acceptance_criteria),
                            )
                        contract_context = "\n\n" + "\n".join(contract_sections)

                    safe_packet = build_standard_packet(
                        "qa",
                        goal=(
                            no_read_safe_goal()
                            + contract_context
                            + "\n\n用户原话："
                            + str(snapshot.get("message") or "")
                        ),
                        user_utterance=str(snapshot.get("message") or ""),
                        read_keys=[], write_keys=[],
                        skill_ids=list(no_read_safe_skill_ids(capability)),
                        capability_id=_capability_skill_registry_id(capability),
                        plan_step_id=f"{run_id}:{plan['plan_version']}:{step['step_index']}",
                    )
                    output = _invoke_subagent(
                        safe_packet, Blackboard(), None,
                        allow_tools=False,
                        interaction_mode=InteractionMode.ASK,
                        student_id=None,
                        plan_tool_allowlist=set(),
                        allow_artifact_commit=False,
                        max_tokens=(
                            min(2048, task_contract.budget.max_call_output_tokens)
                            if task_contract is not None else 1024
                        ),
                        no_tool_output_token_cap=(
                            min(2048, task_contract.budget.max_call_output_tokens)
                            if task_contract is not None else 1024
                        ),
                    )
                    reply_text = str(output.get("reply") or "").strip()
                    if not reply_text:
                        raise RuntimeError("read_only_answer_empty")
                    if capability is not None and capability.execution_pipeline == "student_task_v1":
                        student_task_result = {
                            "agent": "general_knowledge",
                            "state": "completed",
                            "reply": reply_text,
                            "evidence_summary": [],
                            "resource_access": "none",
                        }
                        from .task_context import TaskContextStore
                        TaskContextStore(self.repository.mysql).stage(
                            session_id, student_id, run_id, student_task_result,
                        )
                    elif capability is not None and capability.execution_pipeline == "career_exploration_companion_v1":
                        exploration_analysis = {
                            "reply": reply_text,
                            "review": None,
                            "evidence_summary": [],
                            "resource_access": "none",
                        }
                elif (capability is not None
                        and capability.execution_pipeline == "education_screening_structured_v2"):
                    from ..db.mysql_client import get_mysql_client
                    from .education_screening_structured_v2 import run_structured_education_step
                    from .school_facts import (
                        project_verified_school_facts, read_owner_school_context,
                    )

                    school_context = read_owner_school_context(
                        get_mysql_client(), student_id=student_id,
                    )
                    verified_school_facts = project_verified_school_facts(school_context)
                    output = run_structured_education_step(
                        user_message=str(snapshot.get("message") or ""),
                        owner_id=str(student_id), run_id=str(run_id),
                        expected_runtime_contract=education_structured_release,
                        verified_school_facts=verified_school_facts,
                    )
                    education_structured_record = _persist_education_screening_v2(
                        output.get("education_screening_v2")
                        or output.get("analysis") or {},
                    )
                    education_structured_finalizer = _safe_education_v2_finalizer(
                        output.get("finalizer"),
                    )
                elif (capability is not None
                      and capability.execution_pipeline == "education_screening_structured_v3"):
                    from ..db.mysql_client import get_mysql_client
                    from .education_screening_structured_v3 import run_structured_education_step
                    from .school_facts import (
                        project_verified_school_facts, read_owner_school_context,
                    )

                    school_context = read_owner_school_context(
                        get_mysql_client(), student_id=student_id,
                    )
                    verified_school_facts = project_verified_school_facts(school_context)
                    output = run_structured_education_step(
                        user_message=str(snapshot.get("message") or ""),
                        owner_id=str(student_id), run_id=str(run_id),
                        expected_runtime_contract=education_structured_v3_release,
                        verified_school_facts=verified_school_facts,
                    )
                    education_structured_v3_record = _persist_education_screening_v3(
                        output.get("education_screening_v3") or output.get("analysis") or {},
                    )
                    education_structured_v3_finalizer = _safe_education_v2_finalizer(
                        output.get("finalizer"),
                    )
                elif (capability is not None
                      and capability.execution_pipeline == "education_screening_structured_v4"):
                    from ..db.mysql_client import get_mysql_client
                    from .education_screening_structured_v4 import run_structured_education_step
                    from .school_facts import (
                        project_verified_school_facts, read_owner_school_context,
                    )

                    school_context = read_owner_school_context(
                        get_mysql_client(), student_id=student_id,
                    )
                    verified_school_facts = project_verified_school_facts(school_context)
                    output = run_structured_education_step(
                        user_message=str(snapshot.get("message") or ""),
                        owner_id=str(student_id), run_id=str(run_id),
                        expected_runtime_contract=education_structured_v4_release,
                        verified_school_facts=verified_school_facts,
                    )
                    education_structured_v4_record = _persist_education_screening_v4(
                        output.get("education_screening_v4") or output.get("analysis") or {},
                        output.get("projection") or {},
                    )
                    education_structured_v4_finalizer = _safe_education_v2_finalizer(
                        output.get("finalizer"),
                    )
                elif (capability is not None
                      and capability.execution_pipeline == "education_screening_structured_v5"):
                    from ..db.mysql_client import get_mysql_client
                    from .education_screening_structured_v5 import run_structured_education_step_v5
                    from .school_facts import (
                        project_verified_school_facts, read_owner_school_context,
                    )

                    school_context = read_owner_school_context(
                        get_mysql_client(), student_id=student_id,
                    )
                    verified_school_facts = project_verified_school_facts(school_context)
                    output = run_structured_education_step_v5(
                        user_message=str(snapshot.get("message") or ""),
                        owner_id=str(student_id), run_id=str(run_id),
                        expected_runtime_contract=education_structured_v5_release,
                        verified_school_facts=verified_school_facts,
                    )
                    education_structured_v5_record = _persist_education_screening_v4(
                        output.get("education_screening_v4") or output.get("analysis") or {},
                        output.get("projection") or {},
                    )
                    education_structured_v5_finalizer = _safe_education_v5_finalizer(
                        output.get("finalizer"),
                    )
                elif capability is not None and capability.execution_pipeline == 'student_task_v1':
                    from .student_task import run_step
                    output = run_step(mysql=self.repository.mysql, snapshot=snapshot, run_id=run_id,
                        owner=student_id, session_id=session_id, repository=self.repository,
                        claim_token=claim_token, invoke=_invoke_subagent, packet=packet, board=board)
                    student_task_result = output['student_task']
                elif capability is not None and capability.execution_pipeline == 'career_exploration_companion_v1':
                    from ..career_exploration_analysis import run_analysis_step
                    output = run_analysis_step(mysql=self.repository.mysql, run_id=run_id,
                        sid=student_id, repository=self.repository, claim_token=claim_token,
                        invoke=_invoke_subagent, packet=packet, board=board,
                        session_id=session_id, mode=interaction_mode)
                    exploration_analysis = output['exploration_analysis']
                else:
                    output = _invoke_subagent(
                        packet,
                        board,
                        session_id or None,
                        allow_tools=True,
                        interaction_mode=interaction_mode,
                        student_id=student_id,
                        plan_tool_allowlist=set(_RUN_QA_TOOL_ALLOWLIST),
                        allow_artifact_commit=False,
                    )
                if (not no_personal_reads and capability is not None
                        and capability.execution_pipeline == "education_screening_v1"):
                    self._assert_skill_versions(output, runtime_release)
                    skill_rows = output.get("skill_versions") or []
                    education_skill = next((
                        item for item in skill_rows
                        if isinstance(item, dict)
                        and item.get("skill_id") == "education-screening-context"
                    ), None)
                    if not isinstance(education_skill, dict):
                        raise RuntimeError("selected_education_skill_not_loaded")
                    from .education_screening import (
                        extract_education_conditions,
                    )

                    extracted = extract_education_conditions(
                        user_message=str(snapshot.get("message") or ""),
                        owner_id=str(student_id),
                        run_id=str(run_id),
                        skill_sha256=str(education_skill.get("sha256") or ""),
                        # Current persistent QA tools expose no notice-body
                        # receipt contract. Never promote their free-text cards.
                        receipts=(),
                        include_redacted_diagnostics=True,
                        expected_runtime_contract=education_pipeline_release,
                    )
                    education_conditions_record = _persist_education_conditions(extracted)
                    if extracted.get("error_code") in {
                        "runtime_release_changed", "runtime_release_unavailable",
                    }:
                        raise RuntimeError("education_pipeline_release_changed_before_extractor")
                    education_finalizer = ModeRuntime()._finalize_plan_reply(
                        message=str(snapshot.get("message") or ""),
                        steps=[{
                            "role": "qa",
                            "status": "completed",
                            "verified_facts": output.get("verified_facts") or [],
                            "education_conditions": education_conditions_record,
                        }],
                        interaction_mode=interaction_mode,
                        education_screening_constrained=True,
                        return_details=True,
                        education_pipeline_release=education_pipeline_release,
                    )
                    if (not isinstance(education_finalizer, dict)
                            or not isinstance(education_finalizer.get("reply"), str)
                            or not education_finalizer["reply"].strip()):
                        raise RuntimeError("education_screening_finalizer_result_invalid")
                    if education_finalizer.get("decision_reason") in {
                        "runtime_release_changed", "runtime_release_unavailable",
                    }:
                        raise RuntimeError("education_pipeline_release_changed_before_finalizer")
                model_usage = _project_step_model_usage(usage_ledger)
                if model_usage.get("reason") in _TASK_BUDGET_STOP_REASONS:
                    raise RuntimeError(str(model_usage["reason"]))
                if model_usage["status"] != "observed":
                    raise RuntimeError(model_usage["reason"])
            if not self._release_matches(
                plan["metadata"]["runtime_release"], plan["steps"],
                student_id, capability, interaction_mode.value,
            ):
                raise RuntimeError("runtime_release_changed_during_step")
            if not self._claim_and_account_current(run_id, claim_token):
                raise AgentRunConflict("worker claim or account auth epoch changed during step")
            self._assert_skill_versions(output, plan["metadata"]["runtime_release"])
            if output.get("incomplete"):
                self.repository.finish_step(
                    run_id,
                    student_id=student_id,
                    plan_version=int(plan["plan_version"]),
                    step_index=int(step["step_index"]),
                    outcome="failed",
                    result={"reply": str(output.get("reply") or "")[:8000],
                            "skill_versions": output.get("skill_versions") or [],
                            "tool_calls": _safe_tool_call_cards(output.get("tool_calls")),
                            "tool_call_count": _phase_tool_call_count(
                                usage_ledger, task_usage, output.get("tool_calls"),
                            ),
                            "model_usage": model_usage,
                            "verified_updates": {},
                            "result_class": "read_only_answer_incomplete"},
                    error_code=_safe_error_code(RuntimeError(
                        str(output.get("stop_reason") or "read_only_answer_incomplete"),
                    )),
                    expected_contract_revision=(
                        task_contract.revision if task_contract else None
                    ),
                    claim_token=claim_token,
                )
                self.repository.fail_run(
                    run_id,
                    student_id=student_id,
                    reply=failure_reply(str(output.get('stop_reason') or 'read_only_answer_incomplete')),
                    error_code="read_only_answer_incomplete",
                    claim_token=claim_token,
                )
                return True
            reply = str(output.get("reply") or "").strip()
            if not reply:
                raise RuntimeError("read_only_answer_empty")
            if (not no_personal_reads
                    and step_requires_resource_read(step.get("goal"))
                    and not has_verified_read_tool_result(output)):
                receipt_reader = getattr(
                    self.repository, "has_completed_resource_read_for_step", None,
                )
                has_durable_receipt = bool(callable(receipt_reader) and receipt_reader(
                    run_id,
                    student_id=student_id,
                    plan_version=int(plan["plan_version"]),
                    step_index=int(step["step_index"]),
                ))
                if not has_durable_receipt:
                    raise RuntimeError("resource_read_result_unverified")
            # QA is read-only and its board write set is empty. Store only the
            # candidate narrative separately from source provenance; never
            # promote model prose to a server-verified business fact.
            source_reads = [
                {
                    "tool_name": str(item.get("tool_name") or "")[:64],
                    "status": str(item.get("status") or "")[:32],
                    "summary": str(item.get("summary") or "")[:240],
                }
                for item in output.get("tool_calls") or []
                if isinstance(item, dict) and item.get("kind") == "skill_reference_read"
            ][:8]
            result = {
                "verified_facts": output.get("verified_facts") or [],
                "source_reads": source_reads,
                "skill_versions": output.get("skill_versions") or [],
                "tool_calls": _safe_tool_call_cards(output.get("tool_calls")),
                "tool_call_count": _phase_tool_call_count(
                    usage_ledger, task_usage, output.get("tool_calls"),
                ),
                "model_usage": model_usage,
                "verified_updates": {},
            }
            if exploration_analysis is not None:
                result['exploration_analysis'] = exploration_analysis
            if student_task_result is not None:
                result['student_task'] = student_task_result
            if (no_personal_reads or not (capability is not None
                    and capability.execution_pipeline in {
                        "education_screening_structured_v2",
                        "education_screening_structured_v3",
                        "education_screening_structured_v4",
                        "education_screening_structured_v5",
                    })):
                result["reply_candidate_unverified"] = reply[
                    :1200 if capability is not None
                    and capability.execution_pipeline == "education_screening_v1"
                    else 8000
                ]
            if (not no_personal_reads and capability is not None
                    and capability.execution_pipeline == "education_screening_v1"):
                result.update({
                    "capability_pipeline": capability.execution_pipeline,
                    "education_pipeline_release_sha256": _json_sha256(
                        education_pipeline_release,
                    ),
                    "education_conditions": education_conditions_record or {},
                    "education_finalizer": _safe_education_finalizer(education_finalizer),
                    "capability_reply": education_finalizer["reply"][:4000],
                })
            elif (not no_personal_reads and capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v2"):
                result.update({
                    "capability_pipeline": capability.execution_pipeline,
                    "education_screening_structured_v2_release_sha256": _json_sha256(
                        education_structured_release,
                    ),
                    "education_screening_v2": education_structured_record or {},
                    "education_finalizer": education_structured_finalizer or {},
                    "capability_reply": reply[:4000],
                })
            elif (not no_personal_reads and capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v3"):
                result.update({
                    "capability_pipeline": capability.execution_pipeline,
                    "education_screening_structured_v3_release_sha256": _json_sha256(
                        education_structured_v3_release,
                    ),
                    "education_screening_v3": education_structured_v3_record or {},
                    "education_finalizer": education_structured_v3_finalizer or {},
                    "capability_reply": reply[:4000],
                })
            elif (not no_personal_reads and capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v4"):
                result.update({
                    "capability_pipeline": capability.execution_pipeline,
                    "education_screening_structured_v4_release_sha256": _json_sha256(
                        education_structured_v4_release,
                    ),
                    "education_screening_v4": education_structured_v4_record or {},
                    "education_finalizer": education_structured_v4_finalizer or {},
                    "capability_reply": reply[:4000],
                })
            elif (not no_personal_reads and capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v5"):
                result.update({
                    "capability_pipeline": capability.execution_pipeline,
                    "education_screening_structured_v5_release_sha256": _json_sha256(
                        education_structured_v5_release,
                    ),
                    "education_screening_v5": {
                        "schema_version": 5,
                        "extractor_schema_version": 4,
                        "analysis": education_structured_v5_record or {},
                        "projection": output.get("projection") or {},
                    },
                    "education_finalizer": education_structured_v5_finalizer or {},
                    "capability_reply": reply[:4000],
                })
            self.repository.finish_step(
                run_id,
                student_id=student_id,
                plan_version=int(plan["plan_version"]),
                step_index=int(step["step_index"]),
                outcome="completed",
                result=result,
                expected_contract_revision=(
                    task_contract.revision if task_contract else None
                ),
                claim_token=claim_token,
            )
            return False
        except AgentRunConflict:
            raise
        except Exception as exc:
            # The worker contract is QA-only, and all currently exposed QA
            # tools are reads. Never use this branch for business-write roles.
            model_usage = _project_step_model_usage(usage_ledger)
            try:
                self.repository.finish_step(
                    run_id,
                    student_id=student_id,
                    plan_version=int(plan["plan_version"]),
                    step_index=int(step["step_index"]),
                    outcome="failed",
                    result={"reply_candidate_unverified": "", "verified_facts": [],
                            "source_reads": [], "tool_calls": _safe_tool_call_cards(
                                (locals().get("output") or {}).get("tool_calls")
                                if isinstance(locals().get("output"), dict) else [],
                            ),
                            "tool_call_count": _phase_tool_call_count(
                                usage_ledger,
                                task_usage,
                                (locals().get("output") or {}).get("tool_calls")
                                if isinstance(locals().get("output"), dict) else [],
                            ),
                            "skill_versions": [],
                            "model_usage": model_usage,
                            "verified_updates": {}},
                    error_code=_safe_error_code(exc),
                    expected_contract_revision=(
                        task_contract.revision if task_contract else None
                    ),
                    claim_token=claim_token,
                )
            except Exception:
                # A failed settlement leaves the started step running; claim
                # expiry will move it to needs_reconciliation, never rerun it.
                raise
            if (_safe_error_code(exc) in _TASK_BUDGET_STOP_REASONS
                    and task_contract is not None):
                current = self.repository.get_for_owner(
                    run_id, student_id=student_id,
                )
                usage = self.repository.task_usage_for_owner(
                    run_id, student_id=student_id,
                )
                return self._settle_partial_for_budget(
                    run_id, student_id, claim_token, current, task_contract,
                    usage, "task_budget_exhausted",
                )
            self.repository.fail_run(
                run_id,
                student_id=student_id,
                reply=failure_reply(_safe_error_code(exc)),
                error_code=_safe_error_code(exc),
                claim_token=claim_token,
            )
            return True

    def _finalize(self, message: str, steps: list[dict], interaction_mode: str,
                  *, capability=None, no_read_safe_mode: bool = False) -> str:
        from .mode_runtime import ModeRuntime
        from .modes import InteractionMode

        if no_read_safe_mode:
            from .resource_access_policy import sanitize_no_read_reply

            for step in steps:
                result = step.get("result") or {}
                for candidate in (
                    (result.get("student_task") or {}).get("reply"),
                    (result.get("exploration_analysis") or {}).get("reply"),
                    result.get("reply_candidate_unverified"),
                ):
                    if isinstance(candidate, str) and candidate.strip():
                        return sanitize_no_read_reply(candidate)
            raise ValueError("no_read_answer_missing")

        if capability is not None and capability.execution_pipeline == 'student_task_v1':
            result = next(((s.get('result') or {}).get('student_task') for s in steps
                           if (s.get('result') or {}).get('student_task')), None)
            if not result or not result.get('reply'):
                raise ValueError('任务没有可交付答复')
            return result['reply']

        if capability is not None and capability.execution_pipeline == 'career_exploration_companion_v1':
            result = next(((s.get('result') or {}).get('exploration_analysis') for s in steps
                           if (s.get('result') or {}).get('exploration_analysis')), None)
            if not result:
                raise ValueError('探索分析没有可交付结果')
            return result['reply']

        if (capability is not None
                and getattr(capability, "execution_pipeline", None) == "education_screening_v1"):
            result = next((
                (step.get("result") or {}) for step in steps
                if step.get("role") == "qa"
            ), {})
            reply = result.get("capability_reply")
            if isinstance(reply, str) and reply.strip():
                return reply[:4000]
            return (
                "本轮教育条件分析没有形成可交付答复；为避免遗漏原文条件或例外，"
                "请重新提交完整岗位公告和相关学历信息。"
            )
        if (capability is not None
                and getattr(capability, "execution_pipeline", None) == "education_screening_structured_v2"):
            result = next((
                (step.get("result") or {}) for step in steps if step.get("role") == "qa"
            ), {})
            reply = result.get("capability_reply")
            if isinstance(reply, str) and reply.strip():
                return reply[:4000]
            return "本轮没有形成可交付的教育筛选答复；请补充目标岗位原文和相关条件后重试。"
        if (capability is not None
                and getattr(capability, "execution_pipeline", None) == "education_screening_structured_v3"):
            result = next((
                (step.get("result") or {}) for step in steps if step.get("role") == "qa"
            ), {})
            reply = result.get("capability_reply")
            if isinstance(reply, str) and reply.strip():
                return reply[:4000]
            return "本轮没有形成可交付的教育筛选答复；请补充完整岗位条件及相关自述后重试。"
        if (capability is not None
                and getattr(capability, "execution_pipeline", None) == "education_screening_structured_v4"):
            result = next((
                (step.get("result") or {}) for step in steps if step.get("role") == "qa"
            ), {})
            reply = result.get("capability_reply")
            if isinstance(reply, str) and reply.strip():
                return reply[:4000]
            return "本轮没有形成可交付的教育筛选答复；请补充完整岗位条件、适用范围和相关自述后重试。"
        if (capability is not None
                and getattr(capability, "execution_pipeline", None) == "education_screening_structured_v5"):
            result = next((
                (step.get("result") or {}) for step in steps if step.get("role") == "qa"
            ), {})
            reply = result.get("capability_reply")
            if isinstance(reply, str) and reply.strip():
                return reply[:4000]
            return "本轮没有形成可交付的教育筛选答复；请补充完整岗位条件及相关自述后重试。"

        result_rows = []
        school_context_constrained = bool(
            capability is not None and "school-context" in capability.skill_ids
        )
        for step in steps:
            result = step.get("result") or {}
            row = {
                "role": step["role"],
                "status": "completed" if step["status"] == "completed" else "not_completed",
                "verified_facts": result.get("verified_facts") or [],
            }
            if not school_context_constrained:
                row.update({
                    "verified_sources": result.get("source_reads") or [],
                    "tool_calls": result.get("tool_calls") or [],
                    "step_output_candidate_unverified": result.get("reply_candidate_unverified") or "",
                    "skill_versions": result.get("skill_versions") or [],
                })
            result_rows.append(row)
        # ModeRuntime's finalizer remains the single main-Agent natural-language
        # composition path; the prompt is separately amended to distinguish
        # candidate text from server-verified fact/provenance fields.
        return ModeRuntime()._finalize_plan_reply(
            message=message,
            steps=result_rows,
            interaction_mode=InteractionMode(interaction_mode),
            school_context_constrained=school_context_constrained,
        )

    def _restore_board(self, steps: list[dict]) -> Blackboard:
        board = Blackboard()
        for step in steps:
            if step.get("status") != "completed":
                continue
            result = step.get("result") or {}
            updates = result.get("verified_updates") or {}
            if updates:
                if not isinstance(updates, dict):
                    raise AgentRunConflict("persisted verified step updates have invalid shape")
                board.write_many(updates, str(step["role"]))
        return board

    def _release_matches(self, expected: Dict[str, Any], steps: list[dict],
                         student_id: str, capability=None,
                         interaction_mode: Optional[str] = None) -> bool:
        try:
            selected_steps = [
                {"role": step["role"], "goal": step["goal"],
                 "skill_ids": step["skill_ids"]} for step in steps
            ]
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_v1"):
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                    interaction_mode=interaction_mode or "experts",
                )
            elif (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v2"):
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                    interaction_mode=interaction_mode or "experts",
                )
            elif (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v3"):
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                    interaction_mode=interaction_mode or "experts",
                )
            elif (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v4"):
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                    interaction_mode=interaction_mode or "experts",
                )
            elif (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v5"):
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                    interaction_mode=interaction_mode or "experts",
                )
            else:
                current = _runtime_release(
                    selected_steps, student_id, capability=capability,
                )
            return current == expected
        except Exception:
            return False

    def _assert_skill_versions(self, output: Dict[str, Any], release: Dict[str, Any]) -> None:
        expected_rows = release.get("skills") or []
        observed_rows = output.get("skill_versions") or []
        if not isinstance(expected_rows, list) or not isinstance(observed_rows, list):
            raise RuntimeError("invalid_skill_version_result")
        expected = {
            item["skill_id"]: item for item in expected_rows
            if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
        }
        observed_by_id = {}
        for observed in observed_rows:
            if not isinstance(observed, dict):
                raise RuntimeError("invalid_skill_version_result")
            skill_id = observed.get("skill_id")
            if not isinstance(skill_id, str) or skill_id in observed_by_id:
                raise RuntimeError("invalid_skill_version_result")
            observed_by_id[skill_id] = observed
        if set(observed_by_id) != set(expected):
            raise RuntimeError("selected_skill_not_loaded")
        for skill_id, observed in observed_by_id.items():
            pinned = expected.get(skill_id)
            if not pinned or observed.get("version") != pinned["version"] \
                    or observed.get("sha256") != pinned["sha256"]:
                raise RuntimeError("skill_release_changed_during_step")
            if skill_id == "school-context":
                if str(observed.get("knowledge_sha256") or "") != str(
                    pinned.get("knowledge_sha256") or "",
                ):
                    raise RuntimeError("school_source_changed_during_step")
                observed_profile = str(observed.get("profile_version") or "")
                pinned_profile = str(pinned.get("profile_version") or "")
                if observed_profile != pinned_profile:
                    raise RuntimeError("school_profile_changed_during_step")

    def _has_current_account(self, binding: Dict[str, Any]) -> bool:
        if (binding.get("principal_source") != "account"
                or not binding.get("principal_id")
                or type(binding.get("principal_auth_epoch")) is not int):
            return False
        return self._verify_account_row(binding)

    def _verify_account_row(self, binding: Dict[str, Any]) -> bool:
        from ..db.mysql_client import get_mysql_client

        row = get_mysql_client().fetch_one(
            "SELECT id,status,auth_epoch FROM user_account WHERE id=:id LIMIT 1",
            {"id": binding["principal_id"]},
        )
        return bool(
            row
            and row.get("id") == binding["principal_id"]
            and row.get("status") == "active"
            and type(row.get("auth_epoch")) is int
            and row["auth_epoch"] == binding["principal_auth_epoch"]
        )

    def _claim_and_account_current(self, run_id: str, claim_token: str) -> bool:
        if not self.dispatch_store.claim_is_current(run_id, claim_token):
            return False
        try:
            binding = self.dispatch_store.get_claim_binding(run_id, claim_token)
            return bool(binding and self._verify_account_row(binding))
        except Exception:
            return False

    def _renew_or_stop(self, run_id: str, claim_token: str) -> None:
        if not self.dispatch_store.renew_claim(run_id, claim_token=claim_token):
            raise AgentRunConflict("worker claim could not be renewed")

    @contextmanager
    def _durable_checkpointer(self) -> Iterator[Any]:
        """Open the existing MySQL saver directly and fail closed on fallback.

        The saver schema is an independent LangGraph concern. ``setup`` is the
        upstream idempotent checkpoint bootstrap; no app migration or global
        MemorySaver fallback is used here. This executor deliberately does not
        run the repo-wide collation ALTER helper on a shared application DB.
        """
        from ...services.langgraph_agent import _mysql_checkpoint_uri
        from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

        manager = PyMySQLSaver.from_conn_string(_mysql_checkpoint_uri())
        with manager as saver:
            if not isinstance(saver, PyMySQLSaver):
                raise RuntimeError("LangGraph MySQL checkpointer is unavailable")
            saver.setup()
            yield saver


def _base_runtime_release() -> Dict[str, Any]:
    from .context_epoch import ContextEpoch
    from .skill_registry import skill_cards
    from .model_router import route_model
    from config.integration_loader import get_failover_order, get_provider_config

    epoch = ContextEpoch.current().to_dict()
    epoch.pop("created_at", None)
    providers = []
    for provider in get_failover_order():
        config = get_provider_config(provider) or {}
        base_url = str(config.get("base_url") or "")
        providers.append({
            "provider": str(provider),
            "model": str(config.get("model") or ""),
            "base_url_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
        })
    return {
        "executor_contract": 1,
        "prompt_epoch": epoch,
        "routes": {
            "lead_classify": route_model("lead_classify"),
            "ask_answer": route_model("ask_answer"),
        },
        "providers": providers,
        "skill_cards": skill_cards(),
    }


def _capability_skill_registry_id(capability) -> Optional[str]:
    """Resolve allowlist scope without mutating already-pinned v4 Skill rules."""
    if capability is None:
        return None
    if capability.execution_pipeline == "education_screening_structured_v5":
        return "education-screening-structured-v4"
    return capability.capability_id


def _runtime_release(steps: list[dict], student_id: str, *, capability=None,
                     interaction_mode: Optional[str] = None) -> Dict[str, Any]:
    from .skill_registry import load_skill

    selected = {}
    for step in steps:
        for skill_id in step.get("skill_ids") or []:
            skill_capability_id = _capability_skill_registry_id(capability)
            if (capability is not None
                    and capability.execution_pipeline == "education_screening_structured_v5"
                    and skill_id == "education-screening-context"):
                # The v5 ID reuses the already released v4 Skill allowlist
                # without editing that registry and invalidating old releases.
                skill_capability_id = "education-screening-structured-v4"
            loaded = load_skill(
                skill_id, role=step["role"],
                **({"capability_id": skill_capability_id}
                   if skill_id != "school-context" else {}),
            )
            selected[skill_id] = {
                "skill_id": loaded.skill_id,
                "version": loaded.version,
                "sha256": loaded.sha256,
            }
    release: Dict[str, Any] = {
        "base": _base_runtime_release(),
        "skills": [selected[key] for key in sorted(selected)],
        "skill_sources": {},
        "capability_binding": (
            capability.manifest() if capability is not None else None
        ),
    }
    if capability is not None and capability.execution_pipeline == 'career_exploration_companion_v1':
        from ..career_exploration_analysis import release_contract
        release['exploration_analysis'] = release_contract()
    if capability is not None and capability.execution_pipeline == 'student_task_v1':
        from .student_task import release_contract
        release['student_task'] = release_contract()
    if "school-context" in selected:
        from .school_facts import _load_source, read_owner_school_context
        from ..db.mysql_client import get_mysql_client

        _source, digest = _load_source()
        profile = read_owner_school_context(get_mysql_client(), student_id=student_id)
        source_facts = profile.get("institution_facts") or []
        source_fact = next((item for item in source_facts if isinstance(item, dict)), {})
        release["skill_sources"]["school-context"] = {
            "source_data_sha256": digest,
            "source_id": str(source_fact.get("source_id") or "moe-211-schools-2005"),
            "knowledge_sha256": str(source_fact.get("source_data_sha256") or ""),
            "profile_version": str(profile.get("profile_version") or ""),
            "profile_status": str(profile.get("status") or "unavailable"),
        }
        selected["school-context"].update({
            "knowledge_sha256": str(source_fact.get("source_data_sha256") or ""),
            "profile_version": str(profile.get("profile_version") or ""),
        })
        release["skills"] = [selected[key] for key in sorted(selected)]
    if (capability is not None
            and capability.execution_pipeline == "education_screening_v1"):
        from .education_screening import education_screening_release_contract

        release["education_pipeline"] = education_screening_release_contract(
            interaction_mode or "experts",
        )
    elif (capability is not None
            and capability.execution_pipeline == "education_screening_structured_v2"):
        from .education_screening_structured_v2 import (
            education_screening_structured_release_contract,
        )

        release["education_screening_structured_v2"] = (
            education_screening_structured_release_contract(interaction_mode or "experts")
        )
    elif (capability is not None
            and capability.execution_pipeline == "education_screening_structured_v3"):
        from .education_screening_structured_v3 import (
            education_screening_structured_release_contract,
        )

        release["education_screening_structured_v3"] = (
            education_screening_structured_release_contract(interaction_mode or "experts")
        )
    elif (capability is not None
            and capability.execution_pipeline == "education_screening_structured_v4"):
        from .education_screening_structured_v4 import (
            education_screening_structured_release_contract,
        )

        release["education_screening_structured_v4"] = (
            education_screening_structured_release_contract(interaction_mode or "experts")
        )
    elif (capability is not None
            and capability.execution_pipeline == "education_screening_structured_v5"):
        from .education_screening_structured_v5 import (
            education_screening_structured_v5_release_contract,
        )

        release["education_screening_structured_v5"] = (
            education_screening_structured_v5_release_contract(interaction_mode or "experts")
        )
    release["release_sha256"] = _json_sha256(release)
    return release


def _capability_switch_reply() -> str:
    return (
        "本轮所选职业规划顾问能力包只开放只读问答与院校背景语境，"
        "不执行画像、匹配、报告、学习计划保存或其他正式写入。"
        "当前请求不能由此能力包安全完成；请切换普通 Agent 或适用的能力包后重试。"
    )


def _controlled_lead_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Keep a bounded normalized Lead decision for owner-bound plan audit.

    This is a controlled decision packet, not a source of verified facts. Raw
    model output and free-form reply text are deliberately not persisted here.
    """
    steps = decision.get("steps")
    if not isinstance(steps, list):
        steps = []
    return {
        "schema_version": 1,
        "source": str(decision.get("source") or "unknown")[:32],
        "action": str(decision.get("action") or "")[:32],
        "capability_fit": str(decision.get("capability_fit") or "")[:32],
        "user_job": str(decision.get("user_job") or "")[:32],
        "template": str(decision.get("template") or "")[:64],
        "goal_summary": str(decision.get("goal_summary") or "")[:1000],
        "vagueness": str(decision.get("vagueness") or "")[:32],
        "missing_slots": [str(item)[:64] for item in decision.get("missing_slots", [])[:8]
                          if isinstance(item, (str, int, float, bool))],
        "steps": [
            {
                "role": str(step.get("role") or "")[:32],
                "goal": str(step.get("goal") or "")[:400],
                "skill_ids": [str(item)[:64] for item in step.get("skill_ids", [])[:8]
                              if isinstance(item, str)],
            }
            for step in steps[:6] if isinstance(step, dict)
        ],
    }


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_thread_id(
    run_id: str,
    student_id: str,
    *,
    contract_revision: Optional[int] = None,
) -> str:
    """Keep prior revision checkpoints immutable and start a fresh graph per replan.

    Revision 1 retains the legacy identifier so in-flight and resumable runs
    created before this change continue using their existing checkpoint. A
    later user-approved contract revision gets a distinct checkpoint thread;
    the old completed graph state therefore cannot short-circuit the new plan.
    """
    if contract_revision is not None and (
        type(contract_revision) is not int or contract_revision < 1
    ):
        raise ValueError("contract_revision must be a positive integer")
    scope = student_id + "\0" + run_id
    if contract_revision is not None and contract_revision > 1:
        scope += "\0contract_revision\0" + str(contract_revision)
    return "agent_run_" + hashlib.sha256(
        scope.encode("utf-8"),
    ).hexdigest()


def _task_planning_request(message: str, contract: Optional[TaskContract]) -> str:
    """Give Lead the current user contract without implying write authority."""
    if contract is None:
        return str(message)
    lines = [
        "按下面的当前任务契约规划近期、可验证的只读步骤。不要扩大目标，"
        "不要推断任何未明确授权的写入或外部动作。",
        f"原始请求：{str(message)[:4000]}",
        f"当前目标（契约版本 {contract.revision}）：{contract.goal}",
        "交付物：" + "；".join(contract.deliverables),
        "最新约束：" + ("；".join(contract.constraints) or "无"),
        "验收条件：" + "；".join(contract.acceptance_criteria),
        "本轮只规划必要的近期步骤；计划或回答不会自动授权保存、画像修改或外部发送。",
    ]
    return "\n".join(lines)[:8000]


def _safe_error_code(exc: Exception) -> str:
    candidate = exc.args[0] if len(exc.args) == 1 else None
    code = (str(candidate) if isinstance(candidate, str)
            and candidate.isascii()
            and all(char.isalnum() or char in "_.:-" for char in candidate)
            and 1 <= len(candidate) <= 64 else type(exc).__name__)
    safe = "".join(char if char.isalnum() or char in "_.:-" else "_" for char in code)
    return safe[:64] or "read_only_step_failed"


def _project_step_model_usage(ledger: Any) -> Dict[str, Any]:
    """Persist bounded provider usage without owner/session or request text.

    An empty ledger is not evidence of zero use. If the worker disappears after
    dispatch but before finish_step(), this projection is not persisted; the
    MySQL running step is instead reconciled and its usage remains unknown.
    """
    if not isinstance(ledger, dict):
        return {
            "schema_version": 1,
            "status": "unverified",
            "reason": "usage_ledger_unavailable",
            "turn_token_cap": None,
            "call_count": None,
            "reported_total_tokens": None,
            "calls": [],
        }
    raw_calls = ledger.get("calls")
    calls = raw_calls if isinstance(raw_calls, list) else []
    stop_reason = ledger.get("stop_reason")
    budget_stop = ledger.get("budget_stop")
    pre_dispatch_budget_stop = (
        isinstance(budget_stop, dict)
        and budget_stop.get("reason") in _TASK_BUDGET_STOP_REASONS
        and budget_stop.get("reason") == stop_reason
    )
    allowed_reasons = {
        "turn_token_usage_unverified", "turn_token_cap_exceeded",
        "model_output_truncated", "turn_token_usage_unavailable",
    } | _TASK_BUDGET_STOP_REASONS
    reason = stop_reason if stop_reason in allowed_reasons else None
    if not calls and reason is None:
        reason = "no_model_calls_observed"
    projected_calls = []
    all_usage_valid = (bool(calls) or pre_dispatch_budget_stop) and len(calls) <= 32
    total = 0
    missing_usage = False
    for item in calls[:32]:
        if not isinstance(item, dict):
            all_usage_valid = False
            continue
        usage = item.get("usage")
        valid_usage = isinstance(usage, dict) and all(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        if not valid_usage:
            all_usage_valid = False
            missing_usage = True
            reason = reason or "provider_usage_missing"
            projected_usage = None
        else:
            projected_usage = {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            total += usage["total_tokens"]
        finish_reason = item.get("finish_reason")
        if finish_reason == "length":
            all_usage_valid = False
            reason = reason or "model_output_truncated"
        projected_calls.append({
            "index": len(projected_calls) + 1,
            "stage": str(item.get("stage") or "")[:64],
            "provider": str(item.get("provider") or "")[:48],
            "model": str(item.get("model") or "")[:128],
            "usage": projected_usage,
            "finish_reason": str(finish_reason or "")[:32],
            "preflight_reason": str(
                ((item.get("preflight") or {}).get("reason")
                 if isinstance(item.get("preflight"), dict) else "") or ""
            )[:64],
        })
    if len(calls) > 32:
        all_usage_valid = False
        reason = reason or "usage_ledger_truncated"
    if missing_usage:
        reason = "provider_usage_missing"
    if not all_usage_valid and reason is None:
        reason = "provider_usage_unverified"
    status = (
        "observed" if all_usage_valid and (
            reason is None or pre_dispatch_budget_stop
        ) else "unverified"
    )
    turn_token_cap = ledger.get("turn_token_cap")
    if type(turn_token_cap) is not int or turn_token_cap < 1:
        turn_token_cap = None
        status = "unverified"
        reason = reason or "turn_token_cap_unavailable"
    if status == "observed" and turn_token_cap is not None and total > turn_token_cap:
        status = "unverified"
        reason = "turn_token_cap_exceeded"
    return {
        "schema_version": 1,
        "status": status,
        "reason": reason or "provider_usage_observed",
        "turn_token_cap": turn_token_cap,
        "call_count": len(calls),
        "reported_total_tokens": total if all_usage_valid else None,
        "calls": projected_calls,
    }


def _safe_tool_call_cards(value: Any) -> list[dict]:
    """Persist only bounded service-projected tool cards, never raw tool bodies."""
    if not isinstance(value, list):
        return []
    allowed = {
        "tool_name", "status", "summary", "error_code", "retryable",
        "attempts", "row_count", "truncated", "continuation", "kind",
        "source_id", "version",
    }
    rows = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in allowed:
            val = item.get(key)
            if isinstance(val, str):
                row[key] = val[:240]
            elif type(val) in (bool, int):
                row[key] = val
        if row:
            rows.append(row)
    return rows


def _tool_call_count(value: Any) -> int:
    """Count all observed calls even when the public card list is truncated."""
    return min(len(value), 1_000_000) if isinstance(value, list) else 0


def _phase_tool_call_count(
    ledger: Any, task_usage: Optional[Dict[str, Any]], tool_calls: Any,
) -> int:
    """Persist at least the reserved tool batch so failed batches cannot be retried free."""
    observed = _tool_call_count(tool_calls)
    if not isinstance(ledger, dict):
        return observed
    used_before = ledger.get("tool_calls_used_before")
    if type(used_before) is not int and isinstance(task_usage, dict):
        used_before = task_usage.get("tool_calls")
    reserved_total = ledger.get("tool_calls_reserved")
    if type(used_before) is not int or type(reserved_total) is not int:
        return observed
    return max(observed, max(0, reserved_total - used_before))


def _phase_tool_call_count(
    ledger: Any, task_usage: Optional[Dict[str, Any]], tool_calls: Any,
) -> int:
    """Persist at least the reserved batch count so failed batches cannot be replayed free."""
    observed = _tool_call_count(tool_calls)
    if not isinstance(ledger, dict):
        return observed
    used_before = ledger.get("tool_calls_used_before")
    if type(used_before) is not int and isinstance(task_usage, dict):
        used_before = task_usage.get("tool_calls")
    reserved_total = ledger.get("tool_calls_reserved")
    if type(used_before) is not int or type(reserved_total) is not int:
        return observed
    return max(observed, max(0, reserved_total - used_before))


def _persist_education_conditions(value: Any) -> Dict[str, Any]:
    """Persist a small owner-bound provenance projection, never source bodies."""
    if not isinstance(value, dict):
        return {"schema_version": 1, "status": "unavailable", "conditions": []}
    allowed_condition_keys = (
        "source_id", "source_kind", "source_version", "source_sha256",
        "source_quote", "source_quote_sha256", "quote_integrity",
        "interpretation_status", "speaker_candidate", "dimension_candidate",
        "condition_type_candidate", "operator_candidate", "relation_candidate",
        "value_quote", "qualification_use", "downgraded_fields", "diagnostics",
    )
    projected_conditions = []
    for row in (value.get("conditions") or [])[:4]:
        if not isinstance(row, dict):
            continue
        quote = row.get("source_quote")
        if not isinstance(quote, str) or not quote or len(quote) > 800:
            continue
        projected = {
            key: row[key] for key in allowed_condition_keys if key in row
        }
        projected_conditions.append(projected)
    source_manifest = [
        {key: item[key] for key in (
            "source_id", "source_kind", "tool_name", "source_version",
            "source_sha256", "read_status",
        ) if key in item}
        for item in (value.get("source_manifest") or [])[:8]
        if isinstance(item, dict)
    ]
    return {
        "schema_version": 1,
        "status": str(value.get("status") or "unavailable")[:48],
        "error_code": str(value.get("error_code") or "")[:64] or None,
        "validation_failures": [
            {key: item[key] for key in ("path", "code") if key in item}
            for item in (value.get("validation_failures") or [])[:16]
            if isinstance(item, dict)
        ],
        "prompt_sha256": str(value.get("prompt_sha256") or "")[:64],
        "schema_sha256": str(value.get("schema_sha256") or "")[:64],
        "input_message_sha256": str(value.get("input_message_sha256") or "")[:64],
        "request_sources_sha256": str(value.get("request_sources_sha256") or "")[:64],
        "source_scope": str(value.get("source_scope") or "")[:120],
        "source_manifest": source_manifest,
        "skill": {
            "skill_id": str((value.get("skill") or {}).get("skill_id") or "")[:64],
            "sha256": str((value.get("skill") or {}).get("sha256") or "")[:64],
            "body_in_extractor_prompt": False,
            "purpose": "execution_skill_loaded_in_qa; hash_pins_step_release",
        },
        "provider": str(value.get("provider") or "")[:48] or None,
        "model": str(value.get("model") or "")[:128] or None,
        "finish_reason": str(value.get("finish_reason") or "")[:32] or None,
        "usage": value.get("usage") if isinstance(value.get("usage"), dict) else None,
        "usage_status": str(value.get("usage_status") or "unverified")[:32],
        "conditions": projected_conditions,
        "omitted_condition_count": max(
            0, len(value.get("conditions") or []) - len(projected_conditions),
        ),
        "redacted_row_diagnostics": [
            item for item in (value.get("redacted_row_diagnostics") or [])[:4]
            if isinstance(item, dict)
        ],
    }


def _persist_education_screening_v2(value: Any) -> Dict[str, Any]:
    """Persist only the structured candidate projection; never raw source bodies."""
    if not isinstance(value, dict):
        return {"schema_version": 2, "status": "unavailable", "conditions": []}

    def quote_ref(row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        quote = row.get("source_quote")
        if not isinstance(quote, str) or not quote or len(quote) > 300:
            return None
        return {
            key: row[key] for key in (
                "source_id", "source_kind", "source_version", "source_sha256",
                "source_quote", "source_quote_sha256", "quote_integrity",
                "interpretation_status",
            ) if key in row
        }

    allowed_condition_keys = (
        "source_id", "source_kind", "source_version", "source_sha256",
        "source_quote", "source_quote_sha256", "quote_integrity",
        "interpretation_status", "speaker_candidate", "dimension_candidate",
        "condition_type_candidate", "operator_candidate", "relation_candidate",
        "value_quote", "qualification_use", "downgraded_fields", "diagnostics",
    )
    conditions = []
    for row in (value.get("conditions") or [])[:8]:
        if not isinstance(row, dict):
            continue
        quote = row.get("source_quote")
        if not isinstance(quote, str) or not quote or len(quote) > 800:
            continue
        conditions.append({key: row[key] for key in allowed_condition_keys if key in row})
    situation = value.get("situation") if isinstance(value.get("situation"), dict) else {}
    safe_situation = {
        "purpose_candidate": str(situation.get("purpose_candidate") or "unclear")[:48],
        "target_role": quote_ref(situation.get("target_role")),
        "employer_scope": quote_ref(situation.get("employer_scope")),
        "recruitment_scope": quote_ref(situation.get("recruitment_scope")),
    }
    market = value.get("market_context") if isinstance(value.get("market_context"), dict) else {}
    return {
        "schema_version": 2,
        "status": str(value.get("status") or "unavailable")[:48],
        "error_code": str(value.get("error_code") or "")[:64] or None,
        "validation_failures": [
            {key: item[key] for key in ("path", "code") if key in item}
            for item in (value.get("validation_failures") or [])[:16]
            if isinstance(item, dict)
        ],
        "prompt_sha256": str(value.get("prompt_sha256") or "")[:64],
        "schema_sha256": str(value.get("schema_sha256") or "")[:64],
        "input_message_sha256": str(value.get("input_message_sha256") or "")[:64],
        "request_sources_sha256": str(value.get("request_sources_sha256") or "")[:64],
        "source_scope": str(value.get("source_scope") or "")[:120],
        "source_manifest": [
            {key: row[key] for key in (
                "source_id", "source_kind", "tool_name", "source_version",
                "source_sha256", "read_status",
            ) if key in row}
            for row in (value.get("source_manifest") or [])[:8]
            if isinstance(row, dict)
        ],
        "skill_assets": value.get("skill_assets") if isinstance(value.get("skill_assets"), dict) else {},
        "provider": str(value.get("provider") or "")[:48] or None,
        "model": str(value.get("model") or "")[:128] or None,
        "finish_reason": str(value.get("finish_reason") or "")[:32] or None,
        "usage": value.get("usage") if isinstance(value.get("usage"), dict) else None,
        "usage_status": str(value.get("usage_status") or "unverified")[:32],
        "candidate_response_chars": (
            min(32000, value.get("candidate_response_chars"))
            if type(value.get("candidate_response_chars")) is int
            and value.get("candidate_response_chars") >= 0 else None
        ),
        "situation": safe_situation,
        "conditions": conditions,
        "market_context": {
            "kind": str(market.get("kind") or "none")[:48],
            "scope_quote": quote_ref(market.get("scope_quote")),
            "interpretation_status": str(market.get("interpretation_status") or "not_available")[:48],
            "basis": str(market.get("basis") or "")[:96] or None,
        },
        "next_action": str(value.get("next_action") or "confirm_target_scope")[:48],
        "omitted_condition_count": max(
            0, len(value.get("conditions") or []) - len(conditions),
        ),
    }


def _persist_education_screening_v3(value: Any) -> Dict[str, Any]:
    """Persist the split v3 candidate rows without raw source bodies or drafts."""
    if not isinstance(value, dict):
        return {"schema_version": 3, "status": "unavailable", "job_conditions": [],
                "candidate_statements": []}

    def quote_ref(row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        quote = row.get("source_quote")
        if not isinstance(quote, str) or not quote or len(quote) > 300:
            return None
        return {key: row[key] for key in (
            "source_id", "source_kind", "source_version", "source_sha256",
            "source_quote", "source_quote_sha256", "quote_integrity",
            "interpretation_status",
        ) if key in row}

    condition_keys = (
        "source_id", "source_kind", "source_version", "source_sha256", "source_quote",
        "source_quote_sha256", "quote_integrity", "interpretation_status",
        "dimension_candidate", "condition_type_candidate", "operator_candidate",
        "relation_candidate", "value_quote", "downgraded_fields", "diagnostics",
        "comparison_context_complete",
    )
    statement_keys = (
        "source_id", "source_kind", "source_version", "source_sha256", "source_quote",
        "source_quote_sha256", "quote_integrity", "interpretation_status",
        "dimension_candidate", "value_quote", "downgraded_fields", "diagnostics",
    )

    def project_rows(key: str, fields: tuple[str, ...], maximum: int) -> list[dict]:
        result = []
        for row in (value.get(key) or [])[:maximum]:
            if not isinstance(row, dict):
                continue
            quote = row.get("source_quote")
            if not isinstance(quote, str) or not quote or len(quote) > 300:
                continue
            result.append({name: row[name] for name in fields if name in row})
        return result

    manifest = [
        {key: row[key] for key in (
            "source_id", "source_kind", "tool_name", "source_version",
            "source_sha256", "read_status",
        ) if key in row}
        for row in (value.get("source_manifest") or [])[:8] if isinstance(row, dict)
    ]
    return {
        "schema_version": 3,
        "status": str(value.get("status") or "unavailable")[:48],
        "error_code": str(value.get("error_code") or "")[:64] or None,
        "validation_failures": [
            {key: row[key] for key in ("path", "code") if key in row}
            for row in (value.get("validation_failures") or [])[:16] if isinstance(row, dict)
        ],
        "prompt_sha256": str(value.get("prompt_sha256") or "")[:64],
        "schema_sha256": str(value.get("schema_sha256") or "")[:64],
        "input_message_sha256": str(value.get("input_message_sha256") or "")[:64],
        "source_manifest_sha256": str(value.get("source_manifest_sha256") or "")[:64],
        "skill_assets": value.get("skill_assets") if isinstance(value.get("skill_assets"), dict) else {},
        "provider": str(value.get("provider") or "")[:48] or None,
        "model": str(value.get("model") or "")[:128] or None,
        "finish_reason": str(value.get("finish_reason") or "")[:32] or None,
        "usage": value.get("usage") if isinstance(value.get("usage"), dict) else None,
        "usage_status": str(value.get("usage_status") or "unverified")[:32],
        "purpose_candidate": str(value.get("purpose_candidate") or "unclear")[:48],
        "target_role": quote_ref(value.get("target_role")),
        "job_conditions": project_rows("job_conditions", condition_keys, 8),
        "candidate_statements": project_rows("candidate_statements", statement_keys, 8),
        "market_context_candidate": str(value.get("market_context_candidate") or "none")[:48],
        "next_action_candidate": str(value.get("next_action_candidate") or "provide_full_notice")[:48],
        "source_manifest": manifest,
    }


def _persist_education_screening_v4(value: Any, projection: Any) -> Dict[str, Any]:
    """Persist owner-bound candidate evidence and server conclusions, not raw input/drafts."""
    if not isinstance(value, dict):
        value = {}
    projection = projection if isinstance(projection, dict) else {}

    def quote_ref(row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        quote = row.get("source_quote")
        if not isinstance(quote, str) or not quote or len(quote) > 300:
            return None
        return {key: row[key] for key in (
            "id", "source_id", "source_kind", "source_version", "source_sha256",
            "source_quote", "source_quote_sha256", "quote_integrity",
            "interpretation_status",
        ) if key in row}

    def rows(key: str, fields: tuple[str, ...], maximum: int) -> list[dict]:
        result = []
        for row in (value.get(key) or [])[:maximum]:
            if not isinstance(row, dict):
                continue
            quote = row.get("source_quote")
            if not isinstance(quote, str) or not quote or len(quote) > 300:
                continue
            result.append({name: row[name] for name in fields if name in row})
        return result

    manifest = [
        {key: row[key] for key in (
            "source_id", "source_kind", "tool_name", "source_version",
            "source_sha256", "read_status",
        ) if key in row}
        for row in (value.get("source_manifest") or [])[:8] if isinstance(row, dict)
    ]
    conclusion_rows = []
    for row in (projection.get("allowed_conclusions") or [])[:8]:
        if not isinstance(row, dict):
            continue
        refs = [ref for ref in (row.get("source_refs") or [])[:4]
                if isinstance(ref, dict) and isinstance(ref.get("source_quote"), str)
                and 0 < len(ref["source_quote"]) <= 300]
        conclusion_rows.append({
            "id": str(row.get("id") or "")[:64],
            "status": str(row.get("status") or "")[:48],
            "scope": str(row.get("scope") or "")[:120],
            "statement": str(row.get("statement") or "")[:600],
            "source_refs": [{key: ref[key] for key in (
                "id", "source_id", "source_kind", "source_version", "source_quote",
                "quote_integrity", "interpretation_status",
            ) if key in ref} for ref in refs],
        })
    action = projection.get("next_action") if isinstance(projection.get("next_action"), dict) else {}
    return {
        "schema_version": 4,
        "status": str(value.get("status") or "unavailable")[:48],
        "error_code": str(value.get("error_code") or "")[:64] or None,
        "validation_failures": [
            {key: row[key] for key in ("path", "code") if key in row}
            for row in (value.get("validation_failures") or [])[:16] if isinstance(row, dict)
        ],
        "prompt_sha256": str(value.get("prompt_sha256") or "")[:64],
        "schema_sha256": str(value.get("schema_sha256") or "")[:64],
        "input_message_sha256": str(value.get("input_message_sha256") or "")[:64],
        "source_manifest_sha256": str(value.get("source_manifest_sha256") or "")[:64],
        "skill_assets": value.get("skill_assets") if isinstance(value.get("skill_assets"), dict) else {},
        "provider": str(value.get("provider") or "")[:48] or None,
        "model": str(value.get("model") or "")[:128] or None,
        "finish_reason": str(value.get("finish_reason") or "")[:32] or None,
        "usage": value.get("usage") if isinstance(value.get("usage"), dict) else None,
        "usage_status": str(value.get("usage_status") or "unverified")[:32],
        "purpose_candidate": str(value.get("purpose_candidate") or "unclear")[:48],
        "question_focus": quote_ref(value.get("question_focus")),
        "target_role": quote_ref(value.get("target_role")),
        "job_conditions": rows("job_conditions", (
            "source_id", "source_kind", "source_version", "source_sha256", "source_quote",
            "source_quote_sha256", "quote_integrity", "interpretation_status",
            "dimension_candidate", "condition_type_candidate", "operator_candidate",
            "relation_candidate", "value_quote", "downgraded_fields", "diagnostics",
            "comparison_context_complete",
        ), 8),
        "candidate_statements": rows("candidate_statements", (
            "source_id", "source_kind", "source_version", "source_sha256", "source_quote",
            "source_quote_sha256", "quote_integrity", "interpretation_status",
            "dimension_candidate", "value_quote", "downgraded_fields", "diagnostics",
        ), 8),
        "market_context_candidate": str(value.get("market_context_candidate") or "none")[:48],
        "next_action_candidate": str(value.get("next_action_candidate") or "none")[:48],
        "source_manifest": manifest,
        "projection": {
            "schema_version": 1,
            "overall_qualification": "not_determined",
            "question_focus": quote_ref(projection.get("question_focus")),
            "target_role": quote_ref(projection.get("target_role")),
            "allowed_conclusions": conclusion_rows,
            "market_context": str(projection.get("market_context") or "none")[:64],
            "next_action": {
                "id": str(action.get("id") or "")[:64],
                "text": str(action.get("text") or "")[:320],
            } if action else None,
            "claim_map": {
                str(key)[:64]: {"status": str((row or {}).get("status") or "")[:48],
                                "scope": str((row or {}).get("scope") or "")[:120]}
                for key, row in list((projection.get("claim_map") or {}).items())[:8]
                if isinstance(row, dict)
            },
        },
    }


def _safe_education_v5_finalizer(value: Any) -> Dict[str, Any]:
    """Persist only bounded selector ids and provider accounting metadata."""
    row = value if isinstance(value, dict) else {}
    selector = row.get("selector") if isinstance(row.get("selector"), dict) else {}
    allowed_decisions = {"server_rendered_from_selection", "server_fallback"}
    allowed_reasons = {
        "runtime_release_changed", "selector_contract_unavailable", "selector_contract_changed",
        "step_usage_or_route_unavailable", "selector_context_over_limit", "selector_context_invalid",
        "selector_call_unavailable", "selector_usage_unverified", "selector_response_incomplete",
        "selector_response_invalid", "selector_schema_invalid", "selector_selection_invalid",
        "analysis_unavailable", "user_message_over_limit",
    }
    ids = selector.get("selected_conclusion_ids")
    if not isinstance(ids, list) or len(ids) > 3 or any(
        not isinstance(item, str) or len(item) > 64 for item in ids
    ):
        ids = []
    usage = selector.get("usage")
    safe_usage = None
    if isinstance(usage, dict) and all(
        type(usage.get(key)) is int and usage[key] >= 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        safe_usage = {key: usage[key] for key in (
            "prompt_tokens", "completion_tokens", "total_tokens",
        )}
    return {
        "decision_status": row.get("decision_status") if row.get("decision_status") in allowed_decisions
        else "server_fallback",
        "decision_reason": row.get("decision_reason")
        if row.get("decision_reason") in allowed_reasons else None,
        "selector": {
            key: selector.get(key) for key in (
                "provider", "model", "finish_reason", "usage_status", "prompt_sha256",
                "schema_sha256", "context_sha256", "route_model",
            ) if key in selector
        } | {
            "usage": safe_usage,
            "selected_conclusion_ids": ids,
            "action_id": selector.get("action_id") if selector.get("action_id") in {
                "confirm_candidate_scope", "provide_complete_clause", "compare_required_evidence",
                "provide_full_notice", "provide_target_notice",
            } else None,
            "style": selector.get("style") if selector.get("style") in {"brief", "contextual"}
            else None,
        },
    }


def _safe_education_v2_finalizer(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"decision_status": "server_fallback", "decision_reason": "finalizer_result_unavailable"}
    finalizer = value.get("finalizer") if isinstance(value.get("finalizer"), dict) else {}
    gate = finalizer.get("grounding_gate")
    safe_gate = None
    if isinstance(gate, dict):
        allowed_reason_codes = {
            "unsupported_initial_draft", "unsupported_after_revision",
            "review_unavailable", "unsupported_response_format",
        }
        reason_code = gate.get("reason_code")
        verdict = gate.get("review_verdict")
        safe_gate = {
            "status": str(gate.get("status") or "unavailable")[:32],
            "reason_code": reason_code if isinstance(reason_code, str)
            and reason_code in allowed_reason_codes else None,
            "checks": min(2, max(0, gate.get("checks")))
            if type(gate.get("checks")) is int else 0,
            "review_verdict": verdict if isinstance(verdict, str)
            and verdict in {"supported", "unsupported"} else None,
            "review_reason_count": min(12, max(0, gate.get("review_reason_count")))
            if type(gate.get("review_reason_count")) is int else 0,
            "review_reason_chars": min(4096, max(0, gate.get("review_reason_chars")))
            if type(gate.get("review_reason_chars")) is int else 0,
            "corrected_reply_chars": min(6000, max(0, gate.get("corrected_reply_chars")))
            if type(gate.get("corrected_reply_chars")) is int else 0,
        }
    safe_finalizer = {
        key: finalizer.get(key)
        for key in (
            "provider", "model", "finish_reason", "usage", "usage_status",
            "prompt_bundle_sha256", "instruction_sha256", "user_message_sha256",
            "route_model", "draft_chars",
        ) if key in finalizer
    }
    if safe_gate is not None:
        safe_finalizer["grounding_gate"] = safe_gate
    return {
        "decision_status": str(value.get("decision_status") or "unavailable")[:48],
        "decision_reason": str(value.get("decision_reason") or "")[:64] or None,
        "gate_status": str(value.get("gate_status") or "not_run")[:48],
        "gate_checks": int(value.get("gate_checks") or 0),
        "finalizer": safe_finalizer,
    }


def _safe_education_finalizer(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "server_fallback", "reason": "finalizer_result_unavailable"}
    return {
        key: value.get(key)
        for key in (
            "decision_status", "decision_reason", "provider", "model",
            "finish_reason", "usage", "usage_status", "schema_sha256",
            "prompt_bundle_sha256", "instruction_sha256", "route_model",
            "pipeline_release_sha256",
        )
        if key in value
    }
