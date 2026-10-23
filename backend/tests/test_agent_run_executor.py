"""Focused tests for the durable executor's safe boundary and result shape."""

import pytest

from app.services.agent_runtime.agent_run_executor import AgentRunExecutor


def _fixture_preflight():
    from app.services.agent_runtime.budget import ContextWindowStatus

    return ContextWindowStatus(
        provider="openai", model="gpt-4o-mini",
        context_window_tokens=32768, input_limit_tokens=32000,
        estimated_input_tokens=10, remaining_input_tokens=31990,
        requested_output_tokens=64, fits=True,
        estimate_method="fixture", reason="fixture_preflight",
    )


class _Repository:
    def __init__(self):
        self.started = []
        self.finished = []
        self.failed = []

    def start_step(self, run_id, **kwargs):
        self.started.append((run_id, kwargs))

    def finish_step(self, run_id, **kwargs):
        self.finished.append((run_id, kwargs))

    def fail_run(self, run_id, **kwargs):
        self.failed.append((run_id, kwargs))
        return {"run_id": run_id, "status": "failed"}


class _Dispatch:
    def __init__(self):
        self.renewed = []

    def renew_claim(self, run_id, *, claim_token):
        self.renewed.append((run_id, claim_token))
        return True


def _plan():
    return {
        "plan_version": 1,
        "goal_summary": "只读分析用户背景",
        "metadata": {"runtime_release": {"release_sha256": "pinned"}},
        "steps": [{
            "step_index": 1,
            "role": "qa",
            "goal": "指出关键短板并给出建议",
            "skill_ids": [],
            "status": "pending",
        }],
    }


def test_executor_step_uses_durable_session_and_keeps_narrative_unverified(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )

    repository = _Repository()
    dispatch = _Dispatch()
    executor = AgentRunExecutor(repository, dispatch)
    seen = {}

    def invoke(packet, board, session_id, **kwargs):
        seen["session_id"] = session_id
        seen["owner"] = kwargs["student_id"]
        seen["tool_allowlist"] = kwargs["plan_tool_allowlist"]
        seen["allow_artifact_commit"] = kwargs["allow_artifact_commit"]
        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="executor-fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=_fixture_preflight(),
            finish_reason="stop",
        )
        return {
            "reply": "这是模型生成的候选建议，不是服务端核验事实。",
            "tool_calls": [],
            "skill_versions": [],
        }

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)

    done = executor._execute_step(
        "run_" + "1" * 32,
        "owner_a",
        "2" * 32,
        {"message": "请分析", "interaction_mode": "agent"},
        _plan(),
        _plan()["steps"][0],
        board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(),
        session_id="session_from_mysql_run",
    )

    assert done is False, repository.failed
    assert seen == {
        "session_id": "session_from_mysql_run",
        "owner": "owner_a",
        "tool_allowlist": {"career_data_read", "graph_query", "db_query"},
        "allow_artifact_commit": False,
    }
    assert repository.started[0][1]["claim_token"] == "2" * 32
    result = repository.finished[0][1]["result"]
    assert result["reply_candidate_unverified"].startswith("这是模型生成")
    assert result["verified_facts"] == []
    assert result["verified_updates"] == {}
    assert result["model_usage"]["status"] == "observed"
    assert result["model_usage"]["reported_total_tokens"] == 15
    assert result["model_usage"]["calls"][0]["provider"] == "openai"
    assert result["model_usage"]["calls"][0]["model"] == "gpt-4o-mini"
    assert "student_id" not in str(result["model_usage"])
    assert "session_id" not in str(result["model_usage"])
    assert "summary" not in result


def test_persistent_qa_tool_allowlist_excludes_writes_and_unbound_resources():
    from app.services.agent_runtime.agent_run_executor import _RUN_QA_TOOL_ALLOWLIST

    assert _RUN_QA_TOOL_ALLOWLIST == {
        "career_data_read", "graph_query", "db_query",
    }
    assert "normalize_entity" not in _RUN_QA_TOOL_ALLOWLIST
    assert "semantic_search" not in _RUN_QA_TOOL_ALLOWLIST
    assert "attachment_read" not in _RUN_QA_TOOL_ALLOWLIST
    assert "pipeline_run" not in _RUN_QA_TOOL_ALLOWLIST


