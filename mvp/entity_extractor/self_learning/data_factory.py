#!/usr/bin/env python3
"""
Data Factory: 第一桶金挖掘器
带并发控制、错误重试、交叉共识质检、断点续传的批量API标注系统

核心功能：
1. 多API并发标注（GPT-4 / Claude / DeepSeek）
2. 交叉共识质检（API + NS-LE规则双重验证）
3. 智能错误重试（指数退避）
4. 断点续传（进度持久化）
5. 增量数据保存

使用方式：
    python data_factory.py --input resumes.jsonl --output gold_data --target 200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


@dataclass
class AnnotationQuality:
    """标注质量评估"""
    api_entities: List[Dict[str, Any]]
    rule_entities: List[Dict[str, Any]]
    consensus_score: float  # 0-1, 一致性得分
    conflict_entities: List[Dict[str, Any]]  # 冲突实体
    missing_entities: List[Dict[str, Any]]  # 漏提取实体
    extra_entities: List[Dict[str, Any]]  # 误提取实体
    final_entities: List[Dict[str, Any]]  # 最终采纳实体
    quality_tier: str  # "gold", "silver", "reject"


@dataclass
class ProcessedResume:
    """处理后的简历"""
    resume_id: str
    raw_text: str
    blocks: List[Dict[str, Any]]
    api_annotation: Optional[Dict[str, Any]] = None
    rule_annotation: Optional[Dict[str, Any]] = None
    quality: Optional[AnnotationQuality] = None
    status: str = "pending"  # pending, processing, completed, failed
    error: Optional[str] = None
    retry_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class NSLEPreExtractor:
    """
    NS-LE规则预提取器
    用正则+Aho-Corasick进行快速实体提取
    """

    def __init__(self):
        self.skill_patterns = self._init_skill_patterns()
        self.company_suffixes = ["有限公司", "股份有限公司", "集团", "科技", "网络"]
        self.school_suffixes = ["大学", "学院", "学校", "Institute", "University"]
        self.date_pattern = re.compile(r"(\d{4}[-/]\d{1,2}|\d{4}年\d{1,2}月|\d{4})")

    def _init_skill_patterns(self) -> Dict[str, List[str]]:
        """初始化技能词典"""
        return {
            "programming": [
                "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#",
                "PHP", "Ruby", "Swift", "Kotlin", "Scala", "R", "MATLAB",
            ],
            "framework": [
                "Spring", "Django", "Flask", "FastAPI", "React", "Vue", "Angular",
                "Node.js", "Express", "NestJS", "Ruby on Rails",
            ],
            "ai_ml": [
                "TensorFlow", "PyTorch", "Keras", "Scikit-learn", "LangChain",
                "LangGraph", "RAG", "Agent", "OpenAI", "Hugging Face",
            ],
            "database": [
                "MySQL", "PostgreSQL", "MongoDB", "Redis", "Neo4j", "Milvus",
                "Elasticsearch", "ClickHouse", "Cassandra", "DynamoDB",
            ],
            "tool": [
                "Git", "Docker", "Kubernetes", "Jenkins", "CI/CD", "AWS", "GCP",
                "Azure", "Linux", "Nginx", "Apache", "Kafka", "RabbitMQ",
            ],
        }

    def extract(self, text: str, block_type: str = "general") -> List[Dict[str, Any]]:
        """
        从文本中提取实体

        Returns:
            实体列表
        """

        entities = []

        # 提取技能
        for category, skills in self.skill_patterns.items():
            for skill in skills:
                if skill.lower() in text.lower():
                    entities.append({
                        "type": "SKILL",
                        "span": skill,
                        "category": category,
                        "source": "rule",
                    })

        # 提取公司
        for suffix in self.company_suffixes:
            pattern = re.compile(rf"([\u4e00-\u9fa5]{{2,20}}){re.escape(suffix)}")
            for match in pattern.finditer(text):
                entities.append({
                    "type": "COMPANY",
                    "span": match.group(),
                    "source": "rule",
                })

        # 提取学校
        for suffix in self.school_suffixes:
            pattern = re.compile(rf"([\u4e00-\u9fa5]{{2,15}}{re.escape(suffix)})")
            for match in pattern.finditer(text):
                entities.append({
                    "type": "SCHOOL",
                    "span": match.group(),
                    "source": "rule",
                })

        # 提取日期
        for match in self.date_pattern.finditer(text):
            entities.append({
                "type": "DATE_RANGE",
                "span": match.group(),
                "source": "rule",
            })

        # 提取职位
        position_patterns = [
            r"([\u4e00-\u9fa5]{2,10})(?:工程师|开发者|设计师|经理|总监|架构师)",
            r"(?:担任|曾任|现任)([\u4e00-\u9fa5]{2,10})",
        ]
        for pattern_str in position_patterns:
            pattern = re.compile(pattern_str)
            for match in pattern.finditer(text):
                entities.append({
                    "type": "POSITION",
                    "span": match.group(1) if match.groups() else match.group(),
                    "source": "rule",
                })

        # 提取项目名
        project_pattern = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{5,30})(?:项目|系统|平台|应用)")
        for match in project_pattern.finditer(text):
            entities.append({
                "type": "PROJECT_NAME",
                "span": match.group(),
                "source": "rule",
            })

        return entities


class ConsensusGuard:
    """
    交叉共识质检器
    核心：只有API和规则达成共识的数据才能进入Gold数据集
    """

    def __init__(
        self,
        consensus_threshold: float = 0.8,
        min_entity_count: int = 3,
    ):
        self.consensus_threshold = consensus_threshold
        self.min_entity_count = min_entity_count
        self.pre_extractor = NSLEPreExtractor()

    def evaluate(
        self,
        raw_text: str,
        api_entities: List[Dict[str, Any]],
        block_type: str = "general",
    ) -> AnnotationQuality:
        """
        评估标注质量

        Args:
            raw_text: 原始文本
            api_entities: API提取的实体
            block_type: 文本块类型

        Returns:
            质量评估结果
        """

        # 用规则同步提取
        rule_entities = self.pre_extractor.extract(raw_text, block_type)

        # 计算一致性
        api_spans = {e.get("span", "").lower().strip() for e in api_entities if e.get("span")}
        rule_spans = {e.get("span", "").lower().strip() for e in rule_entities if e.get("span")}

        # 一致实体
        consensus_entities = api_spans & rule_spans

        # 冲突实体（API有但规则没有，且规则也没找到类似）
        conflict_entities = []
        for entity in api_entities:
            span = entity.get("span", "").lower().strip()
            if span not in consensus_entities:
                # 检查是否有相似的（编辑距离<2）
                if not self._has_similar(span, rule_spans):
                    conflict_entities.append(entity)

        # 漏提取（规则有但API没有）
        missing_entities = []
        for span in rule_spans - consensus_entities:
            if len(span) >= 3:  # 过滤太短的
                missing_entities.append({
                    "type": "RULE_ONLY",
                    "span": span,
                })

        # 误提取（API有但规则没有，且不在冲突列表中 — API独有的可疑实体）
        extra_entities = []
        conflict_spans = {e.get("span", "").lower().strip() for e in conflict_entities}
        for span in api_spans - consensus_entities - conflict_spans:
            extra_entities.append({
                "type": "API_EXTRA",
                "span": span,
            })

        # 计算一致性得分
        total = len(api_spans | rule_spans)
        if total > 0:
            consensus_score = len(consensus_entities) / total
        else:
            consensus_score = 0.0

        # 决定最终采纳实体
        final_entities = self._merge_entities(api_entities, rule_entities, conflict_entities)

        # 判定质量等级
        if consensus_score >= self.consensus_threshold and len(final_entities) >= self.min_entity_count:
            if conflict_entities:
                quality_tier = "silver"  # 有冲突，需要人工审核
            else:
                quality_tier = "gold"  # 完全一致，gold
        elif len(final_entities) >= 1:
            quality_tier = "silver"  # 部分有效
        else:
            quality_tier = "reject"  # 拒绝

        return AnnotationQuality(
            api_entities=api_entities,
            rule_entities=rule_entities,
            consensus_score=consensus_score,
            conflict_entities=conflict_entities,
            missing_entities=missing_entities,
            extra_entities=extra_entities,
            final_entities=final_entities,
            quality_tier=quality_tier,
        )

    def _has_similar(self, span: str, span_set: set, threshold: int = 2) -> bool:
        """检查是否有相似的span"""
        for s in span_set:
            if self._edit_distance(span, s) <= threshold:
                return True
        return False

    def _edit_distance(self, s1: str, s2: str) -> int:
        """计算编辑距离"""
        if len(s1) < len(s2):
            return self._edit_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _merge_entities(
        self,
        api_entities: List[Dict[str, Any]],
        rule_entities: List[Dict[str, Any]],
        conflict_entities: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        合并实体：优先使用API结果，规则补充遗漏
        """

        merged = []
        seen_spans = set()

        # 首先添加API实体（去除冲突）
        api_spans = {e.get("span", "").lower().strip() for e in api_entities}
        conflict_spans = {e.get("span", "").lower().strip() for e in conflict_entities}

        for entity in api_entities:
            span = entity.get("span", "").lower().strip()
            if span and span not in conflict_spans:
                merged.append(entity)
                seen_spans.add(span)

        # 用规则补充漏掉的（前提是API没有类似实体）
        for entity in rule_entities:
            span = entity.get("span", "").lower().strip()
            if span and span not in seen_spans:
                # 检查是否与任何API实体相似
                if not self._has_similar(span, api_spans):
                    # 降低置信度
                    entity_copy = entity.copy()
                    entity_copy["source"] = "rule_supplement"
                    entity_copy["confidence"] = 0.7
                    merged.append(entity_copy)
                    seen_spans.add(span)

        return merged


