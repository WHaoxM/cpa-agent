"""
单样本评估框架 — 用于验证 NS-LE 架构和模型选择

评估维度：
1. 块分类准确率 & 覆盖率
2. 实体 F1（按类型分别计算）
3. 关系准确率
4. JSON 解析成功率 & Schema 合规率
5. 效率指标（延迟、fallback 次数）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class GroundTruthEntity:
    """标注的实体"""
    entity_type: str
    span: str
    block_id: str = ""


@dataclass
class GroundTruthRelation:
    """标注的关系"""
    head_type: str
    head_span: str
    tail_type: str
    tail_span: str
    relation_type: str


@dataclass
class GroundTruth:
    """单样本 ground truth"""
    block_classifications: Dict[str, str] = field(default_factory=dict)
    entities: List[GroundTruthEntity] = field(default_factory=list)
    relations: List[GroundTruthRelation] = field(default_factory=list)


@dataclass
class EvaluationMetrics:
    """评估指标"""
    block_classification_accuracy: float = 0.0
    block_classification_coverage: float = 0.0
    entity_precision: float = 0.0
    entity_recall: float = 0.0
    entity_f1: float = 0.0
    entity_f1_by_type: Dict[str, float] = field(default_factory=dict)
    relation_accuracy: float = 0.0
    json_parse_rate: float = 0.0
    schema_compliance_rate: float = 0.0
    total_latency_ms: float = 0.0
    fallback_count: int = 0
    retry_count: int = 0
    avg_confidence: float = 0.0
    high_uncertainty_ratio: float = 0.0


# ---------------------------------------------------------------------------
# 评估函数
# ---------------------------------------------------------------------------

def evaluate_block_classification(
    predicted: Dict[str, str],
    ground_truth: Dict[str, str],
) -> Tuple[float, float]:
    """
    评估块分类准确率。

    Returns:
        (accuracy, coverage)
    """
    if not ground_truth:
        return 0.0, 0.0

    correct = sum(
        1 for block_id, gt_type in ground_truth.items()
        if predicted.get(block_id) == gt_type
    )
    accuracy = correct / len(ground_truth)

    covered = sum(1 for block_id in ground_truth if block_id in predicted)
    coverage = covered / len(ground_truth)

    return round(accuracy, 4), round(coverage, 4)


def _normalize_span(span: str) -> str:
    """标准化 span：去除首尾空白、转小写、去除标点"""
    span = span.strip().lower()
    span = re.sub(r'[，,。；;、\s]+', '', span)
    return span


def _span_overlap(pred_span: str, gt_span: str) -> float:
    """
    计算两个 span 的 token 级别 overlap。
    返回 0.0-1.0 的相似度分数。
    """
    pred_tokens = set(_normalize_span(pred_span))
    gt_tokens = set(_normalize_span(gt_span))
    
    if not pred_tokens or not gt_tokens:
        return 0.0
    
    intersection = pred_tokens & gt_tokens
    union = pred_tokens | gt_tokens
    
    return len(intersection) / len(union) if union else 0.0


def _is_entity_match(pred: Dict[str, Any], gt: GroundTruthEntity, threshold: float = 0.8) -> bool:
    """
    判断预测实体是否与标注实体匹配。
    使用类型完全匹配 + span 部分匹配。
    """
    if pred.get("type", "") != gt.entity_type:
        return False
    
    pred_span = pred.get("span", "")
    if not pred_span:
        return False
    
    overlap = _span_overlap(pred_span, gt.span)
    return overlap >= threshold


def compute_entity_f1(
    predicted: List[Dict[str, Any]],
    ground_truth: List[GroundTruthEntity],
    match_threshold: float = 0.8,
) -> Tuple[float, float, float, Dict[str, float]]:
    """
    计算实体 F1（支持部分匹配）。

    Args:
        predicted: 预测实体列表
        ground_truth: 标注实体列表
        match_threshold: 匹配阈值（0.0-1.0）

    Returns:
        (precision, recall, f1, f1_by_type)
    """
    if not predicted and not ground_truth:
        return 1.0, 1.0, 1.0, {}

    # 贪心匹配：对每个预测实体，找最匹配的标注实体
    matched_gt_indices: set = set()
    tp = 0
    fp = 0
    
    type_tp: Dict[str, int] = {}
    type_fp: Dict[str, int] = {}
    type_fn: Dict[str, int] = {}

    for pred in predicted:
        best_overlap = 0.0
        best_gt_idx = None
        
        for i, gt in enumerate(ground_truth):
            if i in matched_gt_indices:
                continue
            if pred.get("type", "") != gt.entity_type:
                continue
            overlap = _span_overlap(pred.get("span", ""), gt.span)
            if overlap > best_overlap:
                best_overlap = overlap
                best_gt_idx = i
        
        if best_overlap >= match_threshold and best_gt_idx is not None:
            matched_gt_indices.add(best_gt_idx)
            tp += 1
            etype = pred.get("type", "")
            type_tp[etype] = type_tp.get(etype, 0) + 1
        else:
            fp += 1
            etype = pred.get("type", "")
            type_fp[etype] = type_fp.get(etype, 0) + 1

    # 计算 FN
    fn = len(ground_truth) - len(matched_gt_indices)
    for i, gt in enumerate(ground_truth):
        if i not in matched_gt_indices:
            type_fn[gt.entity_type] = type_fn.get(gt.entity_type, 0) + 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # 按类型计算 F1（加权平均）
    f1_by_type: Dict[str, float] = {}
    all_types = set(type_tp.keys()) | set(type_fp.keys()) | set(type_fn.keys())
    type_weights: Dict[str, int] = {}
    
    for etype in all_types:
        tp_type = type_tp.get(etype, 0)
        fp_type = type_fp.get(etype, 0)
        fn_type = type_fn.get(etype, 0)
        
        p_type = tp_type / (tp_type + fp_type) if (tp_type + fp_type) > 0 else 0.0
        r_type = tp_type / (tp_type + fn_type) if (tp_type + fn_type) > 0 else 0.0
        f1_type = 2 * p_type * r_type / (p_type + r_type) if (p_type + r_type) > 0 else 0.0
        f1_by_type[etype] = round(f1_type, 4)
        type_weights[etype] = tp_type + fn_type

    return round(precision, 4), round(recall, 4), round(f1, 4), f1_by_type


def evaluate_json_parsing(results: List[Dict[str, Any]]) -> Tuple[float, float]:
    """
    评估 JSON 解析成功率和 Schema 合规率。

    Returns:
        (parse_rate, compliance_rate)
    """
    if not results:
        return 0.0, 0.0

    parse_success = 0
    schema_compliant = 0

    for r in results:
        llm_output = r.get("llm_output", {})
        raw_text = r.get("raw_text", "")

        if raw_text:
            parse_success += 1

        entities = llm_output.get("entities", [])
        relations = llm_output.get("relations", [])
        if isinstance(entities, list) and isinstance(relations, list):
            schema_compliant += 1

    return (
        round(parse_success / len(results), 4),
        round(schema_compliant / len(results), 4),
    )


def evaluate_single_sample(
    pipeline_result: Dict[str, Any],
    ground_truth: Optional[GroundTruth] = None,
    verbose: bool = False,
) -> EvaluationMetrics:
    """
    单样本评估主函数。

    Args:
        pipeline_result: NSLEPipeline.run_resume() 返回的结果
        ground_truth: 标注的 ground truth（可选）
        verbose: 是否打印详细诊断信息

    Returns:
        EvaluationMetrics 对象
    """
    metrics = EvaluationMetrics()

    blocks = pipeline_result.get("blocks", [])
    phase1 = pipeline_result.get("phase1_summary", {})

    # 块分类评估
    if ground_truth:
        predicted_classifications = {
            b["block_id"]: b["block_type"]
            for b in phase1.get("block_types", [])
        }
        acc, cov = evaluate_block_classification(
            predicted_classifications,
            ground_truth.block_classifications,
        )
        metrics.block_classification_accuracy = acc
        metrics.block_classification_coverage = cov

    # 实体 F1
    if ground_truth:
        all_entities = []
        for b in blocks:
            llm_out = b.get("llm_output", {})
            all_entities.extend(llm_out.get("entities", []))

        p, r, f1, f1_by_type = compute_entity_f1(all_entities, ground_truth.entities)
        metrics.entity_precision = p
        metrics.entity_recall = r
        metrics.entity_f1 = f1
        metrics.entity_f1_by_type = f1_by_type

    # JSON 解析 & Schema 合规
    parse_rate, compliance_rate = evaluate_json_parsing(blocks)
    metrics.json_parse_rate = parse_rate
    metrics.schema_compliance_rate = compliance_rate

    # 效率指标
    metrics.fallback_count = sum(
        1 for b in blocks if b.get("llm_output", {}).get("_fallback_source") == "pre_extracted"
    )
    metrics.retry_count = sum(
        1 for b in blocks if b.get("retry_count", 0) > 0
    )

    # 置信度指标
    confidences = [b.get("confidence", 0.0) for b in blocks]
    if confidences:
        metrics.avg_confidence = round(sum(confidences) / len(confidences), 4)
        metrics.high_uncertainty_ratio = round(
            sum(1 for c in confidences if c < 0.6) / len(confidences), 4
        )

    if verbose:
        print_diagnostic_report(metrics, pipeline_result)

    return metrics


def print_diagnostic_report(
    metrics: EvaluationMetrics,
    pipeline_result: Dict[str, Any],
) -> None:
    """打印详细诊断报告"""
    print("\n" + "=" * 60)
    print("NS-LE 架构单样本评估报告")
    print("=" * 60)

    print("\n【块分类】")
    print(f"  准确率: {metrics.block_classification_accuracy:.2%}")
    print(f"  覆盖率: {metrics.block_classification_coverage:.2%}")

    print("\n【实体抽取】")
    print(f"  Precision: {metrics.entity_precision:.4f}")
    print(f"  Recall:    {metrics.entity_recall:.4f}")
    print(f"  F1:        {metrics.entity_f1:.4f}")
    if metrics.entity_f1_by_type:
        print("  按类型 F1:")
        for etype, f1 in sorted(metrics.entity_f1_by_type.items()):
            print(f"    {etype}: {f1:.4f}")

    print("\n【格式正确性】")
    print(f"  JSON 解析率: {metrics.json_parse_rate:.2%}")
    print(f"  Schema 合规率: {metrics.schema_compliance_rate:.2%}")

    print("\n【效率】")
    print(f"  Fallback 次数: {metrics.fallback_count}")
    print(f"  重试次数: {metrics.retry_count}")
    print(f"  总延迟: {metrics.total_latency_ms:.0f}ms")

    print("\n【置信度】")
    print(f"  平均置信度: {metrics.avg_confidence:.4f}")
    print(f"  高不确定性比例: {metrics.high_uncertainty_ratio:.2%}")

    blocks = pipeline_result.get("blocks", [])
    print("\n【逐块诊断】")
    for b in blocks:
        block_id = b.get("block_id", "?")
        block_type = b.get("block_type", "?")
        confidence = b.get("confidence", 0.0)
        classification = b.get("classification", "?")
        entity_count = len(b.get("llm_output", {}).get("entities", []))
        decode_method = b.get("decode_method", "standard")

        status_icon = "✅" if classification == "HIGH" else "⚠️" if classification == "LOW" else "❌"
        print(f"  {status_icon} {block_id} ({block_type})")
        print(f"     置信度: {confidence:.4f} | 分类: {classification} | 实体数: {entity_count} | 解码: {decode_method}")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Ground Truth 加载
# ---------------------------------------------------------------------------

def load_ground_truth(json_path: str | Path) -> GroundTruth:
    """
    从 JSON 文件加载 ground truth。

    文件格式：
    {
        "block_classifications": {"b_1": "PERSONAL_INFO", ...},
        "entities": [
            {"type": "SKILL", "span": "Python", "block_id": "b_3"},
            ...
        ],
        "relations": [
            {"head_type": "PROJECT_NAME", "head_span": "...",
             "tail_type": "SKILL", "tail_span": "Python",
             "relation_type": "HAS_SKILL"},
            ...
        ]
    }
    """
    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))

    gt = GroundTruth(
        block_classifications=data.get("block_classifications", {}),
        entities=[
            GroundTruthEntity(
                entity_type=e["type"],
                span=e["span"],
                block_id=e.get("block_id", ""),
            )
            for e in data.get("entities", [])
        ],
        relations=[
            GroundTruthRelation(
                head_type=r["head_type"],
                head_span=r["head_span"],
                tail_type=r["tail_type"],
                tail_span=r["tail_span"],
                relation_type=r["relation_type"],
            )
            for r in data.get("relations", [])
        ],
    )
    return gt


# ---------------------------------------------------------------------------
# 模型选择决策树
# ---------------------------------------------------------------------------

def model_selection_decision(metrics: EvaluationMetrics) -> str:
    """
    根据评估指标给出模型选择建议。

    决策树：
    1. JSON 解析率 < 80% → 切换到 Outlines 约束解码
    2. 实体 F1 < 0.7 → 增强预标注
    3. 两次优化后 F1 < 0.8 → 考虑更大模型
    4. F1 >= 0.85 → 架构验证成功
    """
    if metrics.json_parse_rate < 0.8:
        return "JSON 解析率过低，建议切换到 Outlines 约束解码"

    if metrics.entity_f1 < 0.7:
        return "实体 F1 过低，建议增强预标注（扩大词典 + LLM 辅助）"

    if metrics.entity_f1 < 0.8:
        return "实体 F1 偏低，考虑更大模型（Qwen-1.8B 或 Qwen-7B）"

    if metrics.entity_f1 >= 0.85:
        return "架构验证成功，可进入下一阶段"

    return "架构基本可用，建议继续优化"