def test_incomplete_step_persists_only_redacted_tool_cards(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.agent_run_executor import _safe_tool_call_cards

    repository = _Repository()
    executor = AgentRunExecutor(repository, _Dispatch())

    def invoke(*_args, **_kwargs):
        from app.services.agent_runtime.budget import (
            record_model_call, require_turn_token_capacity,
        )

        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="executor-fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=_fixture_preflight(),
            finish_reason="stop",
        )
        return {
            "reply": "只读查询未完成。",
            "incomplete": True,
            "stop_reason": "tool_failed",
            "skill_versions": [],
            "tool_calls": [{
                "tool_name": "career_data_read", "status": "error",
                "error_code": "tool_execution_error", "retryable": False,
                "summary": "读取失败", "raw_result": "secret row contents",
                "parameters": {"student_id": "owner_a"},
            }],
        }

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)
    done = executor._execute_step(
        "run_" + "5" * 32, "owner_a", "6" * 32,
        {"message": "请只读检查", "interaction_mode": "agent"},
        _plan(), _plan()["steps"][0],
        board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(),
        session_id="session_read_only",
    )

    assert done is True
    result = repository.finished[0][1]["result"]
    assert result["tool_calls"] == [{
        "tool_name": "career_data_read", "status": "error",
        "error_code": "tool_execution_error", "retryable": False,
        "summary": "读取失败",
    }]
    assert "secret row contents" not in str(result)
    assert "parameters" not in result["tool_calls"][0]
    assert result["model_usage"]["status"] == "observed"
    assert _safe_tool_call_cards([{"tool_name": "a", "summary": "x" * 1000}])[0]["summary"] == "x" * 240
    assert len(repository.started) == 1
    assert repository.failed[0][1]["error_code"] == "read_only_answer_incomplete"


def test_executor_missing_provider_usage_fails_and_persists_unverified_not_zero(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )

    repository = _Repository()
    executor = AgentRunExecutor(repository, _Dispatch())
    attempts = []

    def invoke(*_args, **_kwargs):
        attempts.append(1)
        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="executor-fixture",
            usage=None,
            preflight=_fixture_preflight(),
            finish_reason="provider_error",
        )
        raise RuntimeError("turn_token_usage_unverified")

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)

    done = executor._execute_step(
        "run_" + "7" * 32, "owner_private", "8" * 32,
        {"message": "请只读检查", "interaction_mode": "agent"},
        _plan(), _plan()["steps"][0],
        board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(),
        session_id="session_private",
    )

    assert done is True
    assert attempts == [1]
    result = repository.finished[0][1]["result"]
    assert result["model_usage"]["status"] == "unverified"
    assert result["model_usage"]["reason"] == "provider_usage_missing"
    assert result["model_usage"]["reported_total_tokens"] is None
    assert result["model_usage"]["calls"][0]["usage"] is None
    assert "owner_private" not in str(result["model_usage"])
    assert "session_private" not in str(result["model_usage"])


def test_executor_over_cap_provider_usage_fails_before_persisting_answer(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )

    monkeypatch.setenv("CPA_AGENT_TURN_TOKEN_CAP", "20")
    repository = _Repository()
    executor = AgentRunExecutor(repository, _Dispatch())

    def invoke(*_args, **_kwargs):
        require_turn_token_capacity(10, estimated_input_tokens=8)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="executor-fixture",
            usage={"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            preflight=_fixture_preflight(),
            finish_reason="stop",
        )
        return {"reply": "不可作为交付答复", "skill_versions": [], "tool_calls": []}

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)

    done = executor._execute_step(
        "run_" + "9" * 32, "owner_private", "a" * 32,
        {"message": "请只读检查", "interaction_mode": "agent"},
        _plan(), _plan()["steps"][0],
        board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(),
        session_id="session_private",
    )

    assert done is True
    result = repository.finished[0][1]["result"]
    assert result["reply_candidate_unverified"] == ""
    assert result["model_usage"]["status"] == "unverified"
    assert result["model_usage"]["reason"] == "turn_token_cap_exceeded"
    assert result["model_usage"]["reported_total_tokens"] == 22


