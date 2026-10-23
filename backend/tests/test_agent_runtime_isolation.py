"""Isolation tests for agent_runtime: ACL, no dual history, tool allowlists."""

import pytest

from app.services.agent_runtime.blackboard import (
    Blackboard,
    BlackboardError,
    load_blackboard_from_session_context,
)
from app.services.agent_runtime.budget import MAX_HISTORY_MESSAGES
from app.services.agent_runtime.task_packet import build_standard_packet
from app.services.agent_runtime.tool_policy import assert_tool_allowed, tools_for
from app.services.session_manager import SessionManager, SessionState, SessionStatus
from datetime import datetime


def test_blackboard_rejects_unknown_key():
    bb = Blackboard()
    with pytest.raises(BlackboardError):
        bb.write("foo.bar", {"x": 1}, "profile")


def test_blackboard_single_writer_acl():
    bb = Blackboard()
    bb.write("portrait.current", {"id": "p1", "summary": "ok"}, "profile")
    with pytest.raises(BlackboardError):
        bb.write("portrait.current", {"id": "p2"}, "match")
    with pytest.raises(BlackboardError):
        bb.write("match.current", {"id": "m1"}, "profile")


def test_blackboard_gate_requires_portrait_for_match():
    bb = Blackboard()
    assert bb.check_gate("match")
    bb.write("portrait.current", {"summary": "ready"}, "profile")
    assert bb.check_gate("match") is None


def test_task_packet_rejects_illegal_write_keys():
    with pytest.raises(BlackboardError):
        build_standard_packet(
            subagent="qa",
            goal="ask",
            write_keys=["portrait.current"],
        )


def test_tool_allowlist_hard_partition():
    assert "pipeline_run" in tools_for("profile")
    assert "pipeline_run" not in tools_for("qa")
    assert "report_generate" in tools_for("report")
    assert not tools_for("supervisor")
    with pytest.raises(PermissionError):
        assert_tool_allowed("qa", "portrait_generate")


def test_structured_context_omits_recent_messages_by_default():
    mgr = SessionManager.__new__(SessionManager)
    mgr._redis = None
    mgr._cache = {}
    sid = "sess_iso_test"
    state = SessionState(
        session_id=sid,
        student_id="stu_1",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        context={"blackboard": {"portrait.current": {"summary": "x"}}},
        conversation_summary="user wants match",
    )
    mgr._cache[sid] = state

    # bypass redis save paths
    def _noop_save(_s):
        pass

    mgr._save_to_redis = _noop_save  # type: ignore

    ctx = mgr.build_structured_context(sid, student_id="stu_1")
    assert "recent_messages" not in ctx.get("conversation", {})
    assert ctx["conversation"]["summary"] == "user wants match"
    assert ctx["blackboard"]["portrait.current"]["summary"] == "x"

    legacy = mgr.build_structured_context(
        sid, student_id="stu_1", include_recent_messages=True,
    )
    assert "recent_messages" in legacy["conversation"]


def test_history_default_is_k8_budget():
    assert MAX_HISTORY_MESSAGES == 16


def test_load_blackboard_migrates_legacy_flat_keys():
    bb = load_blackboard_from_session_context({
        "portrait": {"id": "p9", "summary": "legacy"},
        "match_result": {"score": 80},
    })
    assert bb.has("portrait.current")
    assert bb.has("match.current")


def test_plan_detect_end_to_end():
    from app.services.agent_runtime.plan import detect_plan_template, PLAN_TEMPLATES

    assert detect_plan_template("请生成完整职业方案", plan_mode=False) is None
    # Vague chatter must NOT auto-match; Lead owns intent, matcher is hint-only
    assert detect_plan_template("随便问问", plan_mode=True) is None
    assert PLAN_TEMPLATES["end_to_end"] == ["profile", "match", "report"]


def test_prompt_registry_has_supervisor_bundles():
    from config.prompt_loader import load_prompt

    manual = load_prompt("agent.manual")
    assert "黑板" in manual or "ACL" in manual or "隔离" in manual
    assert load_prompt("agent.supervisor.system")
    assert load_prompt("agent.qa.system")


