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
    monkeypatch.setattr(mode_runtime, "_ask_public_web_provider_available", lambda: True)
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
