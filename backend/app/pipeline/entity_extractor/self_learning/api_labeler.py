"""
自主学习引擎：API标注器模块
使用GPT-4/Claude API进行高质量实体标注

核心职责：
1. 多API支持（OpenAI GPT-4 / Anthropic Claude / DeepSeek）
2. 智能prompt模板
3. 标注质量验证
4. 标注结果标准化
"""

from __future__ import annotations

import json
import logging
import time
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


class APIProvider(Enum):
    """支持的API提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    SILICONFLOW = "siliconflow"
    DASHSCOPE = "dashscope"


@dataclass
class AnnotationResult:
    """标注结果"""
    raw_text: str
    entities: List[Dict[str, Any]]
    relations: List[Dict[str, Any]]
    confidence: float
    api_provider: str
    processing_time: float
    cost: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "entities": self.entities,
            "relations": self.relations,
            "confidence": self.confidence,
            "api_provider": self.api_provider,
            "processing_time": self.processing_time,
            "cost": self.cost,
            "timestamp": self.timestamp,
        }


class BaseAnnotator(ABC):
    """标注器基类"""

    @abstractmethod
    def annotate(self, text: str, context: Optional[Dict[str, Any]] = None) -> AnnotationResult:
        """执行标注"""
        pass

    @abstractmethod
    def batch_annotate(self, texts: List[str], context: Optional[Dict[str, Any]] = None) -> List[AnnotationResult]:
        """批量标注"""
        pass

    def validate_annotation(self, result: AnnotationResult, ground_truth: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """验证标注质量"""
        if not result.entities:
            return False, "标注结果为空"

        if result.confidence < 0.7:
            return False, f"置信度过低: {result.confidence}"

        return True, "OK"


class OpenAIAnnotator(BaseAnnotator):
    """OpenAI GPT-4 标注器"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4-turbo",
        base_url: Optional[str] = None,
        cost_per_1k_tokens: float = 0.01,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or "https://api.openai.com/v1"
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.provider = APIProvider.OPENAI

    def annotate(self, text: str, context: Optional[Dict[str, Any]] = None) -> AnnotationResult:
        start_time = time.time()

        prompt = self._build_prompt(text, context)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # 估算成本
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            cost = (total_tokens / 1000) * self.cost_per_1k_tokens

            # 解析结果
            entities, relations = self._parse_response(content)

            processing_time = time.time() - start_time

            return AnnotationResult(
                raw_text=text,
                entities=entities,
                relations=relations,
                confidence=0.95,  # API标注默认高置信度
                api_provider=self.provider.value,
                processing_time=processing_time,
                cost=cost,
            )

        except Exception as e:
            logger.error("OpenAI标注失败: %s", e)
            processing_time = time.time() - start_time
            return AnnotationResult(
                raw_text=text,
                entities=[],
                relations=[],
                confidence=0.0,
                api_provider=self.provider.value,
                processing_time=processing_time,
                cost=0.0,
            )

    def batch_annotate(self, texts: List[str], context: Optional[Dict[str, Any]] = None) -> List[AnnotationResult]:
        results = []
        for text in texts:
            result = self.annotate(text, context)
            results.append(result)
            time.sleep(0.5)  # 避免速率限制
        return results

    def _build_prompt(self, text: str, context: Optional[Dict[str, Any]]) -> str:
        block_type = context.get("block_type", "通用") if context else "通用"

        prompt = f"""请从以下{block_type}文本中提取实体和关系。

文本内容：
{text}

请以JSON格式输出，结构如下：
{{
    "entities": [
        {{
            "type": "实体类型",
            "span": "原文中的具体文本",
            "start": 开始位置,
            "end": 结束位置
        }}
    ],
    "relations": [
        {{
            "head": "头实体",
            "tail": "尾实体",
            "type": "关系类型"
        }}
    ]
}}

实体类型包括：SKILL, PROJECT_NAME, COMPANY, SCHOOL, POSITION, PROJECT_ROLE, DATE_RANGE, DEGREE, CERTIFICATE, TOOL
关系类型包括：HAS_SKILL, BELONGS_TO, WORKED_AT, STUDIED_AT, HAS_ROLE, HAS_PERIOD

注意：
1. 只提取确定的实体，不要猜测
2. 日期格式统一为 YYYY-MM 或 YYYY/MM
3. 确保实体span与原文完全匹配
"""
        return prompt

    def _get_system_prompt(self) -> str:
        return """你是一个专业的简历信息提取专家，擅长从文本中准确提取实体和关系。
你的输出必须严格遵循JSON格式，不要包含任何其他解释性文字。"""

    def _parse_response(self, content: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        try:
            data = json.loads(content)
            entities = data.get("entities", [])
            relations = data.get("relations", [])
            return entities, relations
        except json.JSONDecodeError:
            logger.warning("JSON解析失败，尝试正则提取")
            return self._fallback_parse(content)


class AnthropicAnnotator(BaseAnnotator):
    """Anthropic Claude 标注器"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-opus-20240229",
        cost_per_1k_tokens: float = 0.015,
    ):
        self.api_key = api_key
        self.model = model
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.provider = APIProvider.ANTHROPIC

    def annotate(self, text: str, context: Optional[Dict[str, Any]] = None) -> AnnotationResult:
        start_time = time.time()

        prompt = self._build_prompt(text, context)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        }

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            content = data["content"][0]["text"]

            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_tokens = input_tokens + output_tokens
            cost = (total_tokens / 1000) * self.cost_per_1k_tokens

            entities, relations = self._parse_response(content)

            processing_time = time.time() - start_time

            return AnnotationResult(
                raw_text=text,
                entities=entities,
                relations=relations,
                confidence=0.97,
                api_provider=self.provider.value,
                processing_time=processing_time,
                cost=cost,
            )

        except Exception as e:
            logger.error("Anthropic标注失败: %s", e)
            processing_time = time.time() - start_time
            return AnnotationResult(
                raw_text=text,
                entities=[],
                relations=[],
                confidence=0.0,
                api_provider=self.provider.value,
                processing_time=processing_time,
                cost=0.0,
            )

    def batch_annotate(self, texts: List[str], context: Optional[Dict[str, Any]] = None) -> List[AnnotationResult]:
        results = []
        for text in texts:
            result = self.annotate(text, context)
            results.append(result)
            time.sleep(0.5)
        return results

    def _build_prompt(self, text: str, context: Optional[Dict[str, Any]]) -> str:
        block_type = context.get("block_type", "通用") if context else "通用"

        return f"""从以下{block_type}文本中提取实体和关系，以JSON格式输出。

文本：{text}

输出格式：
{{
    "entities": [{{"type": "类型", "span": "原文", "start": 0, "end": 10}}],
    "relations": [{{"head": "头实体", "tail": "尾实体", "type": "关系"}}]
}}

类型：SKILL, PROJECT_NAME, COMPANY, SCHOOL, POSITION, PROJECT_ROLE, DATE_RANGE, DEGREE, CERTIFICATE
关系：HAS_SKILL, BELONGS_TO, WORKED_AT, STUDIED_AT, HAS_ROLE, HAS_PERIOD"""


class APILabelingEngine:
    """
    API标注引擎 - 统一入口
    支持多种API提供商的智能路由和负载均衡
    """

    def __init__(self):
        self.annotators: Dict[APIProvider, BaseAnnotator] = {}
        self.usage_stats = {
            "total_calls": 0,
            "total_cost": 0.0,
            "total_tokens": 0,
            "provider_stats": {},
        }

    def register_annotator(self, provider: APIProvider, annotator: BaseAnnotator):
        """注册标注器"""
        self.annotators[provider] = annotator
        logger.info("已注册标注器: %s", provider.value)

    def annotate(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
        preferred_provider: Optional[APIProvider] = None,
    ) -> AnnotationResult:
        """执行标注，自动选择可用提供商"""

        if preferred_provider and preferred_provider in self.annotators:
            annotator = self.annotators[preferred_provider]
        else:
            # 轮询选择可用提供商
            available = [p for p in self.annotators.keys()]
            if not available:
                raise ValueError("没有可用的标注器")
            # 简单的负载均衡：选择成本最低的
            annotator = min(
                available,
                key=lambda p: self.usage_stats.get("provider_stats", {}).get(p.value, {}).get("avg_cost", float("inf"))
            )

        result = annotator.annotate(text, context)

        # 更新统计
        self._update_stats(result)

        return result

    def batch_annotate(
        self,
        texts: List[str],
        context: Optional[Dict[str, Any]] = None,
        preferred_provider: Optional[APIProvider] = None,
    ) -> List[AnnotationResult]:
        """批量标注"""
        results = []
        for text in texts:
            result = self.annotate(text, context, preferred_provider)
            results.append(result)
        return results

    def _update_stats(self, result: AnnotationResult):
        """更新使用统计"""
        self.usage_stats["total_calls"] += 1
        self.usage_stats["total_cost"] += result.cost

        provider = result.api_provider
        if provider not in self.usage_stats["provider_stats"]:
            self.usage_stats["provider_stats"][provider] = {
                "calls": 0,
                "cost": 0.0,
                "avg_cost": 0.0,
            }

        stats = self.usage_stats["provider_stats"][provider]
        stats["calls"] += 1
        stats["cost"] += result.cost
        stats["avg_cost"] = stats["cost"] / stats["calls"]

    def get_stats(self) -> Dict[str, Any]:
        """获取使用统计"""
        return self.usage_stats.copy()


class SelfLearningPipeline:
    """
    自主学习管道
    核心流程：API标注 → 数据清洗 → 规则学习 → 模型蒸馏
    """

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("./self_learning_data")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.labeling_engine = APILabelingEngine()
        self.gold_dataset: List[AnnotationResult] = []
        self.silver_dataset: List[AnnotationResult] = []

    def add_api_key(self, provider: APIProvider, api_key: str, **kwargs):
        """添加API密钥"""

        if provider == APIProvider.OPENAI:
            annotator = OpenAIAnnotator(api_key, **kwargs)
        elif provider == APIProvider.ANTHROPIC:
            annotator = AnthropicAnnotator(api_key, **kwargs)
        elif provider == APIProvider.DEEPSEEK:
            # DeepSeek使用类似的接口
            annotator = OpenAIAnnotator(api_key, base_url="https://api.deepseek.com/v1", **kwargs)
        else:
            raise ValueError(f"不支持的提供商: {provider}")

        self.labeling_engine.register_annotator(provider, annotator)

    def learn_from_resumes(
        self,
        resume_texts: List[str],
        contexts: Optional[List[Dict[str, Any]]] = None,
        provider: Optional[APIProvider] = APIProvider.OPENAI,
    ) -> Dict[str, Any]:
        """
        从简历数据中学习

        Args:
            resume_texts: 简历文本列表
            contexts: 每个简历的上下文信息
            provider: 首选API提供商

        Returns:
            学习统计信息
        """

        logger.info("开始自主学习，共 %d 个样本", len(resume_texts))

        # Step 1: API标注
        logger.info("[Step 1] API标注...")
        annotations = []
        for i, text in enumerate(resume_texts):
            ctx = contexts[i] if contexts and i < len(contexts) else None
            result = self.labeling_engine.annotate(text, ctx, provider)
            annotations.append(result)

            if (i + 1) % 10 == 0:
                logger.info("已标注: %d/%d", i + 1, len(resume_texts))

        # Step 2: 数据质量验证
        logger.info("[Step 2] 数据质量验证...")
        validated_annotations = []
        for ann in annotations:
            is_valid, msg = self.labeling_engine.validate_annotation(ann)
            if is_valid:
                validated_annotations.append(ann)
            else:
                logger.warning("标注验证失败: %s", msg)

        # Step 3: 分类为Gold/Silver数据集
        logger.info("[Step 3] 数据集分类...")
        gold_count = 0
        silver_count = 0

        for ann in validated_annotations:
            if ann.confidence >= 0.9:
                self.gold_dataset.append(ann)
                gold_count += 1
            else:
                self.silver_dataset.append(ann)
                silver_count += 1

        # Step 4: 保存数据集
        logger.info("[Step 4] 保存数据集...")
        self._save_datasets()

        # Step 5: 规则学习
        logger.info("[Step 5] 规则学习...")
        patterns = self._learn_patterns()

        stats = {
            "total_samples": len(resume_texts),
            "annotated": len(annotations),
            "validated": len(validated_annotations),
            "gold_count": gold_count,
            "silver_count": silver_count,
            "patterns_learned": len(patterns),
            "total_cost": self.labeling_engine.get_stats()["total_cost"],
        }

        logger.info("自主学习完成: %s", stats)

        return stats

    def _save_datasets(self):
        """保存Gold和Silver数据集"""

        gold_path = self.output_dir / "gold_dataset.jsonl"
        silver_path = self.output_dir / "silver_dataset.jsonl"

        with gold_path.open("w", encoding="utf-8") as f:
            for ann in self.gold_dataset:
                f.write(json.dumps(ann.to_dict(), ensure_ascii=False) + "\n")

        with silver_path.open("w", encoding="utf-8") as f:
            for ann in self.silver_dataset:
                f.write(json.dumps(ann.to_dict(), ensure_ascii=False) + "\n")

        logger.info("数据集已保存: Gold=%d, Silver=%d", len(self.gold_dataset), len(self.silver_dataset))

    def _learn_patterns(self) -> List[Dict[str, Any]]:
        """从标注数据中学习patterns"""

        patterns = {
            "skill_patterns": {},
            "entity_contexts": {},
            "relation_templates": {},
        }

        # 学习技能模式
        skill_keywords = {}
        for ann in self.gold_dataset:
            for entity in ann.entities:
                if entity.get("type") == "SKILL":
                    span = entity.get("span", "").strip()
                    if span:
                        # 记录技能及其上下文
                        context = ann.raw_text
                        if span in skill_keywords:
                            skill_keywords[span] += 1
                        else:
                            skill_keywords[span] = 1

        # 提取高频技能
        patterns["skill_patterns"] = {
            k: v for k, v in skill_keywords.items() if v >= 2
        }

        return [patterns]

    def get_training_data(self, data_type: str = "gold") -> List[Dict[str, Any]]:
        """获取训练数据"""

        if data_type == "gold":
            dataset = self.gold_dataset
        elif data_type == "silver":
            dataset = self.silver_dataset
        else:
            dataset = self.gold_dataset + self.silver_dataset

        return [ann.to_dict() for ann in dataset]


def create_self_learning_pipeline(api_keys: Dict[str, str]) -> SelfLearningPipeline:
    """
    创建自主学习管道工厂函数
    """

    pipeline = SelfLearningPipeline()

    # 注册OpenAI
    if "openai" in api_keys:
        pipeline.add_api_key(
            APIProvider.OPENAI,
            api_keys["openai"],
            model="gpt-4-turbo",
        )

    # 注册Anthropic
    if "anthropic" in api_keys:
        pipeline.add_api_key(
            APIProvider.ANTHROPIC,
            api_keys["anthropic"],
            model="claude-3-opus-20240229",
        )

    # 注册DeepSeek
    if "deepseek" in api_keys:
        pipeline.add_api_key(
            APIProvider.DEEPSEEK,
            api_keys["deepseek"],
        )

    return pipeline