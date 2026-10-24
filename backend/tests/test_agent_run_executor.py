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


def test_checkpoint_thread_id_is_stable_per_contract_revision_and_legacy_compatible():
    from app.services.agent_runtime.agent_run_executor import _checkpoint_thread_id

    run_id = "run_" + "a" * 32
    student_id = "student-a"
    legacy = _checkpoint_thread_id(run_id, student_id)

    # Revision one keeps the old durable key so in-flight legacy runs resume.
    assert _checkpoint_thread_id(run_id, student_id, contract_revision=1) == legacy
    # A completed revision's end-of-graph checkpoint must not short-circuit a
    # new user-approved revision; retries within that revision remain stable.
    revision_two = _checkpoint_thread_id(run_id, student_id, contract_revision=2)
    assert revision_two != legacy
    assert revision_two == _checkpoint_thread_id(
        run_id, student_id, contract_revision=2,
    )
    assert _checkpoint_thread_id(
        run_id, student_id, contract_revision=3,
    ) != revision_two
    with pytest.raises(ValueError):
        _checkpoint_thread_id(run_id, student_id, contract_revision=0)


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


def test_planner_never_claims_bound_material_reads_when_user_forbids_them(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    class PlanningRepository(_Repository):
        def __init__(self):
            super().__init__()
            self.plan = None

        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

        def persist_plan(self, _run_id, **kwargs):
            self.plan = kwargs

    repository = PlanningRepository()
    executor = AgentRunExecutor(repository, _Dispatch())
    message = "为虚构学生解释数据库隔离级别；不联网，不读取资料，不执行操作。"
    snapshot = {
        "message": message,
        "interaction_mode": "experts",
        "expert_id": "task-coordinator",
        "capability_id": "student-task",
    }
    capability = resolve_explicit_capability(snapshot)
    stable_base = {"version": "fixture"}
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: stable_base)
    monkeypatch.setattr(agent_run_executor, "_runtime_release", lambda *_args, **_kwargs: {
        "base": stable_base, "release_sha256": "fixture", "skills": [],
    })
    monkeypatch.setattr(
        experts_lead, "experts_lead_decide",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planner must not run")),
    )

    done = executor._plan(
        "run_" + "a" * 32, "owner_a", "b" * 32, snapshot,
        {"current_plan_version": 0}, capability=capability,
    )

    assert done is False
    assert repository.plan["goal_summary"] == message
    step = repository.plan["steps"][0]
    assert step["role"] == "qa"
    from app.services.agent_runtime.resource_access_policy import no_read_safe_goal
    assert step["goal"] == no_read_safe_goal()
    assert not any(word in step["goal"] for word in ("读取绑定的探索资料", "真实资料"))


@pytest.mark.parametrize(
    ("selection", "interaction_mode", "expected_skills"),
    [
        ({}, "agent", []),
        ({"expert_id": "career-coach", "capability_id": "career-exploration"}, "experts", []),
        ({"expert_id": "career-coach", "capability_id": "career-planning"}, "experts", []),
        ({"expert_id": "career-coach", "capability_id": "education-screening"},
         "experts", ["education-screening-context"]),
        ({"expert_id": "career-coach", "capability_id": "education-screening-structured"},
         "experts", ["education-screening-context"]),
        ({"expert_id": "career-coach", "capability_id": "education-screening-structured-v3"},
         "experts", ["education-screening-context"]),
        ({"expert_id": "career-coach", "capability_id": "education-screening-structured-v4"},
         "experts", ["education-screening-context"]),
        ({"expert_id": "career-coach", "capability_id": "education-screening-structured-v5"},
         "experts", ["education-screening-context"]),
    ],
)
def test_no_read_plan_is_server_owned_for_generic_and_specialized_capabilities(
    monkeypatch, selection, interaction_mode, expected_skills,
):
    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    from app.services.agent_runtime.resource_access_policy import (
        no_read_safe_goal, validate_no_read_plan,
    )

    class PlanningRepository(_Repository):
        def __init__(self):
            super().__init__()
            self.plan = None

        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

        def persist_plan(self, _run_id, **kwargs):
            self.plan = kwargs

    message = "为虚构学生解释数据校验的三种方法；不联网，不读取资料，不执行操作。"
    snapshot = {"message": message, "interaction_mode": interaction_mode, **selection}
    capability = resolve_explicit_capability(snapshot)
    repository = PlanningRepository()
    executor = AgentRunExecutor(repository, _Dispatch())
    base = {"version": "fixture"}
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: base)
    monkeypatch.setattr(agent_run_executor, "_runtime_release", lambda *_args, **_kwargs: {
        "base": base, "release_sha256": "fixture", "skills": [],
    })
    monkeypatch.setattr(
        experts_lead, "experts_lead_decide",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planner must not run")),
    )

    assert executor._plan(
        "run_" + "7" * 32, "owner_a", "8" * 32, snapshot,
        {"current_plan_version": 0}, capability=capability,
    ) is False
    steps = repository.plan["steps"]
    assert validate_no_read_plan(capability, steps)
    assert steps[0]["goal"] == no_read_safe_goal()
    assert steps[0]["skill_ids"] == expected_skills


