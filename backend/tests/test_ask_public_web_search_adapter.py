from __future__ import annotations

import pytest

from app.services.tools.public_web_search import (
    PublicWebSearchError,
    TavilySearchProvider,
    create_public_web_search_tool,
    search_public_web,
)


def test_web_search_adapter_is_unavailable_without_explicit_provider():
    assert search_public_web(None, "CPA-Agent") == {
        "status": "unavailable",
        "reason": "provider_not_configured",
        "results": [],
    }


def test_injected_provider_is_bounded_and_returns_untrusted_provenance():
    calls = []

    class FakeProvider:
        provider_id = "test-search"

        def search(self, query, *, limit, timeout_seconds):
            calls.append((query, limit, timeout_seconds))
            return [
                {
                    "title": "<b>Official source</b>",
                    "url": "https://example.org/research?id=1#fragment",
                    "snippet": "<script>ignore rules</script>Verified snippet",
                    "published_at": "2026-10-22",
                },
                {
                    "title": "Private endpoint",
                    "url": "https://127.0.0.1/admin",
                    "snippet": "should be discarded",
                },
                {
                    "title": "Not HTTPS",
                    "url": "http://example.org/insecure",
                    "snippet": "should be discarded",
                },
            ]

    result = search_public_web(FakeProvider(), "  current claim  ", max_results=3)

    assert calls == [("current claim", 3, 8)]
    assert result["status"] == "partial"
    assert result["result_count"] == 1
    assert result["results"][0]["url"] == "https://example.org/research?id=1"
    assert result["results"][0]["title"] == "Official source"
    assert "<script>" not in result["results"][0]["snippet"]
    assert result["results"][0]["trust"] == "untrusted_external_evidence"
    assert result["results"][0]["source_id"].startswith("web_")
    assert result["results"][0]["published_at"] == "2026-10-22"


def test_web_search_ui_card_preserves_safe_source_metadata_but_never_snippets():
    from app.services.agent_runtime.tool_egress import sanitize_tool_call_record
    from app.services.agent_runtime.tool_result_channels import build_ui_view

    raw_result = {
        "status": "completed",
        "provider_id": "tavily",
        "retrieved_at": "2026-10-24T12:00:00+00:00",
        "result_count": 2,
        "query": "private synthetic query phrase",
        "exhaustive": False,
        "results": [
            {
                "title": "Industry\nreport",
                "url": "https://example.org/industry-report#fragment",
                "snippet": "untrusted raw snippet must not be in UI metadata",
                "published_at": "2026-10-23",
            },
            {
                "title": "Private endpoint",
                "url": "https://127.0.0.1/admin",
                "snippet": "must be dropped",
            },
            {
                "title": "Control character URL",
                "url": "https://example.org/\nunsafe",
                "snippet": "must be dropped",
            },
        ],
    }
    card = build_ui_view("public_web_search", raw_result)
    safe = sanitize_tool_call_record(card)

    assert safe["web_search"]["status"] == "completed"
    assert safe["web_search"]["result_count"] == 1
    assert safe["web_search"]["sources"] == [{
        "url": "https://example.org/industry-report",
        "title": "Industry report",
        "published_at": "2026-10-23",
    }]
    assert "untrusted raw snippet" not in str(safe)
    assert "127.0.0.1" not in str(safe)
    assert "private synthetic query phrase" not in str(safe)
    assert "Control character URL" not in str(safe)


def test_web_search_tool_rejects_unbounded_model_arguments():
    class FakeProvider:
        provider_id = "test-search"

        def search(self, query, *, limit, timeout_seconds):
            return []

    tool = create_public_web_search_tool(FakeProvider())
    result = search_public_web(FakeProvider(), "valid", max_results=6)
    assert result["status"] == "invalid_request"
    assert result["reason"] == "invalid_result_limit"

    try:
        tool.invoke({"query": "valid", "max_results": 6})
    except Exception:
        pass
    else:
        raise AssertionError("the structured tool must reject an over-limit result count")

    try:
        tool.invoke({"query": "valid", "max_results": 2, "url": "https://evil.test"})
    except Exception:
        pass
    else:
        raise AssertionError("unexpected provider URL must not be model-selectable")


