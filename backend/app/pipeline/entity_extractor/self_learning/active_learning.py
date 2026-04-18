"""
自主学习引擎：主动学习反馈循环
整合所有模块，实现闭环的自主学习系统

核心职责：
1. 主动学习样本选择
2. 反馈信号收集
3. 增量学习调度
4. 质量监控与报警
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from enum import Enum

import torch

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """反馈类型"""
    CORRECT = "correct"           # 正确
    INCORRECT = "incorrect"       # 错误
    MISSING = "missing"            # 漏提取
    FALSE_POSITIVE = "false_positive"  # 误提取
    LOW_CONFIDENCE = "low_confidence"  # 低置信度


@dataclass
class Feedback:
    """反馈数据"""
    feedback_id: str
    sample_id: str
    feedback_type: FeedbackType
    original_entities: List[Dict[str, Any]]
    suggested_entities: List[Dict[str, Any]]
    severity: float = 1.0  # 严重程度 0-1
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    annotator: str = "human"


@dataclass
class ActiveLearningStats:
    """主动学习统计"""
    total_iterations: int = 0
    total_samples_annotated: int = 0
    total_samples_learned: int = 0
    total_model_updates: int = 0
    current_coverage: float = 0.0
    current_precision: float = 0.0
    current_recall: float = 0.0


class ConfidenceAnalyzer:
    """
    置信度分析器
    分析模型预测的置信度分布，识别低置信样本
    """

    def __init__(
        self,
        low_confidence_threshold: float = 0.7,
        uncertainty_threshold: float = 0.3,
    ):
        self.low_confidence_threshold = low_confidence_threshold
        self.uncertainty_threshold = uncertainty_threshold

        self.confidence_history = []
        self.entity_type_confidence = defaultdict(list)

    def analyze_prediction(
        self,
        prediction: Dict[str, Any],
        ground_truth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        分析预测结果

        Returns:
            分析报告
        """

        predicted_entities = prediction.get("entities", [])
        confidence = prediction.get("confidence", 0.0)

        # 记录置信度
        self.confidence_history.append(confidence)

        # 分析每个实体类型的置信度
        for entity in predicted_entities:
            entity_type = entity.get("type", "UNKNOWN")
            entity_confidence = entity.get("confidence", confidence)
            self.entity_type_confidence[entity_type].append(entity_confidence)

        # 计算不确定性指标
        uncertainty = 1.0 - confidence

        # 如果有ground truth，计算精确率和召回率
        precision = 0.0
        recall = 0.0
        if ground_truth:
            gt_entities = ground_truth.get("entities", [])
            precision, recall = self._calculate_pr( predicted_entities, gt_entities)

        return {
            "confidence": confidence,
            "uncertainty": uncertainty,
            "predicted_count": len(predicted_entities),
            "precision": precision,
            "recall": recall,
            "is_low_confidence": confidence < self.low_confidence_threshold,
            "needs_review": uncertainty > self.uncertainty_threshold or precision < 0.8,
        }

    def _calculate_pr(
        self,
        predicted: List[Dict[str, Any]],
        ground_truth: List[Dict[str, Any]],
    ) -> Tuple[float, float]:
        """计算精确率和召回率"""

        if not predicted:
            precision = 0.0
        else:
            # 按span匹配
            pred_spans = {e.get("span", "").strip() for e in predicted}
            gt_spans = {e.get("span", "").strip() for e in ground_truth}

            true_positives = len(pred_spans & gt_spans)
            precision = true_positives / len(pred_spans) if pred_spans else 0.0

        if not ground_truth:
            recall = 0.0
        else:
            gt_spans = {e.get("span", "").strip() for e in ground_truth}
            pred_spans = {e.get("span", "").strip() for e in predicted}
            true_positives = len(pred_spans & gt_spans)
            recall = true_positives / len(gt_spans) if gt_spans else 0.0

        return precision, recall

    def get_low_confidence_samples(
        self,
        threshold: Optional[float] = None,
    ) -> List[str]:
        """获取低置信度样本ID"""

        threshold = threshold or self.low_confidence_threshold

        low_conf_ids = []
        for i, conf in enumerate(self.confidence_history):
            if conf < threshold:
                low_conf_ids.append(f"sample_{i}")

        return low_conf_ids

    def get_entity_type_stats(self) -> Dict[str, Dict[str, float]]:
        """获取各实体类型的置信度统计"""

        stats = {}

        for entity_type, confidences in self.entity_type_confidence.items():
            if confidences:
                stats[entity_type] = {
                    "mean": sum(confidences) / len(confidences),
                    "min": min(confidences),
                    "max": max(confidences),
                    "count": len(confidences),
                }

        return stats


