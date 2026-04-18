"""
自主学习引擎：规则学习器模块
从API标注数据中学习Patterns并更新规则库

核心职责：
1. 模式挖掘（Pattern Mining）
2. 规则自动生成
3. 规则质量评估
4. 规则库自动更新
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class LearnedPattern:
    """学到的模式"""
    pattern_type: str  # skill / company / school / project / role
    pattern_text: str
    confidence: float
    frequency: int
    contexts: List[str] = field(default_factory=list)
    first_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "pattern_text": self.pattern_text,
            "confidence": self.confidence,
            "frequency": self.frequency,
            "contexts": self.contexts,
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
        }


class PatternMiner:
    """
    模式挖掘器
    从标注数据中挖掘实体模式
    """

    def __init__(self, min_frequency: int = 2, min_confidence: float = 0.7):
        self.min_frequency = min_frequency
        self.min_confidence = min_confidence
        self.entity_patterns: Dict[str, List[LearnedPattern]] = defaultdict(list)

    def mine_from_dataset(self, annotations: List[Dict[str, Any]]) -> Dict[str, List[LearnedPattern]]:
        """
        从标注数据集挖掘模式

        Args:
            annotations: API标注结果列表

        Returns:
            按类型组织的模式字典
        """

        logger.info("开始挖掘模式，共 %d 条标注数据", len(annotations))

        # 按实体类型分组
        entity_groups = defaultdict(list)
        for ann in annotations:
            for entity in ann.get("entities", []):
                entity_type = entity.get("type", "UNKNOWN")
                entity_groups[entity_type].append(entity)

        # 挖掘每种实体的模式
        for entity_type, entities in entity_groups.items():
            patterns = self._mine_entity_patterns(entity_type, entities, annotations)
            if patterns:
                self.entity_patterns[entity_type] = patterns
                logger.info("  %s: 发现 %d 个模式", entity_type, len(patterns))

        return dict(self.entity_patterns)

    def _mine_entity_patterns(
        self,
        entity_type: str,
        entities: List[Dict[str, Any]],
        annotations: List[Dict[str, Any]],
    ) -> List[LearnedPattern]:
        """挖掘特定类型实体的模式"""

        patterns = []

        # 统计词频
        span_counter = Counter()
        span_contexts = defaultdict(list)

        for entity in entities:
            span = entity.get("span", "").strip()
            if span and len(span) >= 2:  # 过滤太短的
                span_counter[span] += 1

                # 记录上下文
                for ann in annotations:
                    if entity in ann.get("entities", []):
                        raw_text = ann.get("raw_text", "")
                        # 提取span周围50个字符作为上下文
                        idx = raw_text.find(span)
                        if idx >= 0:
                            start = max(0, idx - 50)
                            end = min(len(raw_text), idx + len(span) + 50)
                            context = raw_text[start:end]
                            span_contexts[span].append(context)

        # 生成高频模式
        for span, freq in span_counter.items():
            if freq >= self.min_frequency:
                confidence = min(1.0, freq / 10)  # 频率越高置信度越高
                if confidence >= self.min_confidence:
                    pattern = LearnedPattern(
                        pattern_type=entity_type,
                        pattern_text=span,
                        confidence=confidence,
                        frequency=freq,
                        contexts=span_contexts.get(span, [])[:5],  # 最多5个上下文
                    )
                    patterns.append(pattern)

        return patterns

    def _extract_context_features(self, text: str, span: str) -> Dict[str, Any]:
        """提取上下文的语法特征"""

        features = {
            "prefix": "",
            "suffix": "",
            "has_parenthesis": False,
            "has_comma_before": False,
        }

        idx = text.find(span)
        if idx < 0:
            return features

        # 前缀（最多10个字符）
        prefix_start = max(0, idx - 10)
        features["prefix"] = text[prefix_start:idx].strip()

        # 后缀（最多10个字符）
        suffix_end = min(len(text), idx + len(span) + 10)
        features["suffix"] = text[idx + len(span):suffix_end].strip()

        # 括号检测
        features["has_parenthesis"] = "(" in text[max(0, idx-5):idx+len(span)+5]

        # 逗号检测
        features["has_comma_before"] = "," in text[max(0, idx-5):idx]

        return features


class RuleGenerator:
    """
    规则生成器
    将挖掘出的模式转换为可执行的正则规则
    """

    def __init__(self):
        self.generated_rules: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def generate_rules(
        self,
        patterns: Dict[str, List[LearnedPattern]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        将模式生成为可执行的规则

        Returns:
            规则字典 {entity_type: [rule1, rule2, ...]}
        """

        logger.info("开始生成规则...")

        for entity_type, type_patterns in patterns.items():
            rules = []
            for pattern in type_patterns:
                rule = self._pattern_to_rule(pattern)
                if rule:
                    rules.append(rule)

            if rules:
                self.generated_rules[entity_type] = rules
                logger.info("  %s: 生成 %d 条规则", entity_type, len(rules))

        return dict(self.generated_rules)

    def _pattern_to_rule(self, pattern: LearnedPattern) -> Optional[Dict[str, Any]]:
        """将单个模式转换为规则"""

        # 技能规则
        if pattern.pattern_type == "SKILL":
            return {
                "type": "skill",
                "pattern": re.escape(pattern.pattern_text),
                "confidence": pattern.confidence,
                "frequency": pattern.frequency,
                "rule_type": "exact",
            }

        # 公司规则（可能包含特定后缀）
        elif pattern.pattern_type == "COMPANY":
            company_suffixes = ["有限公司", "股份有限公司", "集团", "科技", "网络", "有限公司"]
            for suffix in company_suffixes:
                if suffix in pattern.pattern_text:
                    return {
                        "type": "company",
                        "pattern": re.escape(pattern.pattern_text),
                        "confidence": pattern.confidence,
                        "frequency": pattern.frequency,
                        "rule_type": "suffix",
                    }

        # 学校规则
        elif pattern.pattern_type == "SCHOOL":
            school_suffixes = ["大学", "学院", "学校", "Institute", "University"]
            for suffix in school_suffixes:
                if suffix in pattern.pattern_text:
                    return {
                        "type": "school",
                        "pattern": re.escape(pattern.pattern_text),
                        "confidence": pattern.confidence,
                        "frequency": pattern.frequency,
                        "rule_type": "suffix",
                    }

        # 项目规则
        elif pattern.pattern_type == "PROJECT_NAME":
            return {
                "type": "project",
                "pattern": re.escape(pattern.pattern_text),
                "confidence": pattern.confidence,
                "frequency": pattern.frequency,
                "rule_type": "context",
                "contexts": pattern.contexts[:3],  # 保存上下文作为匹配参考
            }

        # 角色规则
        elif pattern.pattern_type == "PROJECT_ROLE" or pattern.pattern_type == "POSITION":
            return {
                "type": "role",
                "pattern": re.escape(pattern.pattern_text),
                "confidence": pattern.confidence,
                "frequency": pattern.frequency,
                "rule_type": "role",
            }

        return None

    def export_to_json(self, path: Path):
        """导出规则到JSON文件"""

        with path.open("w", encoding="utf-8") as f:
            json.dump(dict(self.generated_rules), f, ensure_ascii=False, indent=2)

        logger.info("规则已导出到: %s", path)


