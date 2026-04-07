"""
LLM 客户端封装
参考 MiroFish LLMClient, 统一使用 OpenAI SDK 格式调用
兼容 GPT-4o / DeepSeek-V3 / Qwen-Plus 等模型
"""

import json
import re
from typing import Optional, Dict, Any, List

from openai import OpenAI

from ..config import Config
from .logger import get_logger
from .retry import retry_with_backoff

logger = get_logger('cpa-agent.llm')


class LLMClient:
    """LLM 客户端 — 统一 OpenAI SDK 格式"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME

        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        logger.info(f"LLMClient 初始化: model={self.model}, base_url={self.base_url}")

    @retry_with_backoff(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表 [{"role": "system/user/assistant", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大 token 数
            response_format: 响应格式 (如 {"type": "json_object"})

        Returns:
            模型响应文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content

        # 移除部分模型 (DeepSeek-R1) 的 <think> 思考标签
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()

        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回 JSON

        自动清理 markdown 代码块标记, 解析 JSON

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            解析后的 JSON 对象
        """
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
        """
        流式聊天 (SSE 场景)

        Yields:
            文本片段
        """
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
