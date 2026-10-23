"""Pure Agent task-contract and control-message protocol.

This module deliberately has no database, HTTP, model, or tool dependencies.
The existing AgentRunRepository remains the durable authority. Callers should
apply its decisions in that repository's owner-bound transaction and keep the
task revision distinct from the run's plan version.

The protocol is a narrow adapter for task contracts and user control messages;
it is not a second runner, queue, or persistence layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


# The existing durable Agent run id is the task identity. Never substitute a
# conversation/session id or create a second task-id namespace here.
_TASK_ID = re.compile(r"^run_[a-f0-9]{32}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9_./:@+-]{1,256}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_WRITE_ACTIONS = frozenset({"artifact.save", "profile.update"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_UNCERTAIN_STEP_STATUSES = frozenset({"needs_reconciliation"})


class TaskControlError(ValueError):
    """The task contract or a control-message proposal is invalid."""


class StaleTaskRevision(TaskControlError):
    """A control message was based on a superseded contract revision."""


class StaleTaskOutput(TaskControlError):
    """An output belongs to an old task or plan revision and cannot publish."""


class ControlIntent(str, Enum):
    SUPPLEMENT = "supplement"
    REVISE_GOAL = "revise_goal"
    PROGRESS_QUERY = "progress_query"
    PAUSE = "pause"
    CANCEL = "cancel"
    NEW_TASK = "new_task"
    CLARIFY = "clarify"


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TaskControlError(f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise TaskControlError(f"{field} must contain 1..{maximum} characters")
    return value


def _text_list(value: Sequence[str], field: str, *, maximum_items: int,
               maximum_chars: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TaskControlError(f"{field} must be a list of text")
    if len(value) > maximum_items:
        raise TaskControlError(f"{field} exceeds {maximum_items} items")
    rows = tuple(_text(item, f"{field}[{index}]", maximum=maximum_chars)
                 for index, item in enumerate(value))
    if len(set(rows)) != len(rows):
        raise TaskControlError(f"{field} must not contain duplicates")
    return rows


@dataclass(frozen=True)
class TaskBudget:
    """Server-selected per-call and whole-task limits, not model suggestions."""

    max_model_calls: int
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_call_input_tokens: int
    max_call_output_tokens: int
    max_retries: int

    def __post_init__(self) -> None:
        limits = {
            "max_model_calls": (self.max_model_calls, 1, 256),
            "max_tool_calls": (self.max_tool_calls, 0, 1024),
            "max_input_tokens": (self.max_input_tokens, 1, 2_000_000),
            "max_output_tokens": (self.max_output_tokens, 1, 1_000_000),
            "max_call_input_tokens": (self.max_call_input_tokens, 1, 1_000_000),
            "max_call_output_tokens": (self.max_call_output_tokens, 1, 500_000),
            "max_retries": (self.max_retries, 0, 8),
        }
        for name, (value, lower, upper) in limits.items():
            if type(value) is not int or not lower <= value <= upper:
                raise TaskControlError(f"{name} must be an integer in {lower}..{upper}")
        if self.max_call_input_tokens > self.max_input_tokens:
            raise TaskControlError("per-call input budget cannot exceed task input budget")
        if self.max_call_output_tokens > self.max_output_tokens:
            raise TaskControlError("per-call output budget cannot exceed task output budget")


@dataclass(frozen=True)
class ReadGrant:
    """A server-verified owner-scoped reference and the version actually read."""

    reference: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not _REFERENCE.fullmatch(self.reference):
            raise TaskControlError("read grant reference is invalid")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise TaskControlError("read grant version is invalid")


@dataclass(frozen=True)
class WriteGrant:
    """A confirmation receipt bound to an exact target and contract revision."""

    action: str
    target: str
    target_version: str
    confirmation_id: str
    contract_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or self.action not in _SAFE_WRITE_ACTIONS:
            raise TaskControlError("write action is not supported by this task protocol")
        if not isinstance(self.target, str) or not _REFERENCE.fullmatch(self.target):
            raise TaskControlError("write grant target is invalid")
        if not isinstance(self.target_version, str) or not _VERSION.fullmatch(self.target_version):
            raise TaskControlError("write grant target_version is invalid")
        if not isinstance(self.confirmation_id, str) or not _REFERENCE.fullmatch(self.confirmation_id):
            raise TaskControlError("write grant must carry a confirmation receipt id")
        if type(self.contract_revision) is not int or self.contract_revision < 0:
            raise TaskControlError("write grant contract_revision must be nonnegative")


@dataclass(frozen=True)
class TaskContract:
    """Immutable, revisioned request context keyed by the durable Agent run id."""

    task_id: str
    revision: int
    goal: str
    deliverables: tuple[str, ...]
    constraints: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    reads: tuple[ReadGrant, ...]
    writes: tuple[WriteGrant, ...]
    budget: TaskBudget

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _TASK_ID.fullmatch(self.task_id):
            raise TaskControlError("task_id is invalid")
        if type(self.revision) is not int or self.revision < 0:
            raise TaskControlError("revision must be a nonnegative integer")
        object.__setattr__(self, "goal", _text(self.goal, "goal", maximum=2000))
        object.__setattr__(self, "deliverables", _text_list(
            self.deliverables, "deliverables", maximum_items=16, maximum_chars=500,
        ))
        object.__setattr__(self, "constraints", _text_list(
            self.constraints, "constraints", maximum_items=64, maximum_chars=1000,
        ))
        object.__setattr__(self, "acceptance_criteria", _text_list(
            self.acceptance_criteria, "acceptance_criteria", maximum_items=32, maximum_chars=1000,
        ))
        if not isinstance(self.budget, TaskBudget):
            raise TaskControlError("budget must be a TaskBudget selected by the server")
        if not isinstance(self.reads, (tuple, list)) or not isinstance(self.writes, (tuple, list)):
            raise TaskControlError("reads and writes must be lists of validated grants")
        object.__setattr__(self, "reads", tuple(self.reads))
        object.__setattr__(self, "writes", tuple(self.writes))
        if any(not isinstance(item, ReadGrant) for item in self.reads):
            raise TaskControlError("reads must be server-verified ReadGrant values")
        if any(not isinstance(item, WriteGrant) for item in self.writes):
            raise TaskControlError("writes must be explicit WriteGrant values")
        if any(item.contract_revision != self.revision for item in self.writes):
            raise TaskControlError("write grants must bind to this exact contract revision")
        read_keys = [(item.reference, item.version) for item in self.reads]
        if len(set(read_keys)) != len(read_keys):
            raise TaskControlError("reads must not contain duplicate reference versions")
        write_keys = [(item.action, item.target, item.target_version) for item in self.writes]
        if len(set(write_keys)) != len(write_keys):
            raise TaskControlError("writes must not contain duplicate grants")

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing = []
        if not self.deliverables:
            missing.append("deliverables")
        if not self.acceptance_criteria:
            missing.append("acceptance_criteria")
        return tuple(missing)

    def require_plan_ready(self) -> None:
        """Do not plan until key contract fields exist; persist as waiting_input."""
        if self.missing_fields:
            raise TaskControlError(
                "task contract needs user input for: " + ", ".join(self.missing_fields)
            )

    @property
    def sha256(self) -> str:
        encoded = json.dumps(_contract_record(self), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _contract_record(contract: TaskContract) -> dict[str, Any]:
    return {
        "task_id": contract.task_id,
        "revision": contract.revision,
        "goal": contract.goal,
        "deliverables": list(contract.deliverables),
        "constraints": list(contract.constraints),
        "acceptance_criteria": list(contract.acceptance_criteria),
        "reads": [item.__dict__ for item in contract.reads],
        "writes": [item.__dict__ for item in contract.writes],
        "budget": contract.budget.__dict__,
    }


def task_contract_to_record(contract: TaskContract) -> dict[str, Any]:
    """Return the canonical JSON shape used by the durable run repository."""
    if not isinstance(contract, TaskContract):
        raise TaskControlError("contract must be a validated TaskContract")
    return _contract_record(contract)


def task_contract_from_record(value: Mapping[str, Any]) -> TaskContract:
    """Decode and validate a persisted contract; JSON shape is never trusted."""
    if not isinstance(value, Mapping) or set(value) != {
        "task_id", "revision", "goal", "deliverables", "constraints",
        "acceptance_criteria", "reads", "writes", "budget",
    }:
        raise TaskControlError("persisted task contract has an invalid shape")
    budget = value["budget"]
    if not isinstance(budget, Mapping) or set(budget) != {
        "max_model_calls", "max_tool_calls", "max_input_tokens", "max_output_tokens",
        "max_call_input_tokens", "max_call_output_tokens", "max_retries",
    }:
        raise TaskControlError("persisted task budget has an invalid shape")
    reads = value["reads"]
    writes = value["writes"]
    if not isinstance(reads, list) or not isinstance(writes, list):
        raise TaskControlError("persisted task grants must be lists")
    if any(not isinstance(item, Mapping) or set(item) != {"reference", "version"}
           for item in reads):
        raise TaskControlError("persisted task read grant has an invalid shape")
    if any(not isinstance(item, Mapping) or set(item) != {
        "action", "target", "target_version", "confirmation_id", "contract_revision",
    } for item in writes):
        raise TaskControlError("persisted task write grant has an invalid shape")
    try:
        return TaskContract(
            task_id=value["task_id"],
            revision=value["revision"],
            goal=value["goal"],
            deliverables=tuple(value["deliverables"]),
            constraints=tuple(value["constraints"]),
            acceptance_criteria=tuple(value["acceptance_criteria"]),
            reads=tuple(ReadGrant(**dict(item)) for item in reads),
            writes=tuple(WriteGrant(**dict(item)) for item in writes),
            budget=TaskBudget(**dict(budget)),
        )
    except (TypeError, KeyError) as exc:
        raise TaskControlError("persisted task contract is invalid") from exc


def initial_task_contract(task_id: str, message: str, budget: TaskBudget) -> TaskContract:
    """Build a minimal, explicit task contract from a new user request.

    The exact request remains in the owner-bound input snapshot. The contract
    is a concise revisionable summary; the planner may read the full snapshot
    for revision one only. Deliverable and acceptance text are labeled as
    working assumptions rather than user-granted write authority.
    """
    if not isinstance(message, str) or not message.strip() or "\x00" in message:
        raise TaskControlError("message must be non-empty text without NUL")
    goal = _text(message[:2000], "message", maximum=2000)
    if len(message) > 2000:
        goal = goal[:1980].rstrip() + "…（完整请求保存在任务输入快照）"
    return TaskContract(
        task_id=task_id,
        revision=1,
        goal=goal,
        deliverables=("按请求形成一份可核验的结果；需要特定格式时由用户补充。",),
        constraints=(),
        acceptance_criteria=("回应任务目标；列出已核验证据，并如实标出缺口。",),
        reads=(),
        writes=(),
        budget=budget,
    )


@dataclass(frozen=True)
class ContractPatch:
    """User-authored term changes; never permissions/budget or silent deletions."""

    goal: Optional[str] = None
    deliverables: Optional[tuple[str, ...]] = None
    acceptance_criteria: Optional[tuple[str, ...]] = None
    constraint_updates: tuple[tuple[str, str], ...] = ()
    add_constraints: tuple[str, ...] = ()
    remove_constraints: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContractPatch":
        if not isinstance(value, Mapping):
            raise TaskControlError("contract patch must be an object")
        unknown = set(value) - {
            "goal", "deliverables", "acceptance_criteria", "constraint_updates",
            "add_constraints", "remove_constraints",
        }
        if unknown:
            raise TaskControlError("contract patch contains protected or unknown fields")
        if not value:
            raise TaskControlError("contract patch must change at least one field")
        updates = value.get("constraint_updates", [])
        if not isinstance(updates, list) or len(updates) > 64:
            raise TaskControlError("constraint_updates must be a bounded list")
        normalized_updates = []
        for index, row in enumerate(updates):
            if not isinstance(row, Mapping) or set(row) != {"current", "replacement"}:
                raise TaskControlError(f"constraint_updates[{index}] must identify current and replacement text")
            normalized_updates.append((
                _text(row["current"], f"constraint_updates[{index}].current", maximum=1000),
                _text(row["replacement"], f"constraint_updates[{index}].replacement", maximum=1000),
            ))
        if len({old for old, _new in normalized_updates}) != len(normalized_updates):
            raise TaskControlError("constraint_updates cannot update the same current term twice")
        return cls(
            goal=_text(value["goal"], "goal", maximum=2000) if "goal" in value else None,
            deliverables=_text_list(value["deliverables"], "deliverables", maximum_items=16,
                                    maximum_chars=500) if "deliverables" in value else None,
            acceptance_criteria=_text_list(value["acceptance_criteria"], "acceptance_criteria",
                                           maximum_items=32, maximum_chars=1000)
            if "acceptance_criteria" in value else None,
            constraint_updates=tuple(normalized_updates),
            add_constraints=_text_list(value.get("add_constraints", []), "add_constraints",
                                       maximum_items=64, maximum_chars=1000),
            remove_constraints=_text_list(value.get("remove_constraints", []), "remove_constraints",
                                          maximum_items=64, maximum_chars=1000),
        )


@dataclass(frozen=True)
class TaskControlMessage:
    intent: ControlIntent
    task_id: Optional[str]
    expected_revision: Optional[int]
    patch: Optional[ContractPatch]


def parse_control_message(value: Mapping[str, Any], *,
                          current: Optional[TaskContract]) -> TaskControlMessage:
    """Validate a classifier proposal against the current task binding.

    This does not classify natural language, grant permissions, or execute the
    proposed action. Callers must derive the mapping from the current user turn,
    authenticate its owner, and apply any mutation with a durable CAS.
    """
    if not isinstance(value, Mapping):
        raise TaskControlError("control message must be an object")
    unknown = set(value) - {"intent", "task_id", "expected_revision", "patch"}
    if unknown:
        raise TaskControlError("control message contains unknown fields")
    try:
        intent = ControlIntent(value.get("intent"))
    except (TypeError, ValueError) as exc:
        raise TaskControlError("control message intent is invalid") from exc

    if intent is ControlIntent.NEW_TASK:
        if any(key in value for key in ("task_id", "expected_revision", "patch")):
            raise TaskControlError("new_task must create a separate task without mutating the active one")
        return TaskControlMessage(intent, None, None, None)

    if current is None:
        raise TaskControlError("there is no active task to control")
    task_id = value.get("task_id")
    revision = value.get("expected_revision")
    if task_id != current.task_id:
        raise TaskControlError("control message task_id does not match the active task")
    if intent is ControlIntent.PROGRESS_QUERY:
        if revision is not None and (type(revision) is not int or revision < 0):
            raise TaskControlError("expected_revision must be a nonnegative integer when supplied")
        # A read-only progress request can safely resolve to the latest state;
        # stale revisions only block mutations and execution callbacks.
        if "patch" in value:
            raise TaskControlError("progress query cannot mutate the contract")
        return TaskControlMessage(intent, current.task_id, current.revision, None)
    if type(revision) is not int or revision < 0:
        raise TaskControlError("expected_revision must be a nonnegative integer")
    if revision != current.revision:
        raise StaleTaskRevision("control message is based on an old task revision")

    mutating = intent in {ControlIntent.SUPPLEMENT, ControlIntent.REVISE_GOAL}
    if mutating:
        if "patch" not in value:
            raise TaskControlError("task revision requires an explicit contract patch")
        patch = ContractPatch.from_mapping(value["patch"])
        if intent is ControlIntent.SUPPLEMENT and patch.goal is not None:
            raise TaskControlError("supplement cannot silently replace the task goal")
        if intent is ControlIntent.REVISE_GOAL and patch.goal is None:
            raise TaskControlError("revise_goal requires a replacement goal")
    else:
        if "patch" in value:
            raise TaskControlError("progress, pause, cancel, and clarify cannot mutate the contract")
        patch = None
    return TaskControlMessage(intent, current.task_id, revision, patch)


@dataclass(frozen=True)
class ControlDecision:
    action: str
    request_safe_point: bool = False
    preserve_completed_effects: bool = True
    allow_retry_without_reconciliation: bool = False


def decide_control(message: TaskControlMessage, *, task_status: str,
                   active_step_status: Optional[str]) -> ControlDecision:
    """Return a side-effect-free instruction for the durable run adapter."""
    if not isinstance(message, TaskControlMessage):
        raise TaskControlError("message must be a validated TaskControlMessage")
    if task_status in _TERMINAL_STATUSES:
        if message.intent is ControlIntent.CANCEL and task_status == "cancelled":
            return ControlDecision("already_cancelled")
        if message.intent is ControlIntent.PROGRESS_QUERY:
            return ControlDecision("read_final_status")
        if message.intent is ControlIntent.NEW_TASK:
            return ControlDecision("create_new_task")
        return ControlDecision("reject_terminal_task")

    if message.intent is ControlIntent.NEW_TASK:
        return ControlDecision("create_new_task")
    if message.intent is ControlIntent.PROGRESS_QUERY:
        return ControlDecision("read_progress")
    if message.intent is ControlIntent.CLARIFY:
        return ControlDecision("wait_for_user_input")
    if message.intent in {ControlIntent.SUPPLEMENT, ControlIntent.REVISE_GOAL}:
        if task_status in {"cancel_requested", "needs_reconciliation", "finalizing"}:
            return ControlDecision("wait_for_task_safe_point", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        if active_step_status in _UNCERTAIN_STEP_STATUSES:
            return ControlDecision("wait_for_step_safe_point", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        if active_step_status == "running":
            return ControlDecision("revise_contract_and_fence_active_step",
                                   request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        return ControlDecision("revise_contract_and_replan")
    if message.intent is ControlIntent.PAUSE:
        if task_status == "paused":
            return ControlDecision("already_paused")
        if task_status == "pause_requested":
            return ControlDecision("pause_already_requested", request_safe_point=True)
        if task_status == "needs_reconciliation" or active_step_status == "needs_reconciliation":
            return ControlDecision("request_pause_and_reconcile", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        return ControlDecision("request_pause_at_safe_point",
                               request_safe_point=active_step_status == "running")
    if message.intent is ControlIntent.CANCEL:
        if task_status == "cancel_requested":
            return ControlDecision("cancel_already_requested", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        if task_status == "needs_reconciliation" or active_step_status == "needs_reconciliation":
            return ControlDecision("request_cancel_and_reconcile", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        if active_step_status == "running":
            return ControlDecision("request_cancel_at_safe_point", request_safe_point=True,
                                   allow_retry_without_reconciliation=False)
        return ControlDecision("cancel_without_rollback")
    raise TaskControlError("control message intent is unsupported")


@dataclass(frozen=True)
class ContractRevision:
    contract: TaskContract
    changed: bool
    supersedes_plan_version: Optional[int]
    invalidates_prior_result: bool
    preserves_completed_steps: bool = True


def revise_contract(current: TaskContract, message: TaskControlMessage, *,
                    current_plan_version: Optional[int], task_status: str,
                    active_step_status: Optional[str]) -> ContractRevision:
    """Apply a validated user term update, producing a new immutable revision.

    Authorization and budget are never accepted from the patch. Any changed
    contract drops version-bound write grants; completed historical effects stay
    available as evidence, while pending work and prior result publication must
    be reconciled by the durable repository before the new plan runs.
    """
    if message.task_id != current.task_id or message.expected_revision is None:
        raise TaskControlError("contract revision must target the current task")
    if message.expected_revision != current.revision:
        raise StaleTaskRevision("task contract changed before this update was applied")
    decision = decide_control(message, task_status=task_status,
                              active_step_status=active_step_status)
    if decision.action not in {
        "revise_contract_and_replan", "revise_contract_and_fence_active_step",
    }:
        raise TaskControlError(f"task cannot be revised before a safe point: {decision.action}")
    patch = message.patch
    if patch is None:
        raise TaskControlError("contract revision requires a patch")
    goal = patch.goal if patch.goal is not None else current.goal
    deliverables = patch.deliverables if patch.deliverables is not None else current.deliverables
    constraints = list(current.constraints)
    removals = set(patch.remove_constraints)
    update_map = dict(patch.constraint_updates)
    if removals & set(update_map):
        raise TaskControlError("a constraint cannot be updated and removed in the same revision")
    if not removals.issubset(constraints) or not set(update_map).issubset(constraints):
        raise TaskControlError("constraint update/removal must name the exact current contract text")
    revised_constraints = []
    for item in constraints:
        if item in removals:
            continue
        revised_constraints.append(update_map.get(item, item))
    revised_constraints.extend(patch.add_constraints)
    if len(set(revised_constraints)) != len(revised_constraints):
        raise TaskControlError("constraint revision would create duplicate contract text")
    if len(revised_constraints) > 64:
        raise TaskControlError("constraints exceed 64 items")
    constraints = tuple(revised_constraints)
    acceptance = (patch.acceptance_criteria if patch.acceptance_criteria is not None
                  else current.acceptance_criteria)
    changed = (goal, deliverables, constraints, acceptance) != (
        current.goal, current.deliverables, current.constraints, current.acceptance_criteria,
    )
    if not changed:
        return ContractRevision(current, False, None, False)
    next_contract = replace(
        current,
        revision=current.revision + 1,
        goal=goal,
        deliverables=deliverables,
        constraints=constraints,
        acceptance_criteria=acceptance,
        writes=(),
    )
    if current_plan_version is not None and (
        type(current_plan_version) is not int or current_plan_version < 1
    ):
        raise TaskControlError("current_plan_version must be a positive integer or None")
    return ContractRevision(
        contract=next_contract,
        changed=True,
        supersedes_plan_version=current_plan_version,
        invalidates_prior_result=True,
    )


@dataclass(frozen=True)
class PlanBinding:
    task_id: str
    contract_revision: int
    plan_version: int


def assert_current_plan(contract: TaskContract, binding: PlanBinding, *,
                        current_plan_version: int) -> None:
    """Reject stale worker callbacks before they publish task results."""
    if not isinstance(binding, PlanBinding):
        raise StaleTaskOutput("plan output has no revision binding")
    if (binding.task_id != contract.task_id
            or binding.contract_revision != contract.revision
            or type(current_plan_version) is not int
            or binding.plan_version != current_plan_version):
        raise StaleTaskOutput("plan output belongs to an obsolete task or plan revision")


def write_is_authorized(contract: TaskContract, *, action: str,
                        target: str, target_version: str) -> bool:
    """Match an explicit grant to its exact revision, target, and version."""
    return any(
        grant.action == action
        and grant.target == target
        and grant.target_version == target_version
        and grant.contract_revision == contract.revision
        for grant in contract.writes
    )
