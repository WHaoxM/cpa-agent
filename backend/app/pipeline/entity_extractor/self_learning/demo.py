#!/usr/bin/env python3
"""
自主学习引擎 - 端到端示例
演示如何使用API标注数据进行渐进式学习

使用流程：
1. API标注简历数据
2. 学习Patterns并更新规则
3. 蒸馏数据准备
4. SFT微调小模型
5. 用微调模型替换规则兜底
"""

import sys
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def demo_api_labeling():
    """演示API标注"""
    logger.info("=" * 60)
    logger.info("步骤1: API标注")
    logger.info("=" * 60)

    try:
        from self_learning import (
            APILabelingEngine,
            APIProvider,
            OpenAIAnnotator,
        )

        engine = APILabelingEngine()

        # 添加API（需要真实API Key）
        # engine.register_annotator(APIProvider.OPENAI, OpenAIAnnotator("your-api-key"))

        logger.info("API标注引擎已初始化")
        logger.info("（需要配置真实的API Key才能进行实际标注）")

        return engine

    except Exception as e:
        logger.error("API标注演示失败: %s", e)
        return None


def demo_rule_learning():
    """演示规则学习"""
    logger.info("=" * 60)
    logger.info("步骤2: 规则学习")
    logger.info("=" * 60)

    try:
        from self_learning import RuleLearner, merge_with_existing_rules

        learner = RuleLearner(min_pattern_freq=2, min_confidence=0.7)

        # 模拟标注数据
        sample_annotations = [
            {
                "raw_text": "使用LangChain和LangGraph开发RAG应用",
                "entities": [
                    {"type": "SKILL", "span": "LangChain"},
                    {"type": "SKILL", "span": "LangGraph"},
                    {"type": "PROJECT_NAME", "span": "RAG应用"},
                ],
            },
            {
                "raw_text": "基于FastAPI和Neo4j构建知识图谱系统",
                "entities": [
                    {"type": "SKILL", "span": "FastAPI"},
                    {"type": "TOOL", "span": "Neo4j"},
                    {"type": "PROJECT_NAME", "span": "知识图谱系统"},
                ],
            },
            {
                "raw_text": "使用Milvus向量数据库进行语义检索",
                "entities": [
                    {"type": "TOOL", "span": "Milvus"},
                    {"type": "SKILL", "span": "语义检索"},
                ],
            },
        ]

        # 学习规则
        stats = learner.learn(sample_annotations)

        logger.info("规则学习完成: %s", stats)

        # 加载已有规则并合并
        existing_rules = {
            "SKILL": [
                {"type": "skill", "pattern": "Python", "confidence": 0.9, "frequency": 100},
            ]
        }

        merged = merge_with_existing_rules(learner.adopted_rules, existing_rules)
        logger.info("规则合并完成: %d 类型", len(merged))

        return learner

    except Exception as e:
        logger.error("规则学习演示失败: %s", e)
        return None


def demo_distillation():
    """演示数据蒸馏"""
    logger.info("=" * 60)
    logger.info("步骤3: 数据蒸馏")
    logger.info("=" * 60)

    try:
        from self_learning import DistillationPipeline

        pipeline = DistillationPipeline(output_dir=Path("./demo_distillation_data"))

        # 模拟标注数据
        annotations = [
            {
                "raw_text": "熟练使用Python、Java进行后端开发",
                "entities": [
                    {"type": "SKILL", "span": "Python"},
                    {"type": "SKILL", "span": "Java"},
                ],
                "confidence": 0.95,
            },
            {
                "raw_text": "曾在阿里巴巴担任高级Java工程师",
                "entities": [
                    {"type": "COMPANY", "span": "阿里巴巴"},
                    {"type": "POSITION", "span": "高级Java工程师"},
                ],
                "confidence": 0.92,
            },
            {
                "raw_text": "开发过基于LangChain的智能问答系统",
                "entities": [
                    {"type": "SKILL", "span": "LangChain"},
                    {"type": "PROJECT_NAME", "span": "智能问答系统"},
                ],
                "confidence": 0.88,
            },
        ]

        # 准备蒸馏数据
        stats = pipeline.prepare(annotations)

        logger.info("数据蒸馏完成: %s", stats)

        return pipeline

    except Exception as e:
        logger.error("数据蒸馏演示失败: %s", e)
        return None