def test_ask_mode_greeting_uses_model_not_keyword_template(monkeypatch):
    from app.services.agent_runtime import get_supervisor_service
    from app.services.agent_runtime.modes import InteractionMode, parse_interaction_mode

    assert parse_interaction_mode(None) == InteractionMode.ASK

    class FakeLLM:
        def chat(self, **_kwargs):
            return "你好，今天想聊什么职业方向？"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    result = get_supervisor_service().chat(message="你好啊")
    assert result["interaction_mode"] == "ask"
    assert result["orchestration"]["strategy"] == "ask"
    assert result.get("tool_calls") == []
    assert result["orchestration"]["supervisor_view"]["path"] == "ask_llm"
    assert "你好" in result["reply"]


def test_ask_task_request_never_autoruns_business_tools(monkeypatch):
    from app.services.agent_runtime import get_supervisor_service

    class FakeLLM:
        def chat(self, **_kwargs):
            return "当前仅可讨论职业报告思路；若要执行，请切换到可执行模式。"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    result = get_supervisor_service().chat(
        message="请帮我生成职业报告",
        interaction_mode="ask",
    )
    assert result["interaction_mode"] == "ask"
    assert not result.get("upgrade_hint")
    bb = result.get("blackboard") or {}
    assert "report.current" not in bb or not bb.get("report.current")


