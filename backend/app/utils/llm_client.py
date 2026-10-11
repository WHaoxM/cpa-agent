"""
LLM 客户端封装
统一使用 OpenAI SDK 格式；支持多厂商故障转移（LLM_FAILOVER_ORDER）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

from openai import OpenAI
import httpx

from config.integration_loader import get_failover_order, get_provider_config
from ..config import Config
from .logger import get_logger
from ..services.agent_runtime.budget import (
    ContextWindowStatus, context_window_status, record_model_call,
    require_turn_token_capacity,
)

logger = get_logger("cpa-agent.llm")


@dataclass(frozen=True)
class LLMChatResult:
    text: str
    provider: str
    model: str
    usage: Optional[Dict[str, int]]
    context_status: ContextWindowStatus
    finish_reason: Optional[str]


def _usage_dict(usage: Any) -> Optional[Dict[str, int]]:
    if usage is None:
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    result = {}
    for key in keys:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        if isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
    return result or None


def _is_failover_error(exc: BaseException) -> bool:
    """余额不足 / 鉴权 / 限流 / 超时 / 连接类错误 → 换厂商。"""
    msg = str(exc).lower()
    markers = (
        "insufficient balance",
        "402",
        "401",
        "403",
        "429",
        "rate limit",
        "timeout",
        "timed out",
        "connection",
        "temporarily unavailable",
        "overloaded",
        "service unavailable",
        "503",
        "502",
        "invalid_api_key",
        "authentication",
    )
    return any(m in msg for m in markers)


class LLMClient:
    """LLM 客户端 — OpenAI 兼容 API，按 failover 链轮询厂商。"""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        failover: Optional[List[str]] = None,
    ):
        self._explicit = {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "timeout": timeout,
        }
        if provider and (api_key or base_url or model):
            # 显式单厂商（测试/工具调用）
            self.failover_order = [provider]
        else:
            self.failover_order = failover or get_failover_order()

        # Pattern-upgrade: optional LiteLLM OpenAI-compatible gateway as primary hop
        try:
            from ..services.llmops import litellm_config, litellm_enabled

            if litellm_enabled() and "litellm" not in self.failover_order:
                self.failover_order = ["litellm"] + list(self.failover_order)
        except Exception:
            pass

        self._clients: Dict[str, Tuple[OpenAI, Dict[str, Any]]] = {}
        # 兼容旧属性：指向链上第一个可用厂商
        first = self._resolve_config(self.failover_order[0])
        self.provider = first["provider"]
        self.api_key = first["api_key"]
        self.base_url = first["base_url"]
        self.model = first["model"]
        self.timeout = first["timeout"]
        # A per-call LLMChatResult is authoritative; these are best-effort
        # compatibility diagnostics and are not safe for concurrent callers.
        self.last_usage: Optional[Dict[str, int]] = None
        self.last_context_status: Optional[ContextWindowStatus] = None

        if not any(self._resolve_config(p).get("api_key") for p in self.failover_order):
            raise ValueError("LLM_API_KEY 未配置（含 failover 厂商专用密钥）")

        logger.info(
            "LLMClient failover=%s primary=%s model=%s",
            self.failover_order,
            self.provider,
            self.model,
        )

    def _resolve_config(self, provider: str) -> Dict[str, Any]:
        if provider == "litellm":
            from ..services.llmops import litellm_config

            lc = litellm_config()
            return {
                "provider": "litellm",
                "api_key": lc["api_key"],
                "base_url": lc["base_url"],
                "model": lc["model"],
                "timeout": lc["timeout"],
            }
        cfg = get_provider_config(provider)
        if self._explicit.get("provider") == provider:
            if self._explicit.get("api_key"):
                cfg["api_key"] = self._explicit["api_key"]
            if self._explicit.get("base_url"):
                cfg["base_url"] = self._explicit["base_url"]
            if self._explicit.get("model"):
                cfg["model"] = self._explicit["model"]
            if self._explicit.get("timeout"):
                cfg["timeout"] = self._explicit["timeout"]
        elif self._explicit.get("timeout") and not cfg.get("timeout"):
            cfg["timeout"] = self._explicit["timeout"]
        return cfg

    def _get_openai(self, provider: str) -> Tuple[OpenAI, Dict[str, Any]]:
        if provider in self._clients:
            return self._clients[provider]
        cfg = self._resolve_config(provider)
        if not cfg.get("api_key"):
            raise ValueError(f"provider={provider} 未配置 API Key")
        if not cfg.get("base_url"):
            raise ValueError(f"provider={provider} 未配置 base_url")
        if not cfg.get("model"):
            raise ValueError(f"provider={provider} 未配置 model")
        client = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=httpx.Timeout(cfg["timeout"], connect=10.0),
            max_retries=0,  # Hidden SDK retries have no independently reportable usage.
        )
        self._clients[provider] = (client, cfg)
        return client, cfg

    def _chat_once(
        self,
        provider: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, Optional[Dict[str, int]], str, Optional[str]]:
        client, cfg = self._get_openai(provider)
        params: Dict[str, Any] = {
            "model": model_override or cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            params["response_format"] = response_format
        response = client.chat.completions.create(**params)
        content = response.choices[0].message.content
        usage = _usage_dict(getattr(response, "usage", None))
        actual_model = getattr(response, "model", None) or params["model"]
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        if content is None:
            logger.warning("LLM 返回空内容 provider=%s", provider)
            content = ""
        return re.sub(r"<think>[\s\S]*?</think>", "", content).strip(), usage, actual_model, finish_reason

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        model_override: Optional[str] = None,
    ) -> str:
        """Backward-compatible text API. Use chat_with_metadata for usage."""
        return self.chat_with_metadata(
            messages, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, model_override=model_override,
        ).text

    def chat_with_metadata(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        model_override: Optional[str] = None,
    ) -> LLMChatResult:
        """Recompute the context budget for every failover model and pass usage through."""
        self.last_usage = None
        self.last_context_status = None
        preview = ""
        try:
            preview = str(messages[-1].get("content") or "")[:500] if messages else ""
        except Exception:
            preview = ""

        try:
            from ..services.llmops import trace_generation
        except Exception:
            trace_generation = None  # type: ignore

        def _run() -> LLMChatResult:
            errors: List[str] = []
            for provider in self.failover_order:
                try:
                    cfg = self._resolve_config(provider)
                    if not cfg.get("api_key"):
                        errors.append(f"{provider}: no api key")
                        continue
                    target_model = model_override or cfg["model"]
                    status = context_window_status(provider, target_model, messages, max_tokens)
                    self.last_context_status = status
                    if status.fits is not True:
                        errors.append(f"{provider}/{target_model}: {status.reason}")
                        continue
                    require_turn_token_capacity(max_tokens, estimated_input_tokens=status.estimated_input_tokens or 0)
                    try:
                        text, usage, actual_model, finish_reason = self._chat_once(
                            provider, messages, temperature, max_tokens, response_format,
                            model_override=model_override,
                        )
                    except Exception:
                        # A dispatched request may have consumed tokens even when
                        # the transport returns no usage. Never retry on this turn.
                        record_model_call(
                            provider=provider, model=target_model, stage="llm_client.chat",
                            usage=None, preflight=status, finish_reason="provider_error",
                        )
                        require_turn_token_capacity(0)
                        raise
                    record_model_call(
                        provider=provider, model=actual_model, stage="llm_client.chat",
                        usage=usage, preflight=status, finish_reason=finish_reason,
                    )
                    require_turn_token_capacity(0)
                    if actual_model != target_model:
                        raise RuntimeError(
                            f"provider_model_mismatch: requested={target_model} actual={actual_model}"
                        )
                    if finish_reason == "length":
                        raise RuntimeError(f"LLM output truncated provider={provider} model={actual_model}")
                    self.provider = provider
                    self.model = actual_model
                    self.base_url = cfg["base_url"]
                    self.last_usage = usage
                    logger.info("LLM OK provider=%s model=%s usage=%s", provider, actual_model, usage)
                    return LLMChatResult(text, provider, actual_model, usage, status, finish_reason)
                except Exception as e:
                    logger.error("LLM 调用失败 provider=%s: %s", provider, e)
                    errors.append(f"{provider}: {e}")
                    if _is_failover_error(e):
                        continue
                    raise
            raise RuntimeError("全部 LLM 厂商失败或上下文预算不可判: " + " | ".join(errors))

        if trace_generation is None:
            return _run()
        with trace_generation("llm.chat", metadata={"max_tokens": max_tokens}, input_preview=preview) as bag:
            result = _run()
            bag["output"] = result.text
            bag["usage"] = result.usage
            bag["end_metadata"] = {
                "provider": result.provider, "model": result.model,
                "usage": result.usage, "context_reason": result.context_status.reason,
            }
            return result

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        cleaned = response.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM 返回的 JSON 格式无效: {cleaned[:500]}") from e

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """流式：只用当前链上第一个可用厂商（流式中途切换成本高）。"""
        self.last_usage = None
        self.last_context_status = None
        last_err: Optional[Exception] = None
        skipped: List[str] = []
        for provider in self.failover_order:
            emitted = False
            dispatched = False
            recorded = False
            status = None
            try:
                client, cfg = self._get_openai(provider)
                status = context_window_status(provider, cfg["model"], messages, max_tokens)
                self.last_context_status = status
                if status.fits is not True:
                    skipped.append(f"{provider}/{cfg['model']}: {status.reason}")
                    continue
                require_turn_token_capacity(max_tokens, estimated_input_tokens=status.estimated_input_tokens or 0)
                dispatched = True
                response = client.chat.completions.create(
                    model=cfg["model"],
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                self.provider = provider
                self.model = cfg["model"]
                self.last_usage = None
                finish_reason = None
                for chunk in response:
                    observed_usage = _usage_dict(getattr(chunk, "usage", None))
                    if observed_usage is not None:
                        self.last_usage = observed_usage
                    if chunk.choices:
                        finish_reason = getattr(chunk.choices[0], "finish_reason", None) or finish_reason
                    if chunk.choices and chunk.choices[0].delta.content:
                        emitted = True
                        yield chunk.choices[0].delta.content
                record_model_call(
                    provider=provider, model=cfg["model"], stage="llm_client.stream",
                    usage=self.last_usage, preflight=status, finish_reason=finish_reason,
                )
                recorded = True
                require_turn_token_capacity(0)
                if finish_reason == "length":
                    raise RuntimeError(f"LLM stream output truncated provider={provider} model={cfg['model']}")
                return
            except Exception as e:
                last_err = e
                if dispatched and not recorded:
                    record_model_call(
                        provider=provider, model=cfg["model"], stage="llm_client.stream",
                        usage=self.last_usage, preflight=status,
                        finish_reason="stream_error",
                    )
                    require_turn_token_capacity(0)
                if not emitted and _is_failover_error(e):
                    logger.error("stream failover provider=%s: %s", provider, e)
                    continue
                raise
        raise RuntimeError(f"全部 LLM 厂商流式失败或预算不可判: {last_err}; {' | '.join(skipped)}")


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client() -> None:
    """测试或热更新密钥后重置单例。"""
    global _llm_client
    _llm_client = None
