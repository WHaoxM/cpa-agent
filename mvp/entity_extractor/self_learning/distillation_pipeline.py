"""
自主学习引擎：数据蒸馏管道
将API标注的高质量数据蒸馏到小模型

核心职责：
1. 数据格式化与清洗
2. 训练/验证/测试集划分
3. 对比学习蒸馏
4. 知识蒸馏训练
"""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import hashlib

import torch
from torch.utils.data import Dataset, DataLoader

logger = logging.getLogger(__name__)


@dataclass
class TrainingSample:
    """训练样本"""
    id: str
    input_text: str
    output_json: Dict[str, Any]
    entity_count: int
    source: str  # "gold" or "silver"
    difficulty: str  # "easy", "medium", "hard"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "input_text": self.input_text,
            "output_json": self.output_json,
            "entity_count": self.entity_count,
            "source": self.source,
            "difficulty": self.difficulty,
        }


class DataCleaner:
    """
    数据清洗器
    清洗API标注数据中的噪音
    """

    def __init__(self):
        self.invalid_patterns = [
            r"^\s+$",  # 空白文本
            r"^.{0,2}$",  # 太短的文本
        ]
        self.suspicious_entities = [
            "undefined", "null", "None", "none", "NaN",
        ]

    def clean(self, annotations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        清洗标注数据

        Args:
            annotations: 原始标注数据

        Returns:
            清洗后的数据
        """

        logger.info("开始数据清洗，共 %d 条数据", len(annotations))

        cleaned = []

        for ann in annotations:
            if self._is_valid(ann):
                cleaned_ann = self._clean_annotation(ann)
                if cleaned_ann:
                    cleaned.append(cleaned_ann)

        logger.info("清洗完成: %d -> %d", len(annotations), len(cleaned))

        return cleaned

    def _is_valid(self, ann: Dict[str, Any]) -> bool:
        """检查标注是否有效"""

        # 检查原始文本
        raw_text = ann.get("raw_text", "")
        for pattern in self.invalid_patterns:
            import re
            if re.match(pattern, raw_text):
                return False

        if not raw_text or len(raw_text) < 5:
            return False

        # 检查实体
        entities = ann.get("entities", [])
        if not entities:
            return False

        return True

    def _clean_annotation(self, ann: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """清洗单条标注"""

        cleaned_entities = []

        for entity in ann.get("entities", []):
            # 过滤无效实体
            span = entity.get("span", "").strip()
            if not span:
                continue

            if span in self.suspicious_entities:
                continue

            if len(span) < 2:
                continue

            cleaned_entities.append(entity)

        if not cleaned_entities:
            return None

        return {
            "raw_text": ann.get("raw_text", "").strip(),
            "entities": cleaned_entities,
            "relations": ann.get("relations", []),
            "confidence": ann.get("confidence", 0.0),
            "api_provider": ann.get("api_provider", ""),
        }


class DataFormatter:
    """
    数据格式化器
    将标注数据转换为模型训练格式
    """

    def __init__(self, prompt_template: Optional[str] = None):
        self.default_template = """从以下文本中提取实体，以JSON格式输出。

文本：{text}

实体类型：SKILL(技能), PROJECT_NAME(项目名), COMPANY(公司), SCHOOL(学校), POSITION(职位), PROJECT_ROLE(项目角色), DATE_RANGE(日期), DEGREE(学历), CERTIFICATE(证书), TOOL(工具)

输出格式：
{{
    "entities": [
        {{"type": "类型", "span": "原文", "start": 位置, "end": 位置}}
    ]
}}"""

        self.prompt_template = prompt_template or self.default_template

    def format_for_training(
        self,
        annotation: Dict[str, Any],
        include_prompt: bool = True,
    ) -> Tuple[str, str]:
        """
        格式化训练样本

        Returns:
            (input_text, target_text)
        """

        raw_text = annotation.get("raw_text", "")
        entities = annotation.get("entities", [])

        if include_prompt:
            input_text = self.prompt_template.format(text=raw_text)
        else:
            input_text = raw_text

        # 构建目标输出
        output = {
            "entities": entities,
        }
        target_text = json.dumps(output, ensure_ascii=False)

        return input_text, target_text

    def format_for_classification(
        self,
        annotation: Dict[str, Any],
        task: str = "entity_type",
    ) -> Tuple[str, str]:
        """
        格式化分类任务样本

        task: entity_type, relation_type, span_detection
        """

        raw_text = annotation.get("raw_text", "")
        entities = annotation.get("entities", [])

        if task == "entity_type":
            # 实体类型分类
            entity_list = "\n".join([
                f"- {e['span']}: {e['type']}" for e in entities
            ])
            input_text = f"文本：{raw_text}\n\n识别以下实体的类型："
            target_text = entity_list or "无实体"

        elif task == "span_detection":
            # 实体边界检测（简化版）
            input_text = raw_text
            target_text = "|".join([
                f"{e['span']}({e['type']})" for e in entities
            ]) or "无实体"

        else:
            input_text = raw_text
            target_text = json.dumps(entities, ensure_ascii=False)

        return input_text, target_text

    def format_for_distillation(
        self,
        annotation: Dict[str, Any],
        teacher_logits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        格式化蒸馏学习样本
        """

        input_text, target_text = self.format_for_training(annotation)

        sample = {
            "id": self._generate_id(annotation),
            "input": input_text,
            "target": target_text,
            "entities": annotation.get("entities", []),
            "confidence": annotation.get("confidence", 1.0),
        }

        if teacher_logits:
            sample["teacher_logits"] = teacher_logits

        return sample


class DistillationDataset(Dataset):
    """
    蒸馏数据集
    支持对比学习和标准蒸馏
    """

    def __init__(
        self,
        samples: List[TrainingSample],
        tokenizer,
        max_length: int = 512,
    ):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        sample = self.samples[idx]

        # tokenize input
        input_encoding = self.tokenizer(
            sample.input_text,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        # tokenize target
        target_encoding = self.tokenizer(
            sample.output_json,
            max_length=self.max_length // 2,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        return {
            "input_ids": input_encoding["input_ids"].squeeze(),
            "attention_mask": input_encoding["attention_mask"].squeeze(),
            "labels": target_encoding["input_ids"].squeeze(),
        }

    @staticmethod
    def generate_id(annotation: Dict[str, Any]) -> str:
        """生成样本ID"""
        text = annotation.get("raw_text", "")[:100]
        return hashlib.md5(text.encode()).hexdigest()[:12]


class TrainValSplitter:
    """
    训练集/验证集/测试集划分器
    """

    def __init__(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        seed: int = 42,
    ):
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio
        self.seed = seed

        random.seed(seed)

    def split(
        self,
        samples: List[TrainingSample],
    ) -> Tuple[List[TrainingSample], List[TrainingSample], List[TrainingSample]]:
        """
        划分数据集

        Returns:
            (train_set, val_set, test_set)
        """

        n = len(samples)
        indices = list(range(n))
        random.shuffle(indices)

        train_end = int(n * self.train_ratio)
        val_end = train_end + int(n * self.val_ratio)

        train_indices = indices[:train_end]
        val_indices = indices[train_end:val_end]
        test_indices = indices[val_end:]

        train_set = [samples[i] for i in train_indices]
        val_set = [samples[i] for i in val_indices]
        test_set = [samples[i] for i in test_indices]

        logger.info(
            "数据集划分: train=%d, val=%d, test=%d",
            len(train_set), len(val_set), len(test_set)
        )

        return train_set, val_set, test_set

    def split_by_difficulty(
        self,
        samples: List[TrainingSample],
    ) -> Tuple[List[TrainingSample], List[TrainingSample], List[TrainingSample]]:
        """
        按难度分层采样划分
        """

        easy = [s for s in samples if s.difficulty == "easy"]
        medium = [s for s in samples if s.difficulty == "medium"]
        hard = [s for s in samples if s.difficulty == "hard"]

        train_set, val_set, test_set = [], [], []

        for difficulty_samples in [easy, medium, hard]:
            if not difficulty_samples:
                continue

            n = len(difficulty_samples)
            indices = list(range(n))
            random.shuffle(indices)

            train_end = int(n * self.train_ratio)
            val_end = train_end + int(n * self.val_ratio)

            train_set.extend([difficulty_samples[i] for i in indices[:train_end]])
            val_set.extend([difficulty_samples[i] for i in indices[train_end:val_end]])
            test_set.extend([difficulty_samples[i] for i in indices[val_end:]])

        logger.info(
            "分层数据集划分: train=%d, val=%d, test=%d",
            len(train_set), len(val_set), len(test_set)
        )

        return train_set, val_set, test_set


class DistillationPipeline:
    """
    蒸馏管道 - 主控制器
    协调数据处理、格式化和导出
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
    ):
        self.output_dir = output_dir or Path("./distillation_data")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.cleaner = DataCleaner()
        self.formatter = DataFormatter()
        self.splitter = TrainValSplitter(train_ratio=train_ratio, val_ratio=val_ratio)

        self.samples: List[TrainingSample] = []

    def prepare(
        self,
        annotations: List[Dict[str, Any]],
        difficulty_assessor=None,
    ) -> Dict[str, Any]:
        """
        准备蒸馏数据

        Args:
            annotations: API标注数据
            difficulty_assessor: 难度评估器（可选）

        Returns:
            准备统计
        """

        logger.info("=" * 50)
        logger.info("开始数据蒸馏流程")
        logger.info("=" * 50)

        # Step 1: 数据清洗
        logger.info("[Step 1] 数据清洗...")
        cleaned = self.cleaner.clean(annotations)

        # Step 2: 转换为训练样本
        logger.info("[Step 2] 格式化为训练样本...")
        self.samples = []
        for ann in cleaned:
            sample = self._create_sample(ann, difficulty_assessor)
            if sample:
                self.samples.append(sample)

        logger.info("生成 %d 个训练样本", len(self.samples))

        # Step 3: 划分数据集
        logger.info("[Step 3] 划分训练/验证/测试集...")
        train_set, val_set, test_set = self.splitter.split(self.samples)

        # Step 4: 保存数据集
        logger.info("[Step 4] 保存数据集...")
        self._save_datasets(train_set, val_set, test_set)

        stats = {
            "total_samples": len(self.samples),
            "train_samples": len(train_set),
            "val_samples": len(val_set),
            "test_samples": len(test_set),
            "gold_samples": len([s for s in self.samples if s.source == "gold"]),
            "silver_samples": len([s for s in self.samples if s.source == "silver"]),
        }

        logger.info("=" * 50)
        logger.info("数据蒸馏完成: %s", stats)
        logger.info("=" * 50)

        return stats

    def _create_sample(
        self,
        annotation: Dict[str, Any],
        difficulty_assessor=None,
    ) -> Optional[TrainingSample]:
        """创建训练样本"""

        raw_text = annotation.get("raw_text", "")
        entities = annotation.get("entities", [])
        confidence = annotation.get("confidence", 0.9)

        if not raw_text or not entities:
            return None

        # 评估难度
        difficulty = self._assess_difficulty(entities, raw_text)

        # 判断来源
        source = "gold" if confidence >= 0.9 else "silver"

        # 构建输出JSON
        output_json = {
            "entities": entities,
        }

        return TrainingSample(
            id=self._generate_sample_id(raw_text),
            input_text=raw_text,
            output_json=output_json,
            entity_count=len(entities),
            source=source,
            difficulty=difficulty,
        )

    def _assess_difficulty(
        self,
        entities: List[Dict[str, Any]],
        raw_text: str,
    ) -> str:
        """评估样本难度"""

        entity_count = len(entities)
        text_length = len(raw_text)

        # 基于实体数量和文本长度评估
        if entity_count <= 3 and text_length < 100:
            return "easy"
        elif entity_count <= 8 and text_length < 300:
            return "medium"
        else:
            return "hard"

    def _generate_sample_id(self, text: str) -> str:
        """生成样本ID"""
        return hashlib.md5(text.encode()).hexdigest()[:12]

    def _save_datasets(
        self,
        train_set: List[TrainingSample],
        val_set: List[TrainingSample],
        test_set: List[TrainingSample],
    ):
        """保存数据集"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 保存训练集
        train_path = self.output_dir / f"train_{timestamp}.jsonl"
        self._save_samples_to_jsonl(train_set, train_path)

        # 保存验证集
        val_path = self.output_dir / f"val_{timestamp}.jsonl"
        self._save_samples_to_jsonl(val_set, val_path)

        # 保存测试集
        test_path = self.output_dir / f"test_{timestamp}.jsonl"
        self._save_samples_to_jsonl(test_set, test_path)

        # 保存最新的引用
        latest = {
            "train": str(train_path.name),
            "val": str(val_path.name),
            "test": str(test_path.name),
            "timestamp": timestamp,
        }

        latest_path = self.output_dir / "latest.json"
        with latest_path.open("w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=2)

        logger.info("数据集已保存:")
        logger.info("  训练集: %s (%d samples)", train_path, len(train_set))
        logger.info("  验证集: %s (%d samples)", val_path, len(val_set))
        logger.info("  测试集: %s (%d samples)", test_path, len(test_set))

    def _save_samples_to_jsonl(self, samples: List[TrainingSample], path: Path):
        """保存样本到JSONL文件"""

        with path.open("w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")

    def get_dataloader(
        self,
        split: str = "train",
        batch_size: int = 4,
        tokenizer=None,
        max_length: int = 512,
    ) -> Optional[DataLoader]:
        """
        获取DataLoader

        Args:
            split: train/val/test
            batch_size: 批次大小
            tokenizer: 分词器
            max_length: 最大长度

        Returns:
            DataLoader对象
        """

        if not tokenizer:
            logger.warning("没有提供tokenizer，无法创建DataLoader")
            return None

        # 加载最新数据集
        latest_path = self.output_dir / "latest.json"
        if not latest_path.exists():
            logger.warning("数据集不存在")
            return None

        with latest_path.open(encoding="utf-8") as f:
            latest = json.load(f)

        dataset_file = self.output_dir / latest.get(split, "")

        if not dataset_file.exists():
            logger.warning("数据集文件不存在: %s", dataset_file)
            return None

        # 加载样本
        samples = []
        with dataset_file.open(encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                sample = TrainingSample(**data)
                samples.append(sample)

        # 创建dataset和dataloader
        dataset = DistillationDataset(samples, tokenizer, max_length)
        dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=(split == "train"))

        return dataloader