def test_agent_mode_without_portrait_clarifies_before_match(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.task_packet import build_standard_packet
    import json

    # The server-side Lead can select a match step, but the known-slot check
    # must stop execution before any match artifact is written without a
    # confirmed resume or portrait.
    class FakeLLM:
        def chat(self, **_kwargs):
            return json.dumps({
                "user_job": "execute",
                "action": "run_plan",
                "template": None,
                "steps": [{"role": "match", "goal": "匹配岗位", "skill_ids": []}],
                "goal_summary": "执行岗位匹配",
                "vagueness": "clear",
                "missing_slots": [],
                "handoff_brief": "根据已确认背景匹配岗位",
            }, ensure_ascii=False)

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    pkt = build_standard_packet("match", goal="匹配", user_utterance="帮我做人岗匹配")
    assert pkt.subagent == "match"
    assert "match.current" in pkt.write_keys

    # Mode parse
    from app.services.agent_runtime.modes import parse_interaction_mode, InteractionMode

    assert parse_interaction_mode("agent") == InteractionMode.AGENT
    rt = ModeRuntime()
    # A missing prerequisite must not synthesize a completed match.
    out = rt.chat(message="帮我做人岗匹配", interaction_mode="agent")
    assert out["interaction_mode"] == "agent"
    assert out["orchestration"]["strategy"] == "agent_clarify"
    view = out["orchestration"]["supervisor_view"]
    assert view["lead_action"] == "clarify"
    assert "portrait" in view["missing_slots"]
    assert "match.current" not in (out.get("blackboard") or {})


def test_experts_mode_plan_structure():
    from app.services.agent_runtime.modes import parse_interaction_mode, InteractionMode
    from app.services.agent_runtime.plan import build_plan_packets

    assert parse_interaction_mode(None, plan_mode=True) == InteractionMode.EXPERTS
    packets = build_plan_packets("end_to_end", "一键出报告")
    assert [p.subagent for p in packets] == ["profile", "match", "report"]


def test_fork_rollup_not_sync_on_ask_greeting(monkeypatch):
    from app.services.agent_runtime import fork_jobs, get_supervisor_service

    class FakeLLM:
        def chat(self, **_kwargs):
            return "你好，今天想聊什么职业方向？"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    fork_jobs.reset_fork_stats()
    calls = {"rollup": 0}

    def boom(*_a, **_k):
        calls["rollup"] += 1
        fork_jobs.mark_sync_forbidden("rollup")

    monkeypatch.setattr(
        "app.services.session_manager.SessionManager.maybe_rollup_summary",
        boom,
    )
    get_supervisor_service().chat(message="你好啊", interaction_mode="ask")
    # Ask greeting should not enqueue rollup (hist short) and must not sync-call
    assert calls["rollup"] == 0
    assert fork_jobs.get_fork_stats()["sync_forbidden_calls"] == 0


def test_ask_rollup_is_not_triggered_by_recent_message_count(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    calls = []
    manager = type("FakeManager", (), {
        "maybe_rollup_summary": lambda _self, session_id, keep_messages, *, compact_summary:
            calls.append((session_id, keep_messages, compact_summary)),
    })()
    monkeypatch.setattr(
        "app.services.session_manager.get_session_manager",
        lambda: manager,
    )

    rt = ModeRuntime()
    monkeypatch.setattr(rt, "_chat_impl", lambda *_args, **_kwargs: {"reply": "离线固定答复"})
    for history_size in (15, 16, 200):
        rt.chat(
            message="你好啊",
            session_id="sess_rollup",
            interaction_mode="ask",
            history=[
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                for i in range(history_size)
            ],
        )

    # Recent-message count only defines the tail window. Without a completed
    # final-request token preflight, none of these turns may trigger summary
    # compaction. This test is fully isolated from Redis and model services.
    assert calls == [
        ("sess_rollup", 16, False),
        ("sess_rollup", 16, False),
        ("sess_rollup", 16, False),
    ]


def test_agent_enqueues_rollup(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    calls = []
    monkeypatch.setattr(
        "app.services.agent_runtime.mode_runtime.fork_enqueue",
        lambda session_id, kind, payload=None: calls.append((session_id, kind, payload)) or kind,
    )

    out = ModeRuntime().chat(
        message="帮我做人岗匹配",
        session_id="sess_agent_rollup",
        interaction_mode="agent",
    )
    assert out["interaction_mode"] == "agent"
    assert calls[-1] == ("sess_agent_rollup", "rollup_summary", {"keep_messages": 16})


def test_rollup_summary_uses_deterministic_cursor(monkeypatch):
    mgr = SessionManager.__new__(SessionManager)
    mgr._redis = None
    mgr._cache = {}
    sid = "sess_rollup_cursor"
    state = SessionState(
        session_id=sid,
        student_id="stu_1",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        messages=[
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(20)
        ],
        conversation_summary="",
    )
    mgr._cache[sid] = state
    mgr._save_to_redis = lambda _s: None  # type: ignore

    prompts = []

    class FakeLLM:
        def chat(self, messages, **_kwargs):
            prompts.append(messages[0]["content"])
            return f"summary-{len(prompts)}"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    assert mgr.maybe_rollup_summary(sid, keep_messages=16) == "summary-1"
    assert mgr._cache[sid].summary_message_count == 4

    mgr._cache[sid].messages.extend(
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(20, 24)
    )
    assert mgr.maybe_rollup_summary(sid, keep_messages=16) == "summary-2"
    assert mgr._cache[sid].summary_message_count == 8
    assert "m0" not in prompts[-1]
    assert "m4" in prompts[-1]


def test_router_decision_not_derived_from_keywords(monkeypatch):
    from app.services.agent_runtime.mode_runtime import _llm_route

    class FakeLLM:
        def chat_json(self, **_kwargs):
            return {"action": "call", "subagent": "qa", "goal": "解释匹配", "document_view": "answer"}

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    assert _llm_route("帮我做人岗匹配", "") ["subagent"] == "qa"


def test_prompt_registry_three_modes():
    from config.prompt_loader import load_prompt

    assert "Ask" in load_prompt("agent.ask.system") or "问答" in load_prompt("agent.ask.system")
    assert load_prompt("agent.agent.system")
    assert load_prompt("agent.experts.system")
    assert "Ask" in load_prompt("agent.manual") or "交互模式" in load_prompt("agent.manual")


def test_thinking_egress_strips_internals():
    from app.services.agent_runtime.thinking_egress import sanitize_thinking_steps
    from app.services.agent_runtime.thinking_trace import ask_fallback_trace, parse_thinking_answer

    dirty = [
        "portrait.current / match.current / report.current 为空",
        "Ask 不能调用 portrait_generate；student.profile_ref=student_001",
        "这是 CPA架构 底层契约",
        "我会引导上传简历并切换到执行模式",
    ]
    clean = sanitize_thinking_steps(dirty)
    blob = "\n".join(clean)
    assert "portrait.current" not in blob
    assert "portrait_generate" not in blob
    assert "student_001" not in blob
    assert "CPA架构" not in blob
    assert "个人职业画像" in blob or "画像" in blob

    steps = ask_fallback_trace("帮我分析适合的职业方向", path="ask_llm")
    assert all("portrait." not in s and "blackboard" not in s.lower() for s in steps)

    raw = (
        "<<<THINKING>>>\n"
        "portrait.current 为空，不能假装匹配。\n\n"
        "我会给框架并引导上传简历。\n"
        "<<<ANSWER>>>\n"
        "建议先建立个人画像。"
    )
    parsed, answer = parse_thinking_answer(raw)
    assert answer and "画像" in answer
    assert all("portrait.current" not in s for s in parsed)


def test_tool_egress_redacts_pii_and_json():
    from app.services.agent_runtime.tool_egress import sanitize_tool_calls, redact_text

    raw = [{
        "tool_name": "db_query",
        "parameters": {"query": "查 profile", "table_hint": "student_profile"},
        "result_preview": (
            "查询结果 (1 条): 1. student_id=teacher_001, name=刘老师, "
            "phone=13600136001, email=teacher001@example.com, "
            "profile_json={\"name\": \"刘老师\"}"
        ),
    }]
    cards = sanitize_tool_calls(raw)
    assert len(cards) == 1
    text = cards[0]["summary"] + cards[0].get("result_preview", "")
    assert "13600136001" not in text
    assert "teacher001@example.com" not in text
    assert "profile_json={" not in text
    assert "已脱敏" in cards[0]["summary"] or "完成" in cards[0]["summary"]
    assert "136****6001" in redact_text("phone=13600136001") or "****" in redact_text("13600136001")


def test_db_query_format_redacts_sensitive_keys():
    from app.services.tools.db_query import DbQueryTool

    tool = DbQueryTool.__new__(DbQueryTool)
    formatted = tool._format_results([
        {
            "student_id": "s1",
            "name": "张三",
            "phone": "13800138000",
            "email": "a@b.com",
            "profile_json": {"x": 1},
        }
    ], "student_profile")
    assert "13800138000" not in formatted
    assert "a@b.com" not in formatted
    assert "profile_json=[redacted]" in formatted
    assert "student_id=s1" in formatted


def test_ask_requires_explicit_read_allowlist_and_denies_business_writes():
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.permissions import visible_tools_for, evaluate, Decision
    from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use
    from app.services.agent_runtime.tool_policy import (
        ASK_SEARCH_TOOLS, visible_tools_for_interaction,
    )

    assert visible_tools_for(InteractionMode.ASK, "profile") == set()
    assert visible_tools_for_interaction("ask", "profile") == set()
    assert visible_tools_for(InteractionMode.ASK, "qa") == set()
    assert visible_tools_for_interaction("ask", "qa") == set(ASK_SEARCH_TOOLS)
    assert visible_tools_for(
        InteractionMode.ASK, "qa", plan_tool_allowlist=set(ASK_SEARCH_TOOLS),
    ) == set(ASK_SEARCH_TOOLS)

    read = evaluate(
        "career_data_read",
        {"operation": "list", "student_id": "s1"},
        interaction=InteractionMode.ASK,
        subagent="qa",
        plan_tool_allowlist={"career_data_read"},
    )
    assert read.decision == Decision.ALLOW

    v = evaluate(
        "pipeline_run",
        {"student_id": "s1"},
        interaction=InteractionMode.ASK,
        subagent="qa",
        plan_tool_allowlist=set(ASK_SEARCH_TOOLS),
    )
    assert v.decision == Decision.DENY

    unsafe_reads = ("db_query", "semantic_search", "normalize_entity")
    for tool_name in unsafe_reads:
        verdict = evaluate(
            tool_name,
            {"query": "career facts", "student_id": "s1"},
            interaction=InteractionMode.ASK,
            subagent="qa",
            plan_tool_allowlist=set(ASK_SEARCH_TOOLS),
        )
        assert verdict.decision == Decision.DENY

    ctx = GateContext(
        interaction_mode=InteractionMode.ASK,
        subagent="qa",
        student_id="s1",
    )
    gate = pre_tool_use("pipeline_run", {"student_id": "s1"}, ctx)
    assert not gate.allowed
    assert ctx.denials


def test_agent_rewrites_foreign_student_id():
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use, bind_session_scope

    bound, rewritten, deny = bind_session_scope(
        "db_query",
        {"student_id": "other_stu", "query": "查学生档案"},
        "sess_stu",
    )
    assert deny is None
    assert rewritten
    assert bound["student_id"] == "sess_stu"

    ctx = GateContext(
        interaction_mode=InteractionMode.AGENT,
        subagent="profile",
        student_id="sess_stu",
        session_id="sess_1",
    )
    gate = pre_tool_use(
        "db_query",
        {"student_id": "attacker", "query": "查档案", "table_hint": "student_profile"},
        ctx,
    )
    assert gate.allowed
    assert gate.args["student_id"] == "sess_stu"
    assert gate.rewritten


def test_db_query_missing_session_scope_deny():
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use

    ctx = GateContext(
        interaction_mode=InteractionMode.AGENT,
        subagent="profile",
        student_id=None,
        session_id="sess_1",
    )
    gate = pre_tool_use(
        "db_query",
        {"query": "列出学生 student profile", "table_hint": "student_profile"},
        ctx,
    )
    assert not gate.allowed
    assert "student_id" in gate.verdict.reason


def test_dual_channel_strips_never_fields():
    from app.services.agent_runtime.field_policy import project_row
    from app.services.agent_runtime.tool_result_channels import build_model_view, build_ui_view

    row = project_row({
        "student_id": "s1",
        "phone": "13800138000",
        "email": "a@b.com",
        "profile_json": {"secret": True},
        "name": "张三",
    }, for_model=True)
    assert row["phone"] == "[redacted]"
    assert row["email"] == "[redacted]"
    assert row["profile_json"] == "[redacted]"
    assert row["student_id"] == "s1"

    raw = (
        "查询结果 (1 条): 1. student_id=s1, name=张三, "
        "phone=13800138000, email=a@b.com, profile_json={\"x\":1}"
    )
    model = build_model_view("db_query", raw)
    ui = build_ui_view("db_query", raw, parameters={"student_id": "s1"})
    assert "13800138000" not in model
    assert "a@b.com" not in model
    assert "profile_json={" not in model
    assert "13800138000" not in ui["summary"]
    assert "a@b.com" not in ui.get("result_preview", "")
    assert ui.get("channel") == "ui"


def test_tool_audit_enqueue_called(monkeypatch):
    from app.services.agent_runtime import tool_audit
    from app.services.agent_runtime.gated_tools import _wrap_one
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext
    from langchain_core.tools import StructuredTool

    calls = []

    def _fake_enqueue(**kwargs):
        calls.append(kwargs)
        return "tool_audit"

    monkeypatch.setattr(tool_audit, "enqueue_audit", _fake_enqueue)
    # gated_tools imports enqueue_audit at module level — patch there too
    import app.services.agent_runtime.gated_tools as gt
    monkeypatch.setattr(gt, "enqueue_audit", _fake_enqueue)

    def _boom(**kwargs):
        return "ok"

    base = StructuredTool.from_function(func=_boom, name="semantic_search", description="search")
    ctx = GateContext(
        interaction_mode=InteractionMode.AGENT,
        subagent="qa",
        session_id="sess_a",
        student_id="stu_a",
    )
    ui = []
    wrapped = _wrap_one(base, ctx, ui)
    # Deny path: ask mode visibility empty — use deny by switching mode
    ctx.interaction_mode = InteractionMode.ASK
    out = wrapped.invoke({})
    assert "permission_denied" in out or calls
    assert calls
    assert calls[0]["decision"] == "deny"
    assert calls[0]["tool_name"] == "semantic_search"


def test_gated_invoke_preserves_injected_student_id(monkeypatch):
    """PreToolUse injects student_id; must reach tool.func (not stripped by invoke)."""
    from pydantic import BaseModel, Field
    from langchain_core.tools import StructuredTool
    from app.services.agent_runtime.gated_tools import _wrap_one
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext
    import app.services.agent_runtime.gated_tools as gt

    monkeypatch.setattr(gt, "enqueue_audit", lambda **kwargs: "skip")
    seen = {}

    class NarrowArgs(BaseModel):
        query: str = Field(default="")
        table_hint: str = Field(default="")

    def _db_like(**kwargs):
        seen.update(kwargs)
        return f"got student_id={kwargs.get('student_id')}"

    base = StructuredTool.from_function(
        func=_db_like,
        name="db_query",
        description="query",
        args_schema=NarrowArgs,
    )
    ctx = GateContext(
        interaction_mode=InteractionMode.AGENT,
        subagent="qa",
        session_id="sess_x",
        student_id="student_001",
    )
    wrapped = _wrap_one(base, ctx, [])
    out = wrapped.invoke({"query": "当前用户关联的学生基本信息", "table_hint": "student"})
    assert seen.get("student_id") == "student_001"
    assert "student_001" in out


def test_db_query_match_defaults_to_list_by_student():
    from app.services.tools.db_query import DbQueryTool

    table, qtype = DbQueryTool()._guess_table("获取当前匹配结果的详细信息", "match")
    assert table == "match_result"
    assert qtype == "list_by_student"


def test_request_scope_demo_ignores_forged_student_id(monkeypatch):
    from flask import Flask
    from app.config import Config
    from app.api._helpers import resolve_request_scope

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_demo_scope")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:stu_demo_scope")
    app = Flask(__name__)
    with app.test_request_context(
        "/api/agent/session/create?student_id=attacker",
        method="POST",
        json={"student_id": "attacker"},
    ):
        scope = resolve_request_scope()

    assert scope.student_id == "stu_demo_scope"
    assert scope.principal_id == "demo:stu_demo_scope"


def test_request_scope_strict_without_auth_fails_closed(monkeypatch):
    from flask import Flask
    from app.config import Config
    from app.api._helpers import ScopeError, resolve_request_scope

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    with app.test_request_context("/api/agent/sessions"):
        with pytest.raises(ScopeError) as err:
            resolve_request_scope()

    assert err.value.status_code == 401


def test_create_session_ignores_forged_student_id(monkeypatch):
    from flask import Flask
    from app.config import Config
    from app.api.agent import create_session
    from app.services.session_manager import SessionState, SessionStatus

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:stu_scope")

    created = {}

    class FakeSessionManager:
        def get_session(self, _sid):
            return None

        def create_session(self, student_id=None, session_id=None):
            created["student_id"] = student_id
            return SessionState(
                session_id=session_id or "session_scope",
                student_id=student_id,
                status=SessionStatus.CREATED,
                created_at="now",
                updated_at="now",
            )

    monkeypatch.setattr("app.api.agent.get_session_manager", lambda: FakeSessionManager())
    app = Flask(__name__)
    with app.test_request_context(
        "/api/agent/session/create",
        method="POST",
        json={"student_id": "attacker"},
    ):
        resp = create_session()

    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["data"]["student_id"] == "stu_scope"
    assert created["student_id"] == "stu_scope"


def test_session_history_cross_owner_forbidden(monkeypatch):
    from flask import Flask
    from app.config import Config
    from app.api.agent import get_history
    from app.services.session_manager import SessionState, SessionStatus

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:stu_scope")

    class FakeSessionManager:
        def get_session(self, _sid):
            return SessionState(
                session_id="session_foreign",
                student_id="other_stu",
                status=SessionStatus.ACTIVE,
                created_at="now",
                updated_at="now",
            )

    monkeypatch.setattr("app.api.agent.get_session_manager", lambda: FakeSessionManager())
    app = Flask(__name__)
    with app.test_request_context("/api/agent/session/session_foreign/history"):
        resp, status = get_history("session_foreign")

    assert status == 403
    assert resp.get_json()["code"] == "PERMISSION_ERROR"


def test_legacy_agent_engines_disabled_by_default(monkeypatch):
    from flask import Flask
    from app.config import Config
    from app.api.agent import chat

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:stu_scope")
    monkeypatch.setattr(Config, "ENABLE_LEGACY_AGENT_ENGINES", False)

    app = Flask(__name__)
    with app.test_request_context(
        "/api/agent/chat",
        method="POST",
        json={"message": "hi", "mode": "react", "student_id": "attacker"},
    ):
        resp, status = chat()

    assert status == 403
    assert resp.get_json()["code"] == "PERMISSION_ERROR"


def test_tool_scope_normalizer_rewrites_nested_identity():
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use

    ctx = GateContext(
        interaction_mode=InteractionMode.AGENT,
        subagent="profile",
        student_id="stu_scope",
        session_id="session_scope",
    )
    gate = pre_tool_use(
        "pipeline_run",
        {
            "student_id": "attacker",
            "session_id": "foreign_session",
            "params": {
                "student_id": "other",
                "sessionId": "other_session",
                "items": [{"userId": "evil"}],
            },
        },
        ctx,
    )

    assert gate.allowed
    assert gate.rewritten
    assert gate.args["student_id"] == "stu_scope"
    assert gate.args["session_id"] == "session_scope"
    assert gate.args["params"]["student_id"] == "stu_scope"
    assert gate.args["params"]["sessionId"] == "session_scope"
    assert gate.args["params"]["items"][0]["userId"] == "stu_scope"
