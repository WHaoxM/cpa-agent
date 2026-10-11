"""Provider-aware context windows and usage propagation without network calls."""

from types import SimpleNamespace

import pytest

from config.integration_loader import get_provider_config
from app.services.agent_runtime.budget import context_window_status, model_capability_for
from app.utils.llm_client import LLMClient


def _msg(text):
    return [{"role": "user", "content": text}]


def test_exact_model_capability_and_unknown_override(monkeypatch):
    monkeypatch.delenv("CPA_LLM_CONTEXT_WINDOW_TOKENS_QWEN", raising=False)
    known = model_capability_for("qwen", "qwen-plus")
    assert known.context_window_tokens == 131072
    assert known.max_input_tokens == 98304
    assert model_capability_for("qwen", "another-model").context_window_tokens is None
    unknown = context_window_status("qwen", "another-model", _msg("hello"), 100)
    assert unknown.fits is None
    assert unknown.remaining_input_tokens is None
    assert unknown.reason == "model_capability_unknown"


def test_moonshot_needs_explicit_window_and_detects_overflow(monkeypatch):
    monkeypatch.delenv("CPA_LLM_CONTEXT_WINDOW_TOKENS_MOONSHOT", raising=False)
    assert context_window_status("moonshot", "moonshot-v1-8k", _msg("hi"), 100).fits is None
    monkeypatch.setenv("CPA_LLM_CONTEXT_WINDOW_TOKENS_MOONSHOT", "8192")
    small = context_window_status("moonshot", "moonshot-v1-8k", _msg("hi"), 100)
    large = context_window_status("moonshot", "moonshot-v1-8k", _msg("中" * 4000), 100)
    assert small.fits is True
    assert small.estimate_method == "utf8_bytes_conservative_estimate"
    assert large.fits is False
    assert large.remaining_input_tokens < 0


def test_tiktoken_estimate_and_output_limit_are_explicit():
    status = context_window_status("openai", "gpt-4o-mini", _msg("你好" * 100), 1024)
    assert status.fits is True
    assert status.estimate_method == "tiktoken_chat_estimate"
    assert status.estimated_input_tokens > 0
    too_large_output = context_window_status("openai", "gpt-4o-mini", _msg("hi"), 20000)
    assert too_large_output.fits is False
    assert too_large_output.reason == "output_limit_exceeded"


def test_default_qwen_preset_has_a_bounded_input_budget(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_MODEL_NAME", "qwen-plus")
    cfg = get_provider_config("qwen")
    status = context_window_status(cfg["provider"], cfg["model"], _msg("中文简历" * 100), 4096)
    assert status.fits is True
    assert status.context_window_tokens == 131072
    assert status.input_limit_tokens == 98304
    assert status.estimate_method == "utf8_bytes_conservative_estimate"


def test_failover_rechecks_the_smaller_model_and_skips_overflow(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4o-mini")
    client = LLMClient(failover=["openai", "qwen-max"])
    calls = []

    def fail_first(provider, *_args, **_kwargs):
        calls.append(provider)
        raise ConnectionError("connection failed")

    monkeypatch.setattr(client, "_chat_once", fail_first)
    # ASCII tokens fit OpenAI's 128k window, but the same payload cannot fit
    # qwen-max's 32k context after the first provider fails.
    with pytest.raises(RuntimeError, match="estimated_input_over_window"):
        client.chat(_msg("a" * 35000), max_tokens=1024)
    assert calls == ["openai"]
    assert client.last_context_status.provider == "qwen-max"
    assert client.last_context_status.fits is False


def test_chat_metadata_passes_provider_usage_through(monkeypatch):
    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=19, completion_tokens=7, total_tokens=26),
        model="gpt-4o-mini",
    )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)))
    monkeypatch.setattr(client, "_get_openai", lambda _provider: (fake, client._resolve_config("openai")))
    result = client.chat_with_metadata(_msg("hello"), max_tokens=100)
    assert result.text == "answer"
    assert result.usage == {"prompt_tokens": 19, "completion_tokens": 7, "total_tokens": 26}
    assert result.context_status.fits is True
    assert client.last_usage == result.usage


def test_unknown_model_override_never_reuses_preset_window(monkeypatch):
    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    monkeypatch.setattr(client, "_chat_once", lambda *_args, **_kwargs: pytest.fail("unexpected network dispatch"))
    with pytest.raises(RuntimeError, match="model_capability_unknown"):
        client.chat(_msg("hello"), model_override="unlisted-model", max_tokens=100)


def test_finish_reason_length_is_reported_as_error(monkeypatch):
    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="partial"), finish_reason="length")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=100, total_tokens=110),
        model="gpt-4o-mini",
    )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)))
    monkeypatch.setattr(client, "_get_openai", lambda _provider: (fake, client._resolve_config("openai")))
    with pytest.raises(RuntimeError, match="output truncated"):
        client.chat(_msg("hello"), max_tokens=100)