def test_allowed_career_exploration_read_remains_available_without_no_read_constraint(monkeypatch):
    from app.services.agent_runtime import agent_run_executor
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    class PlanningRepository(_Repository):
        def __init__(self):
            super().__init__()
            self.plan = None

        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

        def persist_plan(self, _run_id, **kwargs):
            self.plan = kwargs

    snapshot = {
        "message": "读取我绑定的探索资料，帮我比较当前选择。",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-exploration",
    }
    capability = resolve_explicit_capability(snapshot)
    repository = PlanningRepository()
    executor = AgentRunExecutor(repository, _Dispatch())
    base = {"version": "fixture"}
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: base)
    monkeypatch.setattr(agent_run_executor, "_runtime_release", lambda *_args, **_kwargs: {
        "base": base, "release_sha256": "fixture", "skills": [],
    })

    assert executor._plan(
        "run_" + "9" * 32, "owner_a", "a" * 32, snapshot,
        {"current_plan_version": 0}, capability=capability,
    ) is False
    assert "读取绑定的探索资料" in repository.plan["steps"][0]["goal"]


def test_no_read_personalized_request_waits_for_user_supplied_context(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    class PlanningRepository(_Repository):
        def __init__(self):
            super().__init__()
            self.waiting = []
            self.persisted = False

        def begin_planning(self, *_args, **_kwargs):
            return {"status": "planning"}

        def set_waiting_status(self, run_id, **kwargs):
            self.waiting.append((run_id, kwargs))

        def persist_plan(self, *_args, **_kwargs):
            self.persisted = True

    repository = PlanningRepository()
    executor = AgentRunExecutor(repository, _Dispatch())
    snapshot = {
        "message": "不要读取我的简历；请结合我的经历推荐适合我的职业方向。",
        "interaction_mode": "experts",
        "expert_id": "task-coordinator",
        "capability_id": "student-task",
    }
    capability = resolve_explicit_capability(snapshot)
    monkeypatch.setattr(
        experts_lead, "experts_lead_decide",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planner must not run")),
    )

    done = executor._plan(
        "run_" + "9" * 32, "owner_a", "8" * 32, snapshot,
        {"current_plan_version": 0}, capability=capability,
    )

    assert done is True
    assert not repository.persisted
    assert repository.waiting[0][1]["status"] == "waiting_input"
    assert "不会访问绑定资料" in repository.waiting[0][1]["reason"]


def test_no_read_student_task_bypasses_personal_pipeline_and_tools(monkeypatch):
    from app.services.agent_runtime import mode_runtime, student_task, task_context
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )
    from app.services.agent_runtime.agent_task_control import TaskBudget, TaskContract

    class Repository(_Repository):
        def __init__(self):
            super().__init__()
            self.mysql = object()
            self.receipt_checks = 0

        def has_completed_resource_read_for_step(self, *_args, **_kwargs):
            self.receipt_checks += 1
            return False

    staged = []

    class TaskContextStore:
        def __init__(self, _mysql):
            pass

        def stage(self, session_id, owner, run_id, result):
            staged.append((session_id, owner, run_id, result))

    repository = Repository()
    executor = AgentRunExecutor(repository, _Dispatch())
    seen = {}

    def invoke(packet, board, session_id, **kwargs):
        seen.update({
            "packet": packet.to_dict(),
            "board": board.to_dict(),
            "session_id": session_id,
            "kwargs": kwargs,
        })
        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="no-read-fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=_fixture_preflight(), finish_reason="stop",
        )
        return {"reply": "数据库隔离级别用于控制并发事务可见性。", "tool_calls": [],
                "skill_versions": []}

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(task_context, "TaskContextStore", TaskContextStore)
    monkeypatch.setattr(student_task, "run_step", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("personal task pipeline must not run"),
    ))
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)
    snapshot = {
        "message": "为虚构学生解释数据库隔离级别；不联网，不读取资料，不执行操作。",
        "interaction_mode": "experts",
        "expert_id": "task-coordinator",
        "capability_id": "student-task",
    }
    plan = _plan()
    from app.services.agent_runtime.resource_access_policy import no_read_safe_goal
    plan["steps"][0]["goal"] = no_read_safe_goal()
    contract = TaskContract(
        task_id="run_" + "c" * 32,
        revision=2,
        goal="解释数据库隔离级别并保持三步结构",
        deliverables=("三步计划",),
        constraints=("补充一道判断脏读与不可重复读的自测题", "不读取任何资料"),
        acceptance_criteria=("给出自测题及答案",),
        reads=(),
        writes=(),
        budget=TaskBudget(
            max_model_calls=4, max_tool_calls=0,
            max_input_tokens=80_000, max_output_tokens=12_000,
            max_call_input_tokens=24_000, max_call_output_tokens=4_000,
            max_retries=1,
        ),
    )

    done = executor._execute_step(
        "run_" + "c" * 32, "owner_a", "d" * 32, snapshot, plan,
        plan["steps"][0], board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(), session_id="session_qa",
        task_contract=contract,
        task_token_cap=28_000,
        task_usage={
            "verified": True, "model_calls": 0, "tool_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        },
    )

    assert done is False, repository.failed
    assert seen["session_id"] is None
    assert seen["kwargs"]["student_id"] is None
    assert seen["kwargs"]["allow_tools"] is False
    assert seen["kwargs"]["max_tokens"] == 2048
    assert seen["kwargs"]["no_tool_output_token_cap"] == 2048
    assert seen["kwargs"]["plan_tool_allowlist"] == set()
    assert seen["kwargs"]["allow_artifact_commit"] is False
    assert seen["packet"]["read_keys"] == []
    assert seen["packet"]["write_keys"] == []
    assert seen["packet"]["skill_ids"] == []
    assert "补充一道判断脏读与不可重复读的自测题" in seen["packet"]["goal"]
    assert "给出自测题及答案" in seen["packet"]["goal"]
    assert "不赋予资料读取或业务写入权限" in seen["packet"]["goal"]
    assert repository.receipt_checks == 0
    assert staged[0][1:3] == ("owner_a", "run_" + "c" * 32)
    assert staged[0][3]["resource_access"] == "none"
    assert repository.finished[0][1]["outcome"] == "completed"


@pytest.mark.parametrize(
    ("capability_id", "safe_skills", "release_key"),
    [
        ("career-planning", [], None),
        ("education-screening", ["education-screening-context"], "education_pipeline"),
        ("education-screening-structured", ["education-screening-context"],
         "education_screening_structured_v2"),
        ("education-screening-structured-v3", ["education-screening-context"],
         "education_screening_structured_v3"),
        ("education-screening-structured-v4", ["education-screening-context"],
         "education_screening_structured_v4"),
        ("education-screening-structured-v5", ["education-screening-context"],
         "education_screening_structured_v5"),
    ],
)
def test_no_read_specialized_education_capability_skips_owner_profile_pipeline(
    monkeypatch, capability_id, safe_skills, release_key,
):
    from app.services.agent_runtime import (
        agent_run_executor, education_screening, education_screening_structured_v2,
        education_screening_structured_v3, education_screening_structured_v4,
        education_screening_structured_v5, mode_runtime, school_facts,
    )
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    from app.services.agent_runtime.resource_access_policy import no_read_safe_goal

    repository = _Repository()
    executor = AgentRunExecutor(repository, _Dispatch())
    snapshot = {
        "message": "解释学历门槛中的‘必须’和‘优先’有什么区别；不联网，不读取资料。",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": capability_id,
    }
    capability = resolve_explicit_capability(snapshot)
    assert capability is not None
    plan = _plan()
    if release_key:
        plan["metadata"]["runtime_release"][release_key] = {"fixture": True}
    plan["metadata"]["runtime_release"]["skills"] = [
        {"skill_id": skill_id, "version": "0.3.0"} for skill_id in safe_skills
    ]
    plan["steps"][0].update({
        "goal": no_read_safe_goal(),
        "skill_ids": safe_skills,
    })
    seen = {}

    def forbidden_read(*_args, **_kwargs):
        raise AssertionError("no-read turn must not load owner school profile")

    monkeypatch.setattr(school_facts, "read_owner_school_context", forbidden_read)
    for module, function_name in (
        (education_screening, "extract_education_conditions"),
        (education_screening_structured_v2, "run_structured_education_step"),
        (education_screening_structured_v3, "run_structured_education_step"),
        (education_screening_structured_v4, "run_structured_education_step"),
        (education_screening_structured_v5, "run_structured_education_step_v5"),
    ):
        monkeypatch.setattr(module, function_name, forbidden_read)

    def invoke(packet, _board, session_id, **kwargs):
        seen.update({
            "packet": packet.to_dict(), "session_id": session_id,
            "student_id": kwargs["student_id"], "allow_tools": kwargs["allow_tools"],
            "allowlist": kwargs["plan_tool_allowlist"],
        })
        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="no-read-specialized-fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=_fixture_preflight(), finish_reason="stop",
        )
        return {"reply": "‘必须’描述门槛，‘优先’描述偏好；仍需核对完整原文。",
                "tool_calls": [], "skill_versions": []}

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)
    monkeypatch.setattr(executor, "_assert_skill_versions", lambda *_args: None)

    assert executor._execute_step(
        "run_" + "b" * 32, "owner_a", "c" * 32, snapshot, plan,
        plan["steps"][0], board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(), session_id="session_bound_should_not_pass",
    ) is False, repository.failed
    assert seen["packet"]["skill_ids"] == safe_skills
    assert seen["packet"]["read_keys"] == []
    assert seen["packet"]["write_keys"] == []
    assert seen["session_id"] is None
    assert seen["student_id"] is None
    assert seen["allow_tools"] is False
    assert seen["allowlist"] == set()
    assert repository.finished[0][1]["outcome"] == "completed"


