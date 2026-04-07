"""
自主学习引擎 (Self-Learning Engine)
使用API标注数据进行渐进式学习，持续提升小模型能力

核心模块：
- api_labeler: API标注器，支持GPT-4/Claude等多种API
- rule_learner: 规则学习器，从标注数据中挖掘Patterns
- distillation_pipeline: 数据蒸馏管道，格式化训练数据
- sft_trainer: 模型微调框架，支持SFT和LoRA/QLoRA
- active_learning: 主动学习反馈循环
- autonomous_loop: 3-Phase 自主学习闭环控制器

使用流程：
1. 使用API对简历数据进行高质量标注
2. 从标注数据中学习实体Patterns
3. 将数据蒸馏为训练格式
4. 使用蒸馏数据微调小模型
5. 用微调后的模型替代规则，实现自主进化

闭环流程（autonomous_loop）：
用户输入简历 → Phase 1 教师数据工厂 → Phase 1.5 合成扩充
→ Phase 2 渐进式蒸馏 → Phase 3 边缘自主推理 → 回流
"""

from .api_labeler import (
    APIProvider,
    APILabelingEngine,
    SelfLearningPipeline,
    create_self_learning_pipeline,
    AnnotationResult,
    BaseAnnotator,
    OpenAIAnnotator,
    AnthropicAnnotator,
)

from .rule_learner import (
    PatternMiner,
    RuleGenerator,
    RuleEvaluator,
    RuleLearner,
    LearnedPattern,
    merge_with_existing_rules,
)

from .distillation_pipeline import (
    DataCleaner,
    DataFormatter,
    DistillationDataset,
    TrainValSplitter,
    DistillationPipeline,
    TrainingSample,
)

from .sft_trainer import (
    TrainingConfig,
    SFTTrainer,
    SFTDataset,
    KnowledgeDistillationTrainer,
    ContinuousLearningManager,
    create_sft_trainer,
)

from .active_learning import (
    FeedbackType,
    Feedback,
    ConfidenceAnalyzer,
    FeedbackCollector,
    SampleSelector,
    IncrementalLearningScheduler,
    SelfLearningLoop,
    ActiveLearningStats,
)

from .autonomous_loop import (
    AutonomousLearningLoop,
    ReplayBuffer,
    OODDetector,
    FieldConstraintValidator,
    LoopConfig,
    TemplateRenderer,
    TeacherModelClient,
)

__all__ = [
    # API Labeler
    "APIProvider",
    "APILabelingEngine",
    "SelfLearningPipeline",
    "create_self_learning_pipeline",
    "AnnotationResult",
    "BaseAnnotator",
    "OpenAIAnnotator",
    "AnthropicAnnotator",
    # Rule Learner
    "PatternMiner",
    "RuleGenerator",
    "RuleEvaluator",
    "RuleLearner",
    "LearnedPattern",
    "merge_with_existing_rules",
    # Distillation Pipeline
    "DataCleaner",
    "DataFormatter",
    "DistillationDataset",
    "TrainValSplitter",
    "DistillationPipeline",
    "TrainingSample",
    # SFT Trainer
    "TrainingConfig",
    "SFTTrainer",
    "SFTDataset",
    "KnowledgeDistillationTrainer",
    "ContinuousLearningManager",
    "create_sft_trainer",
    # Active Learning
    "FeedbackType",
    "Feedback",
    "ConfidenceAnalyzer",
    "FeedbackCollector",
    "SampleSelector",
    "IncrementalLearningScheduler",
    "SelfLearningLoop",
    "ActiveLearningStats",
    # Autonomous Loop (3-Phase)
    "AutonomousLearningLoop",
    "ReplayBuffer",
    "OODDetector",
    "FieldConstraintValidator",
    "LoopConfig",
    "TemplateRenderer",
    "TeacherModelClient",
]