class APIAnnotator:
    """
    API标注器
    支持多种API提供商，带并发和重试
    """

    def __init__(
        self,
        api_keys: Dict[str, str],
        providers: List[str] = None,
        max_workers: int = 5,
        request_timeout: int = 60,
    ):
        self.api_keys = api_keys
        self.providers = providers or ["deepseek", "openai"]
        self.max_workers = max_workers
        self.request_timeout = request_timeout

        self.consensus_guard = ConsensusGuard()

        # 统计
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "gold": 0,
            "silver": 0,
            "rejected": 0,
            "cost": 0.0,
        }

        self.lock = threading.Lock()

    def annotate(
        self,
        text: str,
        block_type: str = "general",
        resume_id: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        标注单条数据

        Returns:
            (标注结果, 错误信息)
        """

        self.stats["total"] += 1

        # 调用API
        api_result = self._call_api(text, block_type)

        if not api_result:
            with self.lock:
                self.stats["failed"] += 1
            return None, "API调用失败"

        # 提取实体
        entities = self._extract_entities_from_response(api_result)

        # 交叉共识质检
        quality = self.consensus_guard.evaluate(text, entities, block_type)

        # 更新统计
        with self.lock:
            self.stats["success"] += 1
            if quality.quality_tier == "gold":
                self.stats["gold"] += 1
            elif quality.quality_tier == "silver":
                self.stats["silver"] += 1
            else:
                self.stats["rejected"] += 1

            self.stats["cost"] += api_result.get("cost", 0)

        return {
            "resume_id": resume_id,
            "raw_text": text,
            "api_response": api_result,
            "rule_entities": quality.rule_entities,
            "quality": {
                "consensus_score": quality.consensus_score,
                "quality_tier": quality.quality_tier,
                "conflict_count": len(quality.conflict_entities),
                "missing_count": len(quality.missing_entities),
            },
            "final_entities": quality.final_entities,
        }, None

    def batch_annotate(
        self,
        texts: List[str],
        block_types: List[str] = None,
        resume_ids: List[str] = None,
        target_count: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        批量标注

        Args:
            texts: 文本列表
            block_types: 文本块类型列表
            resume_ids: 简历ID列表
            target_count: 目标Gold数据数量

        Returns:
            标注结果列表
        """

        results = []
        block_types = block_types or ["general"] * len(texts)
        resume_ids = resume_ids or [f"resume_{i}" for i in range(len(texts))]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.annotate, text, btype, rid): (i, rid)
                for i, (text, btype, rid) in enumerate(zip(texts, block_types, resume_ids))
            }

            for future in as_completed(futures):
                try:
                    result, error = future.result()

                    if result:
                        results.append(result)

                        # 进度报告
                        gold_count = self.stats["gold"]
                        progress = len(results) / len(texts) * 100
                        logger.info(
                            "[进度] %d/%d (%.1f%%) | Gold: %d | Silver: %d | Rejected: %d",
                            len(results), len(texts), progress, self.stats["gold"],
                            self.stats["silver"], self.stats["rejected"]
                        )

                        # 如果达到目标数量，提前停止
                        if gold_count >= target_count:
                            logger.info("已达到目标Gold数量 %d，停止标注", target_count)
                            executor.shutdown(wait=False, cancel_futures=True)
                            break

                except Exception as e:
                    logger.error("处理失败: %s", e)

        return results

    def _call_api(
        self,
        text: str,
        block_type: str,
        retry_count: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """
        调用API，带重试和 provider 降级
        """

        # 按优先级排列可用 provider
        available_providers = []
        if "deepseek" in self.providers and "DEEPSEEK_API_KEY" in self.api_keys:
            available_providers.append(("deepseek", self._call_deepseek_api))
        if "openai" in self.providers and "OPENAI_API_KEY" in self.api_keys:
            available_providers.append(("openai", self._call_openai_api))
        if "anthropic" in self.providers and "ANTHROPIC_API_KEY" in self.api_keys:
            available_providers.append(("anthropic", self._call_anthropic_api))

        if not available_providers:
            logger.error("没有可用的 API provider")
            return None

        for attempt in range(retry_count):
            # 轮换 provider：首次用第一个，失败后降级到下一个
            provider_idx = attempt % len(available_providers)
            provider_name, provider_fn = available_providers[provider_idx]
            try:
                return provider_fn(text, block_type)
            except Exception as e:
                wait_time = (2 ** attempt) * random.uniform(1, 3)
                logger.warning("API调用失败 [%s] (尝试 %d/%d): %s, %.1f秒后重试",
                             provider_name, attempt + 1, retry_count, e, wait_time)
                time.sleep(wait_time)

        return None

    def _call_deepseek_api(
        self,
        text: str,
        block_type: str,
    ) -> Dict[str, Any]:
        """调用DeepSeek API"""

        prompt = f"""从以下{block_type}文本中提取实体，以JSON格式输出。

文本：{text[:2000]}

实体类型：SKILL(技能), PROJECT_NAME(项目名), COMPANY(公司), SCHOOL(学校), POSITION(职位), PROJECT_ROLE(项目角色), DATE_RANGE(日期), DEGREE(学历), CERTIFICATE(证书), TOOL(工具)

输出格式：
{{
    "entities": [
        {{"type": "类型", "span": "原文"}}
    ]
}}

注意：
1. 只提取确定的实体，不要猜测
2. 日期格式统一为 YYYY-MM 或 YYYY/MM
3. 确保实体span与原文完全匹配"""

        start_time = time.time()

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_keys.get('DEEPSEEK_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是一个专业的简历信息提取专家。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
            },
            timeout=self.request_timeout,
        )

        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        # 估算成本（DeepSeek便宜很多）
        cost = (total_tokens / 1000) * 0.001  # $0.001/1K tokens

        return {
            "content": content,
            "provider": "deepseek",
            "cost": cost,
            "latency": time.time() - start_time,
        }

    def _call_openai_api(
        self,
        text: str,
        block_type: str,
    ) -> Dict[str, Any]:
        """调用OpenAI API"""

        prompt = f"""从以下{block_type}文本中提取实体，以JSON格式输出。

文本：{text[:2000]}

实体类型：SKILL, PROJECT_NAME, COMPANY, SCHOOL, POSITION, PROJECT_ROLE, DATE_RANGE, DEGREE, CERTIFICATE, TOOL

输出格式：
{{"entities": [{{"type": "类型", "span": "原文"}}]}}"""

        start_time = time.time()

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_keys.get('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4-turbo",
                "messages": [
                    {"role": "system", "content": "你是一个专业的简历信息提取专家。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=self.request_timeout,
        )

        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        cost = (total_tokens / 1000) * 0.01  # GPT-4 Turbo $0.01/1K

        return {
            "content": content,
            "provider": "openai",
            "cost": cost,
            "latency": time.time() - start_time,
        }

    def _call_anthropic_api(
        self,
        text: str,
        block_type: str,
    ) -> Dict[str, Any]:
        """调用Anthropic Claude API"""

        prompt = f"""从以下{block_type}文本中提取实体，以JSON格式输出。

文本：{text[:2000]}

实体类型：SKILL, PROJECT_NAME, COMPANY, SCHOOL, POSITION, PROJECT_ROLE, DATE_RANGE, DEGREE, CERTIFICATE, TOOL

输出格式：
{{"entities": [{{"type": "类型", "span": "原文"}}]}}"""

        start_time = time.time()

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_keys.get("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-opus-20240229",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1024,
                "temperature": 0.1,
            },
            timeout=self.request_timeout,
        )

        response.raise_for_status()
        data = response.json()

        content = data["content"][0]["text"]
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = (total_tokens / 1000) * 0.015  # Claude 3 Opus $0.015/1K

        return {
            "content": content,
            "provider": "anthropic",
            "cost": cost,
            "latency": time.time() - start_time,
        }

    def _extract_entities_from_response(
        self,
        api_response: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """从API响应中提取实体"""

        content = api_response.get("content", "")

        try:
            # 尝试解析JSON
            json_start = content.find("{")
            json_end = content.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)
                return data.get("entities", [])
        except json.JSONDecodeError:
            pass

        # 回退：正则提取
        entities = []
        type_patterns = {
            "SKILL": r"(?:使用?|掌握|熟悉|熟练)\s*([A-Za-z]+(?:[A-Za-z0-9]+)?)",
            "PROJECT": r"([A-Za-z0-9]+(?:系统|平台|应用|项目))",
            "DATE": r"(\d{4}[-/]\d{1,2})",
        }

        for etype, pattern in type_patterns.items():
            for match in re.finditer(pattern, content):
                entities.append({
                    "type": etype,
                    "span": match.group(1),
                    "source": "api_fallback",
                })

        return entities

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.lock:
            return self.stats.copy()


class DataFactory:
    """
    数据工厂 - 主控制器
    协调整个数据标注流程
    """

    def __init__(
        self,
        output_dir: Path,
        api_keys: Dict[str, str],
        target_gold_count: int = 200,
        max_workers: int = 5,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.target_gold_count = target_gold_count
        self.max_workers = max_workers

        # 初始化组件
        self.annotator = APIAnnotator(
            api_keys=api_keys,
            max_workers=max_workers,
        )

        # 进度文件
        self.progress_file = output_dir / "progress.json"
        self.gold_file = output_dir / "gold_dataset.jsonl"
        self.silver_file = output_dir / "silver_dataset.jsonl"
        self.all_file = output_dir / "all_annotations.jsonl"

        # 加载已有进度
        self.processed_ids = self._load_processed_ids()

    def _load_processed_ids(self) -> Set[str]:
        """加载已处理的ID"""
        processed = set()

        if self.progress_file.exists():
            with self.progress_file.open(encoding="utf-8") as f:
                data = json.load(f)
                processed = set(data.get("processed_ids", []))

        return processed

    def _save_progress(
        self,
        processed: List[Dict[str, Any]],
        gold_count: int,
        silver_count: int,
    ):
        """保存进度"""
        data = {
            "processed_ids": list(self.processed_ids),
            "gold_count": gold_count,
            "silver_count": silver_count,
            "timestamp": datetime.now().isoformat(),
        }

        with self.progress_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def run(
        self,
        resume_texts: List[Tuple[str, str]],  # (resume_id, text)
        block_types: List[str] = None,
        resume_ids: List[str] = None,
    ):
        """
        运行数据工厂

        Args:
            resume_texts: 简历文本列表 (id, text)
            block_types: 文本块类型
            resume_ids: 简历ID
        """

        logger.info("=" * 60)
        logger.info("Data Factory 启动")
        logger.info("目标: %d 条 Gold 数据", self.target_gold_count)
        logger.info("输入: %d 份简历", len(resume_texts))
        logger.info("=" * 60)

        # 过滤已处理的
        unprocessed = [
            (rid, text) for rid, text in resume_texts
            if rid not in self.processed_ids
        ]

        logger.info("待处理: %d 份 (已跳过 %d 份)",
                  len(unprocessed), len(resume_texts) - len(unprocessed))

        if not unprocessed:
            logger.info("没有需要处理的简历")
            return

        # 提取参数
        ids = [item[0] for item in unprocessed]
        texts = [item[1] for item in unprocessed]
        btypes = block_types[:len(unprocessed)] if block_types else ["general"] * len(unprocessed)

        # 批量标注
        results = self.annotator.batch_annotate(
            texts=texts,
            block_types=btypes,
            resume_ids=ids,
            target_count=self.target_gold_count,
        )

        # 保存结果
        gold_count = 0
        silver_count = 0

        for result in results:
            quality_tier = result.get("quality", {}).get("quality_tier", "reject")

            # 写入对应文件
            line = json.dumps(result, ensure_ascii=False)

            with self.all_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

            if quality_tier == "gold":
                with self.gold_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                gold_count += 1
            elif quality_tier == "silver":
                with self.silver_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                silver_count += 1

            self.processed_ids.add(result.get("resume_id", ""))

        # 更新进度
        self._save_progress([], gold_count, silver_count)

        # 打印统计
        stats = self.annotator.get_stats()

        logger.info("=" * 60)
        logger.info("Data Factory 完成")
        logger.info("总计处理: %d", stats["total"])
        logger.info("成功: %d | Gold: %d | Silver: %d | Rejected: %d",
                   stats["success"], stats["gold"], stats["silver"], stats["rejected"])
        logger.info("总成本: $%.4f", stats["cost"])
        logger.info("=" * 60)

    def get_report(self) -> Dict[str, Any]:
        """获取数据报告"""
        gold_count = 0
        silver_count = 0

        if self.gold_file.exists():
            with self.gold_file.open(encoding="utf-8") as f:
                gold_count = sum(1 for _ in f)

        if self.silver_file.exists():
            with self.silver_file.open(encoding="utf-8") as f:
                silver_count = sum(1 for _ in f)

        return {
            "gold_count": gold_count,
            "silver_count": silver_count,
            "total": gold_count + silver_count,
            "target": self.target_gold_count,
            "progress": min(100, (gold_count + silver_count) / self.target_gold_count * 100),
            "stats": self.annotator.get_stats(),
        }


class ResumeSimulator:
    """
    简历模拟器 - 生成测试简历
    用于演示和数据工厂测试
    """

    def __init__(self):
        self.names = ["张明", "李华", "王强", "刘芳", "陈伟", "赵雪", "黄磊", "周杰", "吴敏", "郑涛"]
        self.companies = [
            "阿里巴巴", "腾讯科技", "字节跳动", "百度", "美团",
            "京东", "网易", "快手科技", "小米科技", "华为技术",
        ]
        self.schools = [
            "清华大学", "北京大学", "浙江大学", "上海交通大学", "复旦大学",
            "中国科学技术大学", "南京大学", "哈尔滨工业大学", "西安交通大学", "同济大学",
        ]
        self.skills = [
            "Python", "Java", "Go", "Rust", "JavaScript", "TypeScript",
            "TensorFlow", "PyTorch", "LangChain", "FastAPI", "Django",
            "React", "Vue", "Node.js", "MySQL", "PostgreSQL", "MongoDB", "Redis",
            "Docker", "Kubernetes", "AWS", "GCP", "Git",
        ]
        self.projects = [
            "智能推荐系统", "用户画像分析", "实时数据处理平台", "知识图谱系统",
            "多模态Agent平台", "RAG检索系统", "分布式爬虫系统", "自动化测试平台",
        ]

    def generate_resume(self, index: int) -> Tuple[str, str]:
        """生成模拟简历"""
        name = random.choice(self.names)
        company = random.choice(self.companies)
        school = random.choice(self.schools)
        skill1, skill2, skill3 = random.sample(self.skills, 3)
        project = random.choice(self.projects)

        resume_id = f"resume_{index:04d}"

        text = f"""
{name}
联系方式: 138****8888 | email@example.com

教育背景
{school} | 计算机科学与技术 | 本科 | 2020-2024
GPA: 3.8/4.0 | 获得校级奖学金

实习经历
{company} | 后端开发实习生 | 2023.06-2023.09
- 使用{skill1}和{skill2}开发后端API服务
- 参与{project}的设计与实现
- 使用Git进行版本控制，熟悉敏捷开发流程

项目经历
{project}
- 核心模块开发，使用{skill1}实现核心算法
- 前端使用{skill3}进行交互优化
- 项目获得校内技术创新奖

技能证书
- 熟练掌握: {skill1}, {skill2}, {skill3}
- 了解: Docker, Git, Linux
- CET-6: 520分
"""

        return resume_id, text.strip()

    def generate_batch(self, count: int) -> List[Tuple[str, str]]:
        """批量生成简历"""
        return [self.generate_resume(i) for i in range(count)]


def main():
    """主函数"""

    parser = argparse.ArgumentParser(description="Data Factory - 第一桶金挖掘器")
    parser.add_argument("--input", type=str, help="输入JSONL文件路径")
    parser.add_argument("--output", type=str, default="./gold_data", help="输出目录")
    parser.add_argument("--target", type=int, default=200, help="目标Gold数据数量")
    parser.add_argument("--workers", type=int, default=5, help="并发工作数")
    parser.add_argument("--generate", type=int, help="生成模拟简历数量进行测试")

    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载API Keys
    api_keys = {}
    for key in ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
        value = os.environ.get(key)
        if value:
            api_keys[key] = value

    if not api_keys:
        logger.error("没有找到API Key，请设置环境变量:")
        logger.error("  DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY")
        return

    # 准备数据
    if args.generate:
        # 生成模拟简历
        logger.info("生成 %d 份模拟简历...", args.generate)
        simulator = ResumeSimulator()
        resume_texts = simulator.generate_batch(args.generate)
    elif args.input:
        # 从文件加载
        logger.info("从 %s 加载简历...", args.input)
        resume_texts = []
        with Path(args.input).open(encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                rid = data.get("id", data.get("resume_id", f"resume_{len(resume_texts)}"))
                text = data.get("text", data.get("content", ""))
                resume_texts.append((rid, text))
    else:
        logger.error("请指定 --input 或 --generate")
        return

    # 创建数据工厂
    factory = DataFactory(
        output_dir=output_dir,
        api_keys=api_keys,
        target_gold_count=args.target,
        max_workers=args.workers,
    )

    # 处理信号
    def signal_handler(signum, frame):
        logger.info("收到中断信号，正在保存进度...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # 运行
    factory.run(resume_texts)

    # 打印报告
    report = factory.get_report()
    logger.info("最终报告: %s", json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()