def test_read_named_step_fails_closed_without_a_server_receipt(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.budget import (
        record_model_call, require_turn_token_capacity,
    )

    class Repository(_Repository):
        def has_completed_resource_read_for_step(self, *_args, **_kwargs):
            return False

    repository = Repository()
    executor = AgentRunExecutor(repository, _Dispatch())

    def invoke(*_args, **_kwargs):
        require_turn_token_capacity(10, estimated_input_tokens=10)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="false-read-fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=_fixture_preflight(), finish_reason="stop",
        )
        return {"reply": "已经读取并分析了绑定资料。", "tool_calls": [],
                "skill_versions": []}

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)
    plan = _plan()
    plan["steps"][0]["goal"] = "读取绑定资料并形成建议"

    done = executor._execute_step(
        "run_" + "e" * 32, "owner_a", "f" * 32,
        {"message": "请读取绑定资料并形成建议", "interaction_mode": "agent"},
        plan, plan["steps"][0], board=__import__(
            "app.services.agent_runtime.blackboard", fromlist=["Blackboard"],
        ).Blackboard(), session_id="session_read_receipt",
    )

    assert done is True
    assert repository.finished[0][1]["outcome"] == "failed"
    assert repository.finished[0][1]["error_code"] == "resource_read_result_unverified"
    assert repository.failed[0][1]["error_code"] == "resource_read_result_unverified"