class FeedbackCollector:
    """
    反馈收集器
    从多个来源收集反馈信号
    """

    def __init__(self):
        self.feedbacks: List[Feedback] = []
        self.feedback_by_type = defaultdict(list)
        self.feedback_by_sample = defaultdict(list)

    def add_feedback(
        self,
        feedback: Feedback,
    ):
        """添加反馈"""

        self.feedbacks.append(feedback)
        self.feedback_by_type[feedback.feedback_type].append(feedback)
        self.feedback_by_sample[feedback.sample_id].append(feedback)

        logger.debug("添加反馈: %s - %s", feedback.sample_id, feedback.feedback_type.value)

    def add_manual_feedback(
        self,
        sample_id: str,
        original_entities: List[Dict[str, Any]],
        suggested_entities: List[Dict[str, Any]],
        annotator: str = "human",
    ) -> Feedback:
        """添加人工反馈"""

        # 判断反馈类型
        if not original_entities and suggested_entities:
            feedback_type = FeedbackType.MISSING
        elif original_entities and not suggested_entities:
            feedback_type = FeedbackType.FALSE_POSITIVE
        elif self._is_incorrect(original_entities, suggested_entities):
            feedback_type = FeedbackType.INCORRECT
        else:
            feedback_type = FeedbackType.CORRECT

        feedback = Feedback(
            feedback_id=f"fb_{len(self.feedbacks)}_{int(time.time())}",
            sample_id=sample_id,
            feedback_type=feedback_type,
            original_entities=original_entities,
            suggested_entities=suggested_entities,
            annotator=annotator,
        )

        self.add_feedback(feedback)

        return feedback

    def _is_incorrect(
        self,
        original: List[Dict[str, Any]],
        suggested: List[Dict[str, Any]],
    ) -> bool:
        """判断是否错误"""

        if len(original) != len(suggested):
            return True

        original_spans = {e.get("span", "").strip() for e in original}
        suggested_spans = {e.get("span", "").strip() for e in suggested}

        return original_spans != suggested_spans

    def get_critical_samples(self) -> List[str]:
        """获取需要优先处理的样本ID"""

        critical = set()

        # 低置信度反馈
        for fb in self.feedback_by_type[FeedbackType.LOW_CONFIDENCE]:
            critical.add(fb.sample_id)

        # 漏提取
        for fb in self.feedback_by_type[FeedbackType.MISSING]:
            critical.add(fb.sample_id)

        # 高严重程度的错误
        for fb in self.feedback_by_type[FeedbackType.INCORRECT]:
            if fb.severity > 0.7:
                critical.add(fb.sample_id)

        return list(critical)

    def get_statistics(self) -> Dict[str, Any]:
        """获取反馈统计"""

        total = len(self.feedbacks)

        if total == 0:
            return {
                "total": 0,
                "by_type": {},
            }

        by_type = {
            ft.value: len(fbs) for ft, fbs in self.feedback_by_type.items()
        }

        return {
            "total": total,
            "by_type": by_type,
            "unique_samples": len(set(fb.sample_id for fb in self.feedbacks)),
            "human_annotated": len([fb for fb in self.feedbacks if fb.annotator == "human"]),
            "auto_annotated": len([fb for fb in self.feedbacks if fb.annotator == "auto"]),
        }