def test_executor_rejects_explicit_capability_without_silent_fallback(monkeypatch):
    run_id = "run_" + "3" * 32
    claim_token = "4" * 32

    class Repository(_Repository):
        def get_for_owner(self, requested_run_id, *, student_id):
            assert requested_run_id == run_id
            assert student_id == "owner_a"
            return {
                "run_id": run_id,
                "session_id": "session_a",
                "status": "queued",
                "current_plan_version": 0,
                "cancel_requested": False,
            }

        def get_input_snapshot_for_owner(self, requested_run_id, *, student_id):
            assert requested_run_id == run_id
            assert student_id == "owner_a"
            return {
                "message": "请使用职业规划顾问",
                "interaction_mode": "experts",
                # Historical names are not aliases for the new explicit API.
                "expert_id": "career_advisor",
                "capability_id": "career_planning",
                "attachment_ids": [],
            }

    class Dispatch:
        def claim_is_current(self, requested_run_id, token):
            assert requested_run_id == run_id
            assert token == claim_token
            return True

        def get_claim_binding(self, requested_run_id, token):
            assert requested_run_id == run_id
            assert token == claim_token
            return {"student_id": "owner_a", "principal_id": "acct_a",
                    "principal_source": "account", "principal_auth_epoch": 0}

    repository = Repository()
    executor = AgentRunExecutor(repository, Dispatch())
    monkeypatch.setattr(executor, "_renew_or_stop", lambda *_args: None)
    monkeypatch.setattr(executor, "_has_current_account", lambda _binding: True)
    monkeypatch.setattr(
        executor,
        "_plan",
        lambda *_args: pytest.fail("selected capability must not be ignored"),
    )

    done = executor._advance_once(run_id, "owner_a", claim_token)

    assert done is True
    assert repository.failed[0][1]["error_code"] == "capability_selection_invalid"
    assert "不在当前服务端白名单" in repository.failed[0][1]["reply"]


def test_selected_capability_is_given_to_lead_and_validated_plan_persisted(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    snapshot = {
        "message": "我该如何准备校招？",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-planning",
        "attachment_ids": [],
    }
    capability = resolve_explicit_capability(snapshot)
    assert capability is not None
    manifest = capability.manifest()
    release = {
        "base": {"prompt_epoch": "test-v1"},
        "skills": [{"skill_id": "school-context", "version": "1.0.2", "sha256": "skill-hash"}],
        "capability_binding": manifest,
        "release_sha256": "release-hash",
    }

    class Repository(_Repository):
        def __init__(self):
            super().__init__()
            self.persisted = None

        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

        def persist_plan(self, *_args, **kwargs):
            self.persisted = kwargs
            return {"plan_version": 1}

    repository = Repository()
    executor = AgentRunExecutor(repository, _Dispatch())
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: release["base"])
    monkeypatch.setattr(
        agent_run_executor, "_runtime_release",
        lambda _steps, _owner, *, capability=None: release,
    )
    lead_seen = {}

    def decide(*_args, **kwargs):
        lead_seen["selected_capability"] = kwargs["selected_capability"]
        return {
            "action": "run_plan", "capability_fit": "supported",
            "goal_summary": "分析用户校招准备方向",
            "handoff_brief": "以用户的已确认背景为依据，给出只读建议。",
            "steps": [{
                "role": "qa", "goal": "结合用户背景整理校招准备建议",
                "skill_ids": ["school-context"],
            }],
        }

    monkeypatch.setattr(experts_lead, "experts_lead_decide", decide)

    done = executor._plan(
        "run_" + "7" * 32, "owner_a", "8" * 32,
        snapshot, {"current_plan_version": 0}, capability=capability,
    )

    assert done is False
    saved = repository.persisted
    assert saved["steps"] == [{
        "role": "qa",
        "goal": "结合用户背景整理校招准备建议",
        "skill_ids": ["school-context"],
    }]
    assert lead_seen["selected_capability"] == manifest
    assert saved["plan_metadata"]["capability_binding"] == manifest
    assert saved["plan_metadata"]["runtime_release"]["capability_binding"] == manifest
    lead_decision = saved["plan_metadata"]["lead_decision"]
    assert lead_decision == {
        "schema_version": 1,
        "source": "unknown",
        "action": "run_plan",
        "capability_fit": "supported",
        "user_job": "",
        "template": "",
        "goal_summary": "分析用户校招准备方向",
        "vagueness": "",
        "missing_slots": [],
        "steps": [{
            "role": "qa", "goal": "结合用户背景整理校招准备建议",
            "skill_ids": ["school-context"],
        }],
    }
    assert "reply" not in lead_decision