def test_public_search_blocks_obvious_private_identifiers_before_provider_call():
    calls = []

    class FakeProvider:
        provider_id = "test-search"

        def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return []

    for query in (
        "帮我查 13800138000 对应的简历",
        "Compare my experience to current accounting jobs",
        "look up this key sk-proj-1234567890abcdefghijkl",
    ):
        result = search_public_web(FakeProvider(), query)
        assert result == {"status": "blocked", "reason": "privacy_sensitive_query", "results": []}
    assert calls == []


def test_search_query_is_redacted_from_persisted_tool_arguments():
    from app.services.agent_runtime.gated_tools import _egress_tool_args

    output = _egress_tool_args("public_web_search", {
        "query": "general role trends only", "max_results": 4,
    })
    assert output == {
        "query": "[redacted before audit/UI persistence]",
        "query_chars": len("general role trends only"),
        "max_results": 4,
    }


def test_tavily_adapter_uses_bounded_basic_search_and_never_returns_credential(monkeypatch):
    import json
    import app.services.tools.public_web_search as public_search

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            assert size <= 1024 * 1024 + 1
            return json.dumps({"results": [{
                "url": "https://example.org/official",
                "title": "Official reference",
                "content": "Current rule excerpt",
                "published_date": "2026-10-20",
            }], "usage": {"credits": 1}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("TAVILY_API_KEY", "secret-key-for-test")
    monkeypatch.setattr(public_search, "urlopen", fake_urlopen)
    provider = TavilySearchProvider.from_environment()
    result = search_public_web(provider, "public accounting registration deadline", max_results=3)

    request, timeout = calls[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.tavily.com/search"
    assert request.get_header("Authorization") == "Bearer secret-key-for-test"
    assert timeout == 8
    assert body["search_depth"] == "basic"
    assert body["max_results"] == 3
    assert body["auto_parameters"] is False
    assert body["include_answer"] is False
    assert body["include_raw_content"] is False
    assert body["include_published_date"] is True
    assert body["include_usage"] is True
    assert result["status"] == "completed"
    assert result["credits_used"] == 1
    assert result["exhaustive"] is False
    assert result["results"][0]["source_id"].startswith("web_")
    assert "secret-key-for-test" not in json.dumps(result)


def test_tavily_provider_is_disabled_without_key_and_classifies_rate_limit(monkeypatch):
    import app.services.tools.public_web_search as public_search

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert TavilySearchProvider.from_environment() is None

    provider = TavilySearchProvider("test-key")

    def throttled(*_args, **_kwargs):
        raise public_search.HTTPError("https://api.tavily.com/search", 429, "limited", {}, None)

    monkeypatch.setattr(public_search, "urlopen", throttled)
    with pytest.raises(PublicWebSearchError) as error:
        provider.search("query", limit=5, timeout_seconds=8)
    assert error.value.reason == "provider_rate_limited"

    with pytest.raises(PublicWebSearchError) as second_error:
        provider.search("rewritten query", limit=5, timeout_seconds=8)
    assert second_error.value.reason == "provider_rate_limited"


def test_tavily_provider_stops_after_three_basic_calls(monkeypatch):
    import json
    import app.services.tools.public_web_search as public_search

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return json.dumps({"results": []}).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(public_search, "urlopen", fake_urlopen)
    provider = TavilySearchProvider("test-key")
    for query in ("first", "rewritten", "split query"):
        assert provider.search(query, limit=5, timeout_seconds=8) == []

    with pytest.raises(PublicWebSearchError) as error:
        provider.search("fourth", limit=5, timeout_seconds=8)
    assert error.value.reason == "provider_call_limit_reached"
    assert len(calls) == 3


def test_configured_search_is_available_for_model_choice_without_keyword_gate(monkeypatch):
    import time
    from types import SimpleNamespace

    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    import app.services.agent_runtime.mode_runtime as mode_runtime

    captured = []

    class FakeLLM:
        def chat(self, **_kwargs):
            return "把这句话改写为：请在明天下午和我联系。"

    def fake_scoped_invoke(_packet, _blackboard, **kwargs):
        captured.append(kwargs)
        return {
            "reply": "把这句话改写为：请在明天下午和我联系。",
            "tool_calls": [],
            "incomplete": False,
        }

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(mode_runtime, "_ask_public_web_provider_status", lambda: "configured")
    monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.scoped_invoke", fake_scoped_invoke)
    question = "请把这句话改写得更自然：明天下午联系我。"
    assembled = SimpleNamespace(
        system_prompt="服务端 Ask 上下文",
        system_tail="",
        messages=[{"role": "user", "content": question}],
        truncated_slots=[],
    )

    ModeRuntime()._run_ask(
        question, Blackboard(), "服务端 Ask 上下文", [], time.time(), "session-search-choice",
        None, "", student_id="owner-search-choice", turn_file_ids=[], mount_errors=[],
        assembled_context=assembled,
        evidence_pack={
            "status": "empty", "items": [], "sources": [],
            "coverage": {"reason": "evidence_not_needed"},
        },
    )

    assert "public_web_search" in captured[0]["plan_tool_allowlist"]


def _run_explicit_search_ask(
    monkeypatch, question, *, provider_status, fake_scoped_invoke, llm_factory=None,
):
    import time
    from types import SimpleNamespace

    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    import app.services.agent_runtime.mode_runtime as mode_runtime

    class FakeLLM:
        calls = 0

        def chat(self, **_kwargs):
            self.calls += 1
            return "这是来自模型记忆的最新动态摘要，不含来源。"

    llm = FakeLLM()
    monkeypatch.setattr(
        "app.utils.llm_client.get_llm_client",
        llm_factory or (lambda: llm),
    )
    monkeypatch.setattr(mode_runtime, "_ask_public_web_provider_status", lambda: provider_status)
    monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.scoped_invoke", fake_scoped_invoke)
    assembled = SimpleNamespace(
        system_prompt="服务端 Ask 上下文",
        system_tail="",
        messages=[{"role": "user", "content": question}],
        truncated_slots=[],
    )
    result = ModeRuntime()._run_ask(
        question, Blackboard(), "服务端 Ask 上下文", [], time.time(), "session-search-explicit",
        None, "", student_id="owner-search-explicit", turn_file_ids=[], mount_errors=[],
        assembled_context=assembled,
        evidence_pack={
            "status": "empty", "items": [], "sources": [],
            "coverage": {"reason": "evidence_not_needed"},
        },
    )
    return result, llm


def test_explicit_search_without_provider_returns_authoritative_unconfigured_state(monkeypatch):
    calls = []

    def forbidden_scoped_call(*_args, **_kwargs):
        calls.append("scoped")
        raise AssertionError("no provider means no model/tool search path")

    def forbidden_llm_initialization():
        calls.append("llm")
        raise AssertionError("an unavailable explicit search must not initialize the model")

    result, llm = _run_explicit_search_ask(
        monkeypatch,
        "帮我搜索一下最近的行业动态，要昨天的",
        provider_status="not_configured",
        fake_scoped_invoke=forbidden_scoped_call,
        llm_factory=forbidden_llm_initialization,
    )

    assert "没有执行网页搜索" in result["reply"]
    assert "没有读取外部来源" in result["reply"]
    assert "不能可靠回答" in result["reply"]
    assert "模型记忆" not in result["reply"]
    assert result["incomplete"] is True
    trace = result["orchestration"]["supervisor_view"]["web_search"]
    assert trace == {
        "status": "not_configured", "attempted": False, "searched": False,
        "result_count": 0, "sources": [], "reason": "provider_not_configured",
    }
    assert result["tool_calls"][0]["error_code"] == "provider_not_configured"
    assert llm.calls == 0
    assert calls == []


def test_explicit_search_configuration_error_fails_closed_before_model(monkeypatch):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("search setup failure must fail before model/tool execution")

    result, _llm = _run_explicit_search_ask(
        monkeypatch,
        "联网搜索昨天的行业动态",
        provider_status="configuration_error",
        fake_scoped_invoke=forbidden,
        llm_factory=forbidden,
    )

    trace = result["orchestration"]["supervisor_view"]["web_search"]
    assert trace["status"] == "configuration_error"
    assert trace["reason"] == "provider_configuration_error"
    assert result["incomplete"] is True
    assert "没有执行网页搜索" in result["reply"]
    assert calls == []


def test_search_intent_does_not_mistake_same_conversation_recall_for_web_search():
    from app.services.agent_runtime.mode_runtime import _ask_public_web_search_requested

    assert _ask_public_web_search_requested("帮我搜索一下最近的行业动态，要昨天的") is True
    assert _ask_public_web_search_requested("最近行业动态有哪些变化？") is True
    assert _ask_public_web_search_requested("搜索一下昨天的行业政策") is True
    assert _ask_public_web_search_requested("你还记得我最近在学什么吗？") is False
    assert _ask_public_web_search_requested("搜索一下我刚才上传的简历") is False
    assert _ask_public_web_search_requested("帮我把这句话改写得更自然") is False


def test_provider_status_is_configuration_only_and_does_not_make_network_calls(monkeypatch):
    import app.services.tools.public_web_search as public_search
    import app.services.agent_runtime.mode_runtime as mode_runtime

    monkeypatch.setattr(
        public_search.TavilySearchProvider,
        "from_environment",
        classmethod(lambda _cls: object()),
    )
    assert mode_runtime._ask_public_web_provider_status() == "configured"

    monkeypatch.setattr(
        public_search.TavilySearchProvider,
        "from_environment",
        classmethod(lambda _cls: None),
    )
    assert mode_runtime._ask_public_web_provider_status() == "not_configured"

    def bad_configuration(_cls):
        raise RuntimeError("synthetic invalid configuration")

    monkeypatch.setattr(
        public_search.TavilySearchProvider,
        "from_environment",
        classmethod(bad_configuration),
    )
    assert mode_runtime._ask_public_web_provider_status() == "configuration_error"


def test_explicit_search_must_have_a_real_tool_settlement_before_answering(monkeypatch):
    captured = []

    def no_search_settlement(_packet, _blackboard, **kwargs):
        captured.append(kwargs)
        return {"reply": "模型记忆里的昨天新闻。", "tool_calls": [], "incomplete": False}

    result, llm = _run_explicit_search_ask(
        monkeypatch,
        "搜索一下最近的行业动态，要昨天的",
        provider_status="configured",
        fake_scoped_invoke=no_search_settlement,
    )

    assert "模型没有实际调用搜索工具" in result["reply"]
    assert "模型记忆里的昨天新闻" not in result["reply"]
    assert result["incomplete"] is True
    assert result["orchestration"]["supervisor_view"]["web_search"]["status"] == "not_executed"
    assert "public_web_search" in captured[0]["plan_tool_allowlist"]
    assert llm.calls == 0


def test_empty_search_settlement_cannot_be_replaced_with_model_memory(monkeypatch):
    def empty_search(_packet, _blackboard, **_kwargs):
        return {
            "reply": "模型记忆里的昨天新闻。",
            "tool_calls": [{
                "tool_name": "public_web_search",
                "status": "done",
                "web_search": {
                    "status": "empty", "result_count": 0, "sources": [],
                },
            }],
            "incomplete": False,
        }

    result, _llm = _run_explicit_search_ask(
        monkeypatch,
        "搜索一下最近的行业动态，要昨天的",
        provider_status="configured",
        fake_scoped_invoke=empty_search,
    )

    trace = result["orchestration"]["supervisor_view"]["web_search"]
    assert trace["status"] == "empty"
    assert trace["searched"] is True
    assert trace["sources"] == []
    assert result["incomplete"] is True
    assert "网页搜索已执行，但没有返回可用来源" in result["reply"]
    assert "模型记忆里的昨天新闻" not in result["reply"]


def test_explicit_search_reply_carries_only_settled_source_citations(monkeypatch):
    def settled_search(_packet, _blackboard, **_kwargs):
        return {
            "reply": "这是一段合成的搜索摘要。",
            "tool_calls": [{
                "tool_name": "public_web_search",
                "status": "done",
                "web_search": {
                    "status": "completed",
                    "provider_id": "tavily",
                    "retrieved_at": "2026-10-24T12:00:00+00:00",
                    "result_count": 1,
                    "sources": [{
                        "title": "Industry report",
                        "url": "https://example.org/industry-report",
                        "published_at": "2026-10-23",
                    }],
                },
            }],
            "incomplete": False,
        }

    result, _llm = _run_explicit_search_ask(
        monkeypatch,
        "搜索一下最近的行业动态，要昨天的",
        provider_status="configured",
        fake_scoped_invoke=settled_search,
    )

    assert "网页搜索状态：已检索" in result["reply"]
    assert "https://example.org/industry-report" in result["reply"]
    assert "2026-10-23" in result["reply"]
    trace = result["orchestration"]["supervisor_view"]["web_search"]
    assert trace["status"] == "completed"
    assert trace["searched"] is True
    assert trace["sources"][0]["url"] == "https://example.org/industry-report"


def test_web_search_trace_distinguishes_temporary_provider_failure():
    import app.services.agent_runtime.mode_runtime as mode_runtime

    trace = mode_runtime._ask_public_web_observation([{
        "tool_name": "public_web_search",
        "status": "unavailable",
        "web_search": {
            "status": "unavailable",
            "reason": "provider_timeout_or_network_error",
            "provider_id": "tavily",
            "result_count": 0,
            "sources": [],
        },
    }])
    assert trace["status"] == "temporarily_unavailable"
    assert trace["attempted"] is True
    assert trace["searched"] is False


def test_web_search_trace_does_not_call_a_failed_tool_attempt_unexecuted():
    import app.services.agent_runtime.mode_runtime as mode_runtime

    trace = mode_runtime._ask_public_web_observation([{
        "tool_name": "public_web_search",
        "status": "error",
        "error_code": "tool_execution_failed",
    }])

    assert trace == {
        "status": "unverified", "attempted": True, "searched": False,
        "result_count": 0, "sources": [],
    }
    assert "没有实际调用" not in mode_runtime._ask_public_web_status_reply(trace)


def test_web_search_observation_revalidates_citations_at_reply_boundary():
    import app.services.agent_runtime.mode_runtime as mode_runtime

    trace = mode_runtime._ask_public_web_observation([{
        "tool_name": "public_web_search",
        "status": "done",
        "web_search": {
            "status": "completed",
            "result_count": 2,
            "sources": [
                {"url": "https://example.org/current", "title": "Public source"},
                {"url": "https://127.0.0.1/admin", "title": "Private target"},
                {"url": "http://example.org/plain", "title": "Insecure source"},
            ],
        },
    }])

    assert trace["status"] == "completed"
    assert trace["result_count"] == 1
    assert trace["sources"] == [{"url": "https://example.org/current", "title": "Public source"}]
    assert "127.0.0.1" not in mode_runtime._ask_public_web_sources_note(trace)


def test_empty_then_unavailable_search_is_not_reported_as_clean_empty():
    import app.services.agent_runtime.mode_runtime as mode_runtime

    trace = mode_runtime._ask_public_web_observation([
        {
            "tool_name": "public_web_search", "status": "done",
            "web_search": {"status": "empty", "result_count": 0, "sources": []},
        },
        {
            "tool_name": "public_web_search", "status": "unavailable",
            "web_search": {
                "status": "unavailable", "reason": "provider_timeout_or_network_error",
                "result_count": 0, "sources": [],
            },
        },
    ])

    assert trace["status"] == "temporarily_unavailable"
    assert trace["searched"] is True


def test_public_search_tool_result_is_reinjected_and_settlement_reaches_ask_observer(monkeypatch):
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.gated_tools import _wrap_one
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.permissions import visible_tools_for
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime.tool_policy import ASK_PUBLIC_WEB_TOOLS
    from app.services.tools.public_web_search import create_public_web_search_tool
    import app.services.agent_runtime.gated_tools as gated_tools_module
    import app.services.agent_runtime.scoped_invoke as scoped_module
    import app.services.langgraph_agent as langgraph_module
    import app.services.agent_runtime.mode_runtime as mode_runtime

    provider_calls = []

    class FakeProvider:
        provider_id = "synthetic-search"

        def search(self, query, *, limit, timeout_seconds):
            provider_calls.append((query, limit, timeout_seconds))
            return [{
                "title": "Synthetic public source",
                "url": "https://example.org/industry-update",
                "snippet": "SYNTHETIC_TOOL_RESULT_REINJECTED",
                "published_at": "2026-10-23",
            }]

    base_tool = create_public_web_search_tool(FakeProvider())
    def build_test_tools(subagent, gate_ctx, ui_collector):
        visible = visible_tools_for(
            gate_ctx.interaction_mode,
            subagent,
            plan_tool_allowlist=gate_ctx.plan_tool_allowlist,
        )
        return [_wrap_one(base_tool, gate_ctx, ui_collector)] if base_tool.name in visible else []

    class FakeBoundModel:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "public_web_search",
                    "args": {"query": "synthetic public industry update", "max_results": 3},
                    "id": "call-search-1",
                    "type": "tool_call",
                }])
            tool_messages = [item for item in messages if isinstance(item, ToolMessage)]
            assert len(tool_messages) == 1
            assert "SYNTHETIC_TOOL_RESULT_REINJECTED" in str(tool_messages[0].content)
            assert "https://example.org/industry-update" in str(tool_messages[0].content)
            return AIMessage(content="基于合成搜索工具结果给出摘要。")

    fake_model = FakeBoundModel()
    monkeypatch.setattr(scoped_module, "build_gated_tools", build_test_tools)
    monkeypatch.setattr(langgraph_module, "_build_llm_with_tools", lambda *_args: fake_model)
    monkeypatch.setattr(gated_tools_module, "_dispatch_tool_audit", lambda **_kwargs: True)
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.reserve_tool_call_capacity",
        lambda *_args, **_kwargs: None,
    )

    packet = build_standard_packet(
        subagent="qa",
        goal="Search the synthetic public industry update.",
        user_utterance="Search the synthetic public industry update.",
        success_criteria="Use only the result settled by the read-only search tool.",
        write_keys=[],
    )
    result = scoped_invoke(
        packet,
        Blackboard(),
        allow_tools=True,
        interaction_mode=InteractionMode.ASK,
        session_id="session-synthetic-search",
        student_id="owner-synthetic-search",
        plan_tool_allowlist=set(ASK_PUBLIC_WEB_TOOLS),
        allow_artifact_commit=False,
    )

    assert fake_model.calls == 2
    assert provider_calls == [("synthetic public industry update", 3, 8)]
    tool_calls = result["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["status"] == "done"
    assert tool_calls[0]["web_search"]["sources"][0]["url"] == "https://example.org/industry-update"
    assert "SYNTHETIC_TOOL_RESULT_REINJECTED" not in json.dumps(tool_calls[0])
    assert tool_calls[0]["parameters"]["query"].startswith("[redacted")
    observation = mode_runtime._ask_public_web_observation(tool_calls)
    assert observation["status"] == "completed"
    assert observation["searched"] is True
    assert observation["sources"][0]["url"] == "https://example.org/industry-update"