class SampleSelector:
    """
    主动学习样本选择器
    选择最值得标注的样本
    """

    def __init__(
        self,
        strategy: str = "uncertainty",  # uncertainty, diversity, random
    ):
        self.strategy = strategy

    def select(
        self,
        unlabeled_samples: List[Dict[str, Any]],
        model_predictions: List[Dict[str, Any]],
        feedback_collector: FeedbackCollector,
        batch_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        选择最值得标注的样本

        Args:
            unlabeled_samples: 未标注样本
            model_predictions: 模型预测结果
            feedback_collector: 反馈收集器

        Returns:
            选中的样本
        """

        if not unlabeled_samples:
            return []

        # 已标注的样本
        annotated_ids = {fb.sample_id for fb in feedback_collector.feedbacks}

        # 过滤已标注
        candidates = [
            (i, s) for i, s in enumerate(unlabeled_samples)
            if s.get("id", f"sample_{i}") not in annotated_ids
        ]

        if not candidates:
            return []

        if self.strategy == "random":
            return self._select_random(candidates, batch_size)

        elif self.strategy == "uncertainty":
            return self._select_by_uncertainty(candidates, model_predictions, batch_size)

        elif self.strategy == "diversity":
            return self._select_by_diversity(candidates, model_predictions, batch_size)

        elif self.strategy == "combined":
            return self._select_by_combined(candidates, model_predictions, feedback_collector, batch_size)

        else:
            return self._select_random(candidates, batch_size)

    def _select_random(
        self,
        candidates: List[Tuple[int, Dict[str, Any]]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        """随机选择"""

        import random
        selected = random.sample(candidates, min(batch_size, len(candidates)))
        return [s for _, s in selected]

    def _select_by_uncertainty(
        self,
        candidates: List[Tuple[int, Dict[str, Any]]],
        predictions: List[Dict[str, Any]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        """基于不确定性选择"""

        # 计算每个候选的不确定性
        uncertainty_scores = []

        for i, sample in candidates:
            # 找到对应的预测
            pred = None
            for p in predictions:
                if p.get("sample_id") == sample.get("id"):
                    pred = p
                    break

            if pred:
                uncertainty = 1.0 - pred.get("confidence", 0.5)
            else:
                uncertainty = 0.5  # 默认

            uncertainty_scores.append((uncertainty, sample))

        # 选择最不确定的
        uncertainty_scores.sort(reverse=True)

        return [s for _, s in uncertainty_scores[:batch_size]]

    def _select_by_diversity(
        self,
        candidates: List[Tuple[int, Dict[str, Any]]],
        predictions: List[Dict[str, Any]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        """基于多样性选择"""

        # 按实体类型多样性选择
        entity_type_diversity = []

        for i, sample in candidates:
            # 统计实体类型多样性
            entity_types = set()
            for entity in sample.get("entities", []):
                entity_types.add(entity.get("type", "UNKNOWN"))

            diversity = len(entity_types)
            entity_type_diversity.append((diversity, sample))

        # 选择多样性最高的
        entity_type_diversity.sort(key=lambda x: x[0], reverse=True)

        return [s for _, s in entity_type_diversity[:batch_size]]

    def _select_by_combined(
        self,
        candidates: List[Tuple[int, Dict[str, Any]]],
        predictions: List[Dict[str, Any]],
        feedback_collector: FeedbackCollector,
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        """综合策略：不确定性 + 多样性 + 反馈优先级"""

        # 获取关键样本
        critical_samples = set(feedback_collector.get_critical_samples())

        combined_scores = []

        for i, sample in candidates:
            sample_id = sample.get("id", f"sample_{i}")

            # 不确定性分数
            pred = None
            for p in predictions:
                if p.get("sample_id") == sample_id:
                    pred = p
                    break

            uncertainty = 1.0 - pred.get("confidence", 0.5) if pred else 0.5

            # 多样性分数
            entity_types = set()
            for entity in sample.get("entities", []):
                entity_types.add(entity.get("type", "UNKNOWN"))
            diversity = len(entity_types) / 10.0  # 归一化

            # 关键样本加分
            is_critical = 1.0 if sample_id in critical_samples else 0.0

            # 综合分数
            combined_score = uncertainty * 0.5 + diversity * 0.3 + is_critical * 0.2

            combined_scores.append((combined_score, sample))

        combined_scores.sort(key=lambda x: x[0], reverse=True)

        return [s for _, s in combined_scores[:batch_size]]


class IncrementalLearningScheduler:
    """
    增量学习调度器
    管理何时触发模型更新
    """

    def __init__(
        self,
        min_feedbacks_to_update: int = 50,
        update_interval_hours: int = 24,
        min_improvement_threshold: float = 0.05,
    ):
        self.min_feedbacks_to_update = min_feedbacks_to_update
        self.update_interval_hours = update_interval_hours
        self.min_improvement_threshold = min_improvement_threshold

        self.last_update_time = None
        self.pending_updates = 0

    def should_update(
        self,
        feedback_collector: FeedbackCollector,
        current_metrics: Optional[Dict[str, float]] = None,
        previous_metrics: Optional[Dict[str, float]] = None,
    ) -> Tuple[bool, str]:
        """
        判断是否应该触发模型更新

        Returns:
            (是否更新, 原因)
        """

        total_feedbacks = len(feedback_collector.feedbacks)

        # 检查反馈数量
        if total_feedbacks - self.pending_updates < self.min_feedbacks_to_update:
            return False, f"反馈不足 ({total_feedbacks}/{self.min_feedbacks_to_update})"

        # 检查时间间隔
        if self.last_update_time:
            hours_since_update = (
                datetime.now() - self.last_update_time
            ).total_seconds() / 3600

            if hours_since_update < self.update_interval_hours:
                return False, f"时间间隔不足 ({hours_since_update:.1f}h/{self.update_interval_hours}h)"

        # 检查是否有足够改进
        if current_metrics and previous_metrics:
            improvement = current_metrics.get("recall", 0) - previous_metrics.get("recall", 0)

            if improvement < self.min_improvement_threshold:
                return False, f"改进不足 ({improvement:.3f}/{self.min_improvement_threshold})"

        return True, "满足更新条件"

    def record_update(self):
        """记录更新事件"""

        self.last_update_time = datetime.now()
        self.pending_updates = len(
            [fb for fb in [] if fb.annotator == "human"]  # 这里需要传入feedback_collector
        )


class SelfLearningLoop:
    """
    自主学习循环 - 主控制器
    整合所有模块，实现闭环自主学习
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        api_keys: Optional[Dict[str, str]] = None,
    ):
        self.output_dir = output_dir or Path("./self_learning_data")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 初始化各组件
        self.labeling_engine = None  # 需要外部初始化
        self.rule_learner = None
        self.distillation_pipeline = None
        self.sft_trainer = None

        # 初始化分析器
        self.confidence_analyzer = ConfidenceAnalyzer()
        self.feedback_collector = FeedbackCollector()
        self.sample_selector = SampleSelector(strategy="combined")
        self.update_scheduler = IncrementalLearningScheduler()

        # 统计
        self.stats = ActiveLearningStats()

        # 状态
        self.current_iteration = 0
        self.is_running = False

    def set_components(
        self,
        labeling_engine,
        rule_learner,
        distillation_pipeline,
        sft_trainer,
    ):
        """设置各组件"""

        self.labeling_engine = labeling_engine
        self.rule_learner = rule_learner
        self.distillation_pipeline = distillation_pipeline
        self.sft_trainer = sft_trainer

    def run_iteration(
        self,
        unlabeled_samples: List[Dict[str, Any]],
        use_api_annotation: bool = True,
        batch_size: int = 10,
    ) -> Dict[str, Any]:
        """
        执行一次自主学习迭代

        Args:
            unlabeled_samples: 未标注样本
            use_api_annotation: 是否使用API标注
            batch_size: 每次标注的样本数

        Returns:
            迭代结果
        """

        self.current_iteration += 1
        self.is_running = True

        logger.info("=" * 50)
        logger.info("开始自主学习迭代 #%d", self.current_iteration)
        logger.info("=" * 50)

        iteration_result = {
            "iteration": self.current_iteration,
            "samples_selected": 0,
            "samples_annotated": 0,
            "patterns_learned": 0,
            "model_updated": False,
        }

        # Step 1: 样本选择
        logger.info("[Step 1] 主动学习样本选择...")
        selected_samples = self.sample_selector.select(
            unlabeled_samples,
            [],  # model_predictions
            self.feedback_collector,
            batch_size=batch_size,
        )

        iteration_result["samples_selected"] = len(selected_samples)

        if not selected_samples:
            logger.info("没有需要标注的样本")
            self.is_running = False
            return iteration_result

        # Step 2: API标注（如果启用）
        if use_api_annotation and self.labeling_engine:
            logger.info("[Step 2] API标注...")
            annotations = []
            for sample in selected_samples:
                result = self.labeling_engine.annotate(
                    sample.get("text", ""),
                    sample.get("context"),
                )
                annotations.append(result)

            iteration_result["samples_annotated"] = len(annotations)

            # 合并到反馈收集器
            for ann in annotations:
                self.feedback_collector.add_manual_feedback(
                    sample_id=ann.raw_text[:50],
                    original_entities=ann.entities,
                    suggested_entities=[],
                    annotator="api",
                )

        # Step 3: 分析预测（如果有模型）
        if self.sft_trainer and self.sft_trainer.model:
            logger.info("[Step 3] 分析模型预测...")

            for sample in unlabeled_samples[:10]:  # 抽样分析
                prediction = self.sft_trainer.predict(sample.get("text", ""))
                analysis = self.confidence_analyzer.analyze_prediction(prediction)

                if analysis.get("needs_review"):
                    self.feedback_collector.add_manual_feedback(
                        sample_id=sample.get("id", ""),
                        original_entities=prediction.get("entities", []),
                        suggested_entities=[],
                        annotator="auto",
                    )

        # Step 4: 检查是否需要模型更新
        logger.info("[Step 4] 检查模型更新条件...")

        previous_metrics = {
            "recall": self.stats.current_recall,
            "precision": self.stats.current_precision,
        }

        current_metrics = {
            "recall": self.stats.current_recall,
            "precision": self.stats.current_precision,
        }

        should_update, reason = self.update_scheduler.should_update(
            self.feedback_collector,
            current_metrics,
            previous_metrics,
        )

        logger.info("更新检查: %s", reason)

        if should_update:
            logger.info("[Step 5] 触发模型更新...")
            self._trigger_model_update()
            iteration_result["model_updated"] = True

        # Step 5: 更新统计
        self._update_stats()

        self.is_running = False

        logger.info("=" * 50)
        logger.info("迭代 #%d 完成", self.current_iteration)
        logger.info("  选择样本: %d", iteration_result["samples_selected"])
        logger.info("  标注样本: %d", iteration_result["samples_annotated"])
        logger.info("  模型更新: %s", iteration_result["model_updated"])
        logger.info("=" * 50)

        return iteration_result

    def _trigger_model_update(self):
        """触发模型更新"""

        if not self.feedback_collector.feedbacks:
            return

        # 准备训练数据
        training_data = []

        for fb in self.feedback_collector.feedbacks:
            if fb.feedback_type in [FeedbackType.INCORRECT, FeedbackType.MISSING]:
                training_data.append({
                    "input": fb.original_entities[0].get("context", "") if fb.original_entities else "",
                    "target": json.dumps({"entities": fb.suggested_entities}, ensure_ascii=False),
                })

        if len(training_data) < 10:
            logger.info("训练数据不足，跳过模型更新")
            return

        # 执行微调
        if self.sft_trainer and self.sft_trainer.setup_model():
            self.sft_trainer.prepare_dataset(training_data)
            self.sft_trainer.train()

            self.stats.total_model_updates += 1

        # 更新规则
        if self.rule_learner:
            annotations = [
                {
                    "raw_text": fb.original_entities[0].get("context", "") if fb.original_entities else "",
                    "entities": fb.suggested_entities,
                }
                for fb in self.feedback_collector.feedbacks
            ]

            self.rule_learner.learn(annotations)
            self.stats.total_model_updates += 1

    def _update_stats(self):
        """更新统计"""

        self.stats.total_iterations = self.current_iteration
        self.stats.total_samples_annotated += len(self.feedback_collector.feedbacks)

        # 计算当前覆盖率
        self.stats.current_coverage = min(1.0, len(self.feedback_collector.feedbacks) / 1000)

        # 从置信度分析器获取精确率和召回率
        entity_stats = self.confidence_analyzer.get_entity_type_stats()

        if entity_stats:
            avg_precision = sum(s.get("mean", 0) for s in entity_stats.values()) / len(entity_stats)
            self.stats.current_precision = avg_precision

        logger.info("统计已更新: %s", self.stats)

    def get_learning_report(self) -> Dict[str, Any]:
        """获取学习报告"""

        return {
            "iteration": self.current_iteration,
            "stats": {
                "total_iterations": self.stats.total_iterations,
                "total_samples_annotated": self.stats.total_samples_annotated,
                "total_model_updates": self.stats.total_model_updates,
                "current_coverage": self.stats.current_coverage,
                "current_precision": self.stats.current_precision,
                "current_recall": self.stats.current_recall,
            },
            "feedback": self.feedback_collector.get_statistics(),
            "confidence": self.confidence_analyzer.get_entity_type_stats(),
        }

    def save_state(self, path: Optional[Path] = None):
        """保存状态"""

        state_path = path or (self.output_dir / "learning_state.json")

        state = {
            "current_iteration": self.current_iteration,
            "stats": {
                "total_iterations": self.stats.total_iterations,
                "total_samples_annotated": self.stats.total_samples_annotated,
                "total_model_updates": self.stats.total_model_updates,
                "current_coverage": self.stats.current_coverage,
                "current_precision": self.stats.current_precision,
                "current_recall": self.stats.current_recall,
            },
            "timestamp": datetime.now().isoformat(),
        }

        with state_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        logger.info("状态已保存: %s", state_path)

    def load_state(self, path: Optional[Path] = None):
        """加载状态"""

        state_path = path or (self.output_dir / "learning_state.json")

        if not state_path.exists():
            logger.warning("状态文件不存在: %s", state_path)
            return

        with state_path.open(encoding="utf-8") as f:
            state = json.load(f)

        self.current_iteration = state.get("current_iteration", 0)

        stats = state.get("stats", {})
        self.stats.total_iterations = stats.get("total_iterations", 0)
        self.stats.total_samples_annotated = stats.get("total_samples_annotated", 0)
        self.stats.total_model_updates = stats.get("total_model_updates", 0)
        self.stats.current_coverage = stats.get("current_coverage", 0.0)
        self.stats.current_precision = stats.get("current_precision", 0.0)
        self.stats.current_recall = stats.get("current_recall", 0.0)

        logger.info("状态已加载: iteration=%d", self.current_iteration)