"""
LLM 客户端封装
统一使用 OpenAI SDK 格式调用
"""

import json
import re
from typing import Optional, Dict, Any, List

from openai import OpenAI

import httpx

from ..config import Config
from .logger import get_logger
from .retry import retry_with_backoff

logger = get_logger('cpa-agent.llm')


class LLMClient:
    """LLM 客户端 — 标准 OpenAI API 格式, 支持所有兼容提供商"""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None
    ):
        provider_config = Config.get_provider_config()

        self.provider = provider or provider_config['provider']
        self.api_key = api_key or provider_config['api_key']
        self.base_url = base_url or provider_config['base_url']
        self.model = model or provider_config['model']
        self.timeout = timeout or provider_config['timeout']

        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")

        if not self.base_url:
            raise ValueError("LLM_BASE_URL 未配置, 请设置 LLM_PROVIDER 或 LLM_BASE_URL")

        if not self.model:
            raise ValueError("LLM_MODEL_NAME 未配置, 请设置 LLM_PROVIDER 或 LLM_MODEL_NAME")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0)
        )

        logger.info(
            f"LLMClient 初始化: provider={self.provider}, "
            f"model={self.model}, base_url={self.base_url}, timeout={self.timeout}s"
        )

    @retry_with_backoff(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """发送聊天请求并返回文本"""
        try:
            params = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            if response_format:
                params["response_format"] = response_format
            
            response = self.client.chat.completions.create(**params)
            
            content = response.choices[0].message.content
            
            if content is None:
                logger.warning("LLM 返回空内容")
                return ""
            
            content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()

            return content

        except Exception as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                logger.error(f"LLM 请求超时 ({self.timeout}s): {e}")
            else:
                logger.error(f"LLM 调用失败: {e}")
            raise

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """发送聊天请求并返回 JSON"""
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )

        cleaned = response.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"LLM 返回的 JSON 格式无效: {cleaned[:500]}")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096
    ):
        """流式聊天 (SSE 场景)"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True
        )

        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