def test_executor_fails_closed_if_persisted_capability_plan_omits_skill(monkeypatch):
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    run_id = "run_" + "9" * 32
    claim_token = "a" * 32
    snapshot = {
        "message": "请给出职业建议",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-planning",
        "attachment_ids": [],
    }
    manifest = resolve_explicit_capability(snapshot).manifest()
    plan = {
        "plan_version": 1,
        "goal_summary": "职业规划顾问：只读职业规划问答",
        "metadata": {
            "capability_binding": manifest,
            "runtime_release": {"release_sha256": "pinned"},
        },
        "steps": [{
            "step_index": 1, "role": "qa", "goal": "只读职业规划问答",
            "skill_ids": [], "status": "pending",
        }],
    }

    class Repository(_Repository):
        def get_for_owner(self, requested_run_id, *, student_id):
            assert (requested_run_id, student_id) == (run_id, "owner_a")
            return {
                "run_id": run_id, "session_id": "session_a", "status": "running",
                "current_plan_version": 1, "current_plan": plan,
                "cancel_requested": False,
            }

        def get_input_snapshot_for_owner(self, requested_run_id, *, student_id):
            assert (requested_run_id, student_id) == (run_id, "owner_a")
            return snapshot

    class Dispatch:
        def claim_is_current(self, requested_run_id, token):
            return requested_run_id == run_id and token == claim_token

        def get_claim_binding(self, requested_run_id, token):
            return {"student_id": "owner_a", "principal_id": "acct_a",
                    "principal_source": "account", "principal_auth_epoch": 0}

    repository = Repository()
    executor = AgentRunExecutor(repository, Dispatch())
    monkeypatch.setattr(executor, "_renew_or_stop", lambda *_args: None)
    monkeypatch.setattr(executor, "_has_current_account", lambda _binding: True)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)

    done = executor._advance_once(run_id, "owner_a", claim_token)

    assert done is True
    assert repository.failed[0][1]["error_code"] == "capability_plan_mismatch"
    assert "只开放只读问答" in repository.failed[0][1]["reply"]
    assert "请切换" in repository.failed[0][1]["reply"]


def test_lead_marked_unsupported_capability_returns_explicit_switch_request(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    class Repository(_Repository):
        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

    repository = Repository()
    executor = AgentRunExecutor(repository, _Dispatch())
    snapshot = {
        "message": "请生成正式岗位匹配报告",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-planning",
        "attachment_ids": [],
    }
    capability = resolve_explicit_capability(snapshot)
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: {"epoch": "stable"})
    monkeypatch.setattr(experts_lead, "experts_lead_decide", lambda *_args, **kwargs: {
        "action": "clarify", "capability_fit": "unsupported",
        "reply": "该包不适用。", "steps": [],
    })

    done = executor._plan(
        "run_" + "b" * 32, "owner_a", "c" * 32,
        snapshot, {"current_plan_version": 0}, capability=capability,
    )

    assert done is True
    assert repository.failed[0][1]["error_code"] == "capability_not_applicable"
    assert "请切换普通 Agent 或适用的能力包" in repository.failed[0][1]["reply"]


def test_capability_and_skill_release_versions_are_pinned(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, expert_capabilities, school_facts
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    import app.services.db.mysql_client as mysql_client

    selection = {
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-planning",
    }
    selected = resolve_explicit_capability(selection)
    steps = [{
        "role": "qa", "goal": "结合用户背景解释学校语境",
        "skill_ids": ["school-context"],
    }]
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: {"prompt_epoch": "pinned"})
    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(
        school_facts, "_load_source",
        lambda: ({}, "official-data-hash"),
    )
    monkeypatch.setattr(
        school_facts, "read_owner_school_context",
        lambda *_args, **_kwargs: {
            "profile_version": "confirmed-resume-v3",
            "status": "matched_official_name",
            "institution_facts": [{
                "source_id": "moe-211-schools-2005",
                "source_data_sha256": "official-data-hash",
            }],
        },
    )

    class LoadedSkill:
        skill_id = "school-context"
        version = "1.0.2"
        sha256 = "skill-content-hash"

    monkeypatch.setattr(
        "app.services.agent_runtime.skill_registry.load_skill",
        lambda _skill_id, *, role: LoadedSkill(),
    )

    pinned = agent_run_executor._runtime_release(steps, "owner_a", capability=selected)
    assert pinned["capability_binding"] == selected.manifest()
    assert pinned["skills"] == [{
        "skill_id": "school-context", "version": "1.0.2",
        "sha256": "skill-content-hash", "knowledge_sha256": "official-data-hash",
        "profile_version": "confirmed-resume-v3",
    }]
    assert pinned["release_sha256"]

    updated = expert_capabilities.ExpertCapability(
        expert_id="career-coach", capability_id="career-planning",
        version="1.0.1", name="职业规划顾问", role="qa",
        skill_ids=("school-context",),
    )
    old_registry = expert_capabilities._CAPABILITIES
    monkeypatch.setattr(expert_capabilities, "_CAPABILITIES", {
        ("career-coach", "career-planning"): updated,
    })
    current_selection = resolve_explicit_capability(selection)
    executor = AgentRunExecutor(_Repository(), _Dispatch())
    assert executor._release_matches(pinned, [{
        "role": "qa", "goal": steps[0]["goal"],
        "skill_ids": ["school-context"],
    }], "owner_a", current_selection) is False
    monkeypatch.setattr(expert_capabilities, "_CAPABILITIES", old_registry)


def test_executor_requires_every_pinned_skill_to_be_loaded():
    executor = AgentRunExecutor(_Repository(), _Dispatch())
    release = {"skills": [{
        "skill_id": "school-context",
        "version": "1.0.2",
        "sha256": "skill-hash",
        "knowledge_sha256": "official-source-hash",
        "profile_version": "resume-confirmation-1",
    }]}

    with pytest.raises(RuntimeError, match="selected_skill_not_loaded"):
        executor._assert_skill_versions({"skill_versions": []}, release)

    with pytest.raises(RuntimeError, match="school_source_changed_during_step"):
        executor._assert_skill_versions({"skill_versions": [{
            "skill_id": "school-context",
            "version": "1.0.2",
            "sha256": "skill-hash",
            "knowledge_sha256": "",
            "profile_version": "resume-confirmation-1",
        }]}, release)

    with pytest.raises(RuntimeError, match="school_profile_changed_during_step"):
        executor._assert_skill_versions({"skill_versions": [{
            "skill_id": "school-context",
            "version": "1.0.2",
            "sha256": "skill-hash",
            "knowledge_sha256": "official-source-hash",
            "profile_version": "",
        }]}, release)


def test_task_budget_totals_seed_every_phase_and_survive_worker_resume():
    from app.services.agent_runtime.agent_run_executor import (
        AgentRunExecutor, _phase_tool_call_count, _project_step_model_usage,
    )
    from app.services.agent_runtime.agent_task_control import (
        TaskBudget, initial_task_contract,
    )
    from app.services.agent_runtime.budget import (
        capture_usage_ledger, require_turn_token_capacity,
        reserve_tool_call_capacity,
    )

    contract = initial_task_contract(
        "run_" + "a" * 32,
        "Create a bounded plan",
        TaskBudget(
            max_model_calls=5, max_tool_calls=4,
            max_input_tokens=1000, max_output_tokens=800,
            max_call_input_tokens=500, max_call_output_tokens=400,
            max_retries=1,
        ),
    )
    # These stand for the owner-verified aggregate after an earlier durable
    # plan/step checkpoint. Every worker phase starts from those same totals.
    durable_usage = {
        "verified": True, "model_calls": 3, "tool_calls": 2,
        "input_tokens": 180, "output_tokens": 90, "total_tokens": 270,
    }
    options = AgentRunExecutor._task_usage_ledger_options(contract, durable_usage)
    assert options == {
        "model_call_cap": 5, "tool_call_cap": 4,
        "model_calls_used": 3, "tool_calls_used": 2,
    }

    for phase in ("planning", "step", "resumed_step", "finalizing"):
        with capture_usage_ledger(
            phase, turn_token_cap=900, **options,
        ) as ledger:
            assert ledger["model_calls_used_before"] == 3
            assert ledger["tool_calls_used_before"] == 2
            assert ledger["tool_calls_reserved"] == 2
            if phase == "resumed_step":
                require_turn_token_capacity(20, estimated_input_tokens=20)
                require_turn_token_capacity(20, estimated_input_tokens=20)
                with pytest.raises(RuntimeError, match="model_call_cap_exceeded"):
                    require_turn_token_capacity(20, estimated_input_tokens=20)
    with capture_usage_ledger(
        "resumed_tool_step", turn_token_cap=900, **options,
    ) as tool_ledger:
        reserve_tool_call_capacity(2, stage="resumed_step_fixture")
        assert _phase_tool_call_count(tool_ledger, durable_usage, []) == 2
        with pytest.raises(RuntimeError, match="tool_call_cap_exceeded"):
            reserve_tool_call_capacity(1, stage="resumed_step_fixture")

    blocked_without_dispatch = _project_step_model_usage({
        "calls": [], "stop_reason": "model_call_cap_exceeded",
        "budget_stop": {"reason": "model_call_cap_exceeded"},
        "turn_token_cap": 900,
    })
    assert blocked_without_dispatch["status"] == "observed"
    assert blocked_without_dispatch["call_count"] == 0
    assert blocked_without_dispatch["reported_total_tokens"] == 0

    provider_overrun = _project_step_model_usage({
        "calls": [{
            "stage": "fixture", "provider": "openai", "model": "fixture-model",
            "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            "finish_reason": "stop",
        }],
        "stop_reason": "turn_token_cap_exceeded", "turn_token_cap": 20,
    })
    assert provider_overrun["status"] == "unverified"
    assert provider_overrun["reason"] == "turn_token_cap_exceeded"