class RuleEvaluator:
    """
    规则评估器
    评估新生成的规则质量，决定是否采纳
    """

    def __init__(self, precision_threshold: float = 0.8, recall_threshold: float = 0.7):
        self.precision_threshold = precision_threshold
        self.recall_threshold = recall_threshold

    def evaluate_rule(
        self,
        rule: Dict[str, Any],
        test_annotations: List[Dict[str, Any]],
    ) -> Tuple[bool, Dict[str, float]]:
        """
        评估单条规则

        Returns:
            (是否采纳, 评估指标)
        """

        entity_type = rule.get("type", "")
        pattern = rule.get("pattern", "")

        if not pattern:
            return False, {"precision": 0, "recall": 0}

        # 统计命中情况
        true_positives = 0
        false_positives = 0
        false_negatives = 0

        for ann in test_annotations:
            text = ann.get("raw_text", "")
            entities = ann.get("entities", [])

            # 检查是否匹配
            matches = list(re.finditer(pattern, text))

            # 标记为该类型的实体
            type_entities = [e for e in entities if e.get("type", "").upper() == entity_type.upper()]

            # 计算TP/FP/FN
            for match in matches:
                matched_text = match.group()
                # 检查是否匹配到真实实体
                if any(e.get("span") == matched_text for e in type_entities):
                    true_positives += 1
                else:
                    false_positives += 1

            # 统计FN（漏掉的真实实体）
            matched_spans = {m.group() for m in matches}
            for entity in type_entities:
                if entity.get("span") not in matched_spans:
                    false_negatives += 1

        # 计算精确率和召回率
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0

        metrics = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0,
            "support": true_positives + false_negatives,
        }

        # 决定是否采纳
        adopt = precision >= self.precision_threshold and recall >= self.recall_threshold

        return adopt, metrics