def demo_sft_training():
    """演示SFT微调"""
    logger.info("=" * 60)
    logger.info("步骤4: SFT微调")
    logger.info("=" * 60)

    try:
        from self_learning import SFTTrainer, TrainingConfig

        config = TrainingConfig(
            model_name="Qwen/Qwen2.5-0.5B-Instruct",
            output_dir="./demo_sft_output",
            num_train_epochs=1,
            per_device_train_batch_size=2,
            use_lora=True,
        )

        trainer = SFTTrainer(config=config)

        # 初始化模型
        if not trainer.setup_model():
            logger.warning("模型初始化失败（可能是环境问题），跳过实际训练演示")
            logger.info("SFT配置已准备好，可以在GPU环境完整运行")
            return None

        # 模拟训练数据
        train_data = [
            {
                "input": "从以下文本中提取实体：使用Python和FastAPI开发Web应用",
                "target": '{"entities": [{"type": "SKILL", "span": "Python"}, {"type": "SKILL", "span": "FastAPI"}]}',
            },
        ]

        # 准备数据集
        trainer.prepare_dataset(train_data)

        logger.info("SFT训练器已初始化")
        logger.info("配置：")
        logger.info("  - 模型: %s", config.model_name)
        logger.info("  - LoRA: %s", config.use_lora)
        logger.info("  - 输出: %s", config.output_dir)

        # 注意：实际训练需要GPU环境和更多数据
        # trainer.train()

        return trainer

    except Exception as e:
        logger.error("SFT微调演示失败: %s", e)
        return None


def demo_active_learning():
    """演示主动学习"""
    logger.info("=" * 60)
    logger.info("步骤5: 主动学习循环")
    logger.info("=" * 60)

    try:
        from self_learning import SelfLearningLoop, FeedbackCollector, SampleSelector

        loop = SelfLearningLoop()

        # 初始化组件
        loop.sample_selector = SampleSelector(strategy="combined")
        loop.feedback_collector = FeedbackCollector()

        # 模拟未标注数据
        unlabeled_samples = [
            {"id": f"sample_{i}", "text": f"简历内容示例 {i}"}
            for i in range(20)
        ]

        # 执行一轮主动学习
        result = loop.run_iteration(
            unlabeled_samples,
            use_api_annotation=False,  # 演示模式，不调用API
            batch_size=5,
        )

        logger.info("主动学习一轮完成: %s", result)

        # 获取学习报告
        report = loop.get_learning_report()
        logger.info("学习报告: %s", json.dumps(report, indent=2, ensure_ascii=False))

        return loop

    except Exception as e:
        logger.error("主动学习演示失败: %s", e)
        return None


def main():
    """主函数"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("    自主学习引擎 - 端到端演示")
    logger.info("=" * 60)
    logger.info("")
    logger.info("目标：通过API标注学习 → 规则挖掘 → 蒸馏微调")
    logger.info("     让0.6B小模型逐步掌握实体提取能力")
    logger.info("")

    # 执行演示
    demo_api_labeling()
    demo_rule_learning()
    demo_distillation()
    demo_sft_training()
    demo_active_learning()

    logger.info("")
    logger.info("=" * 60)
    logger.info("    演示完成!")
    logger.info("=" * 60)
    logger.info("")
    logger.info("下一步：")
    logger.info("1. 配置API Key (OPENAI_API_KEY 或 ANTHROPIC_API_KEY)")
    logger.info("2. 准备大量简历数据")
    logger.info("3. 运行完整的学习流程")
    logger.info("4. 微调后的模型将逐步替代规则兜底")
    logger.info("")


if __name__ == "__main__":
    main()