"""
SFT训练数据集验证与测试工具
==========================

用途：
    1. 验证训练数据格式是否符合要求
    2. 划分训练集/验证集/测试集
    3. 评估数据质量
"""

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class DataSample:
    messages: List[Dict[str, str]]
    metadata: Optional[Dict[str, Any]] = None


class TrainingDataValidator:
    """验证训练数据格式"""

    REQUIRED_ROLES = {"system", "user", "assistant"}

    def __init__(self):
        self.errors: List[str] = []

    def validate(self, sample: Dict) -> bool:
        """验证单条数据"""
        self.errors = []

        # 检查messages字段
        if "messages" not in sample:
            self.errors.append("缺少 'messages' 字段")
            return False

        messages = sample["messages"]
        if not isinstance(messages, list):
            self.errors.append("'messages' 必须是列表")
            return False

        if len(messages) < 2:
            self.errors.append("messages至少需要2条（user + assistant）")
            return False

        # 检查角色
        roles = set()
        for i, msg in enumerate(messages):
            if "role" not in msg:
                self.errors.append(f"第{i}条消息缺少 'role' 字段")
                return False
            if "content" not in msg:
                self.errors.append(f"第{i}条消息缺少 'content' 字段")
                return False
            roles.add(msg["role"])

        # 验证角色顺序（至少要有user和assistant）
        if not {"user", "assistant"}.issubset(roles):
            self.errors.append("messages必须包含 'user' 和 'assistant' 角色")
            return False

        # 检查assistant的content是否为有效JSON
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        for i, msg in enumerate(assistant_msgs):
            try:
                content = msg["content"]
                if content.strip().startswith("{"):
                    json.loads(content)
            except json.JSONDecodeError as e:
                self.errors.append(f"assistant响应不是有效JSON: {e}")
                return False

        return True

    def validate_dataset(self, dataset: List[Dict]) -> Dict[str, Any]:
        """验证整个数据集"""
        total = len(dataset)
        valid_count = 0
        error_summary = {}

        for i, sample in enumerate(dataset):
            if self.validate(sample):
                valid_count += 1
            else:
                error_type = self.errors[0] if self.errors else "未知错误"
                error_summary[f"sample_{i}"] = error_type

        return {
            "total": total,
            "valid": valid_count,
            "invalid": total - valid_count,
            "valid_rate": valid_count / total if total > 0 else 0,
            "errors": error_summary
        }


class DatasetSplitter:
    """划分训练集/验证集/测试集"""

    @staticmethod
    def split(
        dataset: List[Dict],
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        seed: int = 42
    ) -> Dict[str, List[Dict]]:
        """
        划分数据集

        Args:
            dataset: 完整数据集
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例
            seed: 随机种子

        Returns:
            {"train": [...], "val": [...], "test": [...]}
        """
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "比例之和必须为1"

        random.seed(seed)
        data = dataset.copy()
        random.shuffle(data)

        n = len(data)
        train_end = int(n * train_ratio)
        val_end = train_end + int(n * val_ratio)

        return {
            "train": data[:train_end],
            "val": data[train_end:val_end],
            "test": data[val_end:]
        }

    @staticmethod
    def stratified_split_by_type(
        dataset: List[Dict],
        type_field: str = "task_type",
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        seed: int = 42
    ) -> Dict[str, List[Dict]]:
        """
        按类型分层抽样划分

        确保每种任务类型在训练/验证/测试集中都有相同比例
        """
        from collections import defaultdict

        random.seed(seed)

        # 按类型分组
        type_groups = defaultdict(list)
        for sample in dataset:
            task_type = sample.get("metadata", {}).get(type_field, "unknown")
            type_groups[task_type].append(sample)

        train, val, test = [], [], []

        for task_type, samples in type_groups.items():
            random.shuffle(samples)
            n = len(samples)
            train_end = int(n * train_ratio)
            val_end = train_end + int(n * val_ratio)

            train.extend(samples[:train_end])
            val.extend(samples[train_end:val_end])
            test.extend(samples[val_end:])

        # 打乱各集合内部顺序
        random.shuffle(train)
        random.shuffle(val)
        random.shuffle(test)

        return {"train": train, "val": val, "test": test}


class DataQualityChecker:
    """检查数据质量"""

    def __init__(self, min_response_length: int = 50):
        self.min_response_length = min_response_length
        self.warnings: List[str] = []

    def check(self, sample: Dict) -> List[str]:
        """检查单条数据质量"""
        warnings = []

        messages = sample.get("messages", [])

        # 检查assistant响应长度
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if len(content) < self.min_response_length:
                    warnings.append(f"assistant响应过短: {len(content)}字符")

        # 检查是否有重复内容
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if content.count("{") > 10:  # JSON可能过长
                warnings.append(f"第{i}条消息可能包含过长的JSON")

        return warnings

    def check_dataset(self, dataset: List[Dict]) -> Dict[str, Any]:
        """检查整个数据集质量"""
        total = len(dataset)
        samples_with_warnings = 0
        warning_counts = {}

        for sample in dataset:
            warnings = self.check(sample)
            if warnings:
                samples_with_warnings += 1
                for w in warnings:
                    warning_counts[w] = warning_counts.get(w, 0) + 1

        return {
            "total": total,
            "samples_with_warnings": samples_with_warnings,
            "warning_rate": samples_with_warnings / total if total > 0 else 0,
            "warning_summary": warning_counts
        }


def load_jsonl(path: Path) -> List[Dict]:
    """加载JSONL文件"""
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def save_jsonl(data: List[Dict], path: Path):
    """保存为JSONL文件"""
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    print("=== 训练数据验证工具 ===")
    print("\n使用示例:")
    print("""
    from training_data_validator import TrainingDataValidator, DatasetSplitter

    # 1. 验证数据格式
    validator = TrainingDataValidator()
    result = validator.validate_dataset(your_dataset)
    print(f"验证结果: {result}")

    # 2. 划分数据集
    splitter = DatasetSplitter()
    splits = splitter.split(dataset, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1)

    # 3. 检查数据质量
    checker = DataQualityChecker(min_response_length=50)
    quality_report = checker.check_dataset(dataset)
    print(f"质量报告: {quality_report}")
    """)