class RuleLearner:
    """
    规则学习器 - 主控制器
    协调模式挖掘、规则生成和规则评估
    """

    def __init__(
        self,
        rules_dir: Optional[Path] = None,
        min_pattern_freq: int = 2,
        min_confidence: float = 0.7,
    ):
        self.rules_dir = rules_dir or Path("./learned_rules")
        self.rules_dir.mkdir(parents=True, exist_ok=True)

        self.min_pattern_freq = min_pattern_freq
        self.min_confidence = min_confidence

        self.miner = PatternMiner(min_frequency=min_pattern_freq, min_confidence=min_confidence)
        self.generator = RuleGenerator()
        self.evaluator = RuleEvaluator()

        self.all_patterns: Dict[str, List[LearnedPattern]] = {}
        self.all_rules: Dict[str, List[Dict[str, Any]]] = {}
        self.adopted_rules: Dict[str, List[Dict[str, Any]]] = {}

    def learn(
        self,
        gold_annotations: List[Dict[str, Any]],
        test_annotations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        执行完整的学习流程

        Args:
            gold_annotations: 训练用标注数据
            test_annotations: 测试用标注数据（可选）

        Returns:
            学习结果统计
        """

        logger.info("=" * 50)
        logger.info("开始规则学习流程")
        logger.info("=" * 50)

        # Step 1: 模式挖掘
        logger.info("[Step 1] 模式挖掘...")
        patterns = self.miner.mine_from_dataset(gold_annotations)
        self.all_patterns = patterns

        # Step 2: 规则生成
        logger.info("[Step 2] 规则生成...")
        rules = self.generator.generate_rules(patterns)
        self.all_rules = rules

        # Step 3: 规则评估（如果有测试数据）
        adopted_rules = {}
        if test_annotations:
            logger.info("[Step 3] 规则评估...")
            for entity_type, type_rules in rules.items():
                adopted = []
                for rule in type_rules:
                    adopt, metrics = self.evaluator.evaluate_rule(rule, test_annotations)
                    if adopt:
                        rule["metrics"] = metrics
                        adopted.append(rule)
                adopted_rules[entity_type] = adopted
                logger.info("  %s: 采纳 %d/%d 条规则", entity_type, len(adopted), len(type_rules))
        else:
            # 没有测试数据，直接采纳所有规则
            adopted_rules = rules
            logger.info("[Step 3] 无测试数据，直接采纳所有规则")

        self.adopted_rules = adopted_rules

        # Step 4: 保存规则
        logger.info("[Step 4] 保存规则...")
        self._save_rules()

        # 统计
        stats = {
            "total_patterns": sum(len(p) for p in patterns.values()),
            "total_rules": sum(len(r) for r in rules.values()),
            "adopted_rules": sum(len(r) for r in adopted_rules.values()),
            "entity_types": list(patterns.keys()),
        }

        logger.info("=" * 50)
        logger.info("规则学习完成: %s", stats)
        logger.info("=" * 50)

        return stats

    def _save_rules(self):
        """保存学习到的规则"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 保存采纳的规则
        adopted_path = self.rules_dir / f"adopted_rules_{timestamp}.json"
        self.generator.export_to_json(adopted_path)

        # 保存所有规则
        all_path = self.rules_dir / f"all_rules_{timestamp}.json"
        with all_path.open("w", encoding="utf-8") as f:
            json.dump(self.all_rules, f, ensure_ascii=False, indent=2)

        # 更新主规则文件
        main_path = self.rules_dir / "rules.json"
        with main_path.open("w", encoding="utf-8") as f:
            json.dump(self.adopted_rules, f, ensure_ascii=False, indent=2)

        logger.info("规则已保存到: %s", self.rules_dir)

    def load_rules(self, path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
        """加载已有规则"""

        rule_path = path or self.rules_dir / "rules.json"

        if not rule_path.exists():
            logger.warning("规则文件不存在: %s", rule_path)
            return {}

        with rule_path.open(encoding="utf-8") as f:
            rules = json.load(f)

        logger.info("已加载 %d 类实体的规则", len(rules))

        return rules


def merge_with_existing_rules(
    new_rules: Dict[str, List[Dict[str, Any]]],
    existing_rules: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    合并新规则和已有规则

    策略：
    1. 新规则中frequency更高的覆盖低频率的已有规则
    2. 新规则中confidence更高的覆盖低置信度的已有规则
    """

    merged = {}

    all_types = set(new_rules.keys()) | set(existing_rules.keys())

    for entity_type in all_types:
        new_type_rules = {r["pattern"]: r for r in new_rules.get(entity_type, [])}
        existing_type_rules = {r["pattern"]: r for r in existing_rules.get(entity_type, [])}

        # 合并
        combined = {}
        combined.update(existing_type_rules)
        combined.update(new_type_rules)

        # 如果同一个pattern有新有旧，取置信度更高的
        for pattern in combined:
            new_rule = new_type_rules.get(pattern, {})
            existing_rule = existing_type_rules.get(pattern, {})

            if new_rule and existing_rule:
                if new_rule.get("confidence", 0) > existing_rule.get("confidence", 0):
                    combined[pattern] = new_rule

        merged[entity_type] = list(combined.values())

    return merged