"""Regression coverage for the optional external Langfuse observability hook."""

import sys
from types import SimpleNamespace

import pytest


def _install_fake_langfuse(monkeypatch):
    captured = {}

    class Generation:
        def end(self, **kwargs):
            captured["end"] = kwargs

    class Trace:
        def generation(self, **kwargs):
            captured["generation"] = kwargs
            return Generation()

    class Langfuse:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def trace(self, **kwargs):
            captured["trace"] = kwargs
            return Trace()

        def flush(self):
            captured["flushed"] = True

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=Langfuse))
    return captured


def test_langfuse_default_trace_exports_accounting_but_not_prompt_or_answer(monkeypatch):
    from app.services.llmops import trace_generation

    captured = _install_fake_langfuse(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.delenv("CPA_LANGFUSE_CAPTURE_CONTENT", raising=False)

    prompt = "姓名：张三；手机号：13800138000"
    answer = "张三的简历建议包含 13800138000"
    with trace_generation("llm.chat", metadata={"max_tokens": 64}, input_preview=prompt) as bag:
        bag["output"] = answer
        bag["end_metadata"] = {
            "provider": "openai",
            "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
        }

    assert captured["generation"] == {"name": "llm.chat"}
    assert captured["end"] == {
        "metadata": {
            "provider": "openai",
            "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
        }
    }
    assert prompt not in str(captured)
    assert answer not in str(captured)
    assert captured["flushed"] is True


def test_langfuse_content_export_requires_explicit_opt_in(monkeypatch):
    from app.services.llmops import trace_generation

    captured = _install_fake_langfuse(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("CPA_LANGFUSE_CAPTURE_CONTENT", "true")

    with trace_generation("llm.chat", input_preview="approved prompt") as bag:
        bag["output"] = "approved answer"

    assert captured["generation"]["input"] == "approved prompt"
    assert captured["end"]["output"] == "approved answer"


def test_llm_client_default_request_boundary_does_not_export_resume_content(monkeypatch):
    """Exercise the same LLMClient boundary reached by Supervisor subcalls."""
    from app.utils.llm_client import LLMClient

    captured = _install_fake_langfuse(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.delenv("CPA_LANGFUSE_CAPTURE_CONTENT", raising=False)

    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="已完成分析"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=14, completion_tokens=4, total_tokens=18),
        model="gpt-4o-mini",
    )
    fake_openai = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
    )
    monkeypatch.setattr(
        client,
        "_get_openai",
        lambda _provider: (fake_openai, client._resolve_config("openai")),
    )

    prompt = "简历：李四，邮箱 lisi@example.test，电话 13900139000"
    assert client.chat([{"role": "user", "content": prompt}], max_tokens=64) == "已完成分析"
    assert captured["generation"] == {"name": "llm.chat"}
    assert captured["end"]["metadata"]["usage"]["total_tokens"] == 18
    assert prompt not in str(captured)


def test_llm_client_failed_request_ends_trace_without_exporting_prompt(monkeypatch):
    """The context-manager error path must not restore the old content export."""
    from app.utils.llm_client import LLMClient

    captured = _install_fake_langfuse(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.delenv("CPA_LANGFUSE_CAPTURE_CONTENT", raising=False)

    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    fake_openai = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("fixture timeout")))
        )
    )
    monkeypatch.setattr(
        client,
        "_get_openai",
        lambda _provider: (fake_openai, client._resolve_config("openai")),
    )

    prompt = "失败路径也不能上报：王五 wangwu@example.test"
    with pytest.raises(RuntimeError, match="全部 LLM 厂商失败"):
        client.chat([{"role": "user", "content": prompt}], max_tokens=64)

    assert captured["generation"] == {"name": "llm.chat"}
    assert captured["end"] == {"metadata": {}}
    assert prompt not in str(captured)
