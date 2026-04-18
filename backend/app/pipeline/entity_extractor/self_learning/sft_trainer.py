"""
自主学习引擎：模型微调框架
使用蒸馏数据对Qwen3-0.6B进行监督微调(SFT)

核心职责：
1. SFT训练循环
2. 知识蒸馏
3. LoRA/QLoRA微调支持
4. 训练监控与早停
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

import torch
from torch.utils.data import DataLoader
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR

from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    get_linear_schedule_with_warmup,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)

logger = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    """训练配置"""
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct"
    output_dir: str = "./sft_output"
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 4
    per_device_eval_batch_size: int = 4
    learning_rate: float = 1e-4
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    logging_steps: int = 10
    save_steps: int = 100
    eval_steps: int = 100
    save_total_limit: int = 3
    fp16: bool = True
    gradient_accumulation_steps: int = 2
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    use_lora: bool = True
    use_qlora: bool = False


class SFTTrainer:
    """
    监督微调训练器
    支持标准SFT和LoRA/QLoRA微调
    """

    def __init__(
        self,
        config: Optional[TrainingConfig] = None,
        device: Optional[str] = None,
    ):
        self.config = config or TrainingConfig()
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self.tokenizer = None
        self.model = None
        self.trainer = None

        self.training_stats = {
            "start_time": None,
            "end_time": None,
            "total_steps": 0,
            "completed_steps": 0,
            "best_eval_loss": float("inf"),
        }

    def setup_model(self) -> bool:
        """
        初始化模型和分词器

        Returns:
            是否成功
        """

        logger.info("=" * 50)
        logger.info("初始化模型: %s", self.config.model_name)
        logger.info("=" * 50)

        try:
            # 加载分词器
            logger.info("加载分词器...")
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.config.model_name,
                trust_remote_code=True,
            )

            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
                self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

            # 加载模型
            logger.info("加载模型...")
            torch_dtype = torch.float16 if self.config.fp16 else torch.float32

            if self.config.use_lora or self.config.use_qlora:
                # 使用LoRA/QLoRA
                self.model = self._load_model_with_lora(torch_dtype)
            else:
                # 标准微调
                self.model = AutoModelForCausalLM.from_pretrained(
                    self.config.model_name,
                    torch_dtype=torch_dtype,
                    device_map="auto" if self.device == "cuda" else None,
                    trust_remote_code=True,
                )

            logger.info("模型加载成功")
            logger.info("  设备: %s", self.device)
            logger.info("  LoRA启用: %s", self.config.use_lora)
            logger.info("  QLoRA启用: %s", self.config.use_qlora)

            return True

        except Exception as e:
            logger.error("模型加载失败: %s", e)
            return False

    def _load_model_with_lora(self, torch_dtype) -> AutoModelForCausalLM:
        """加载带LoRA的模型"""

        try:
            from peft import LoraConfig, get_peft_model, prepare_model_for_kv_training

            # 加载基础模型
            model = AutoModelForCausalLM.from_pretrained(
                self.config.model_name,
                torch_dtype=torch_dtype,
                device_map="auto" if self.device == "cuda" else None,
                trust_remote_code=True,
            )

            # 准备KV缓存训练
            model = prepare_model_for_kv_training(model)

            # LoRA配置
            lora_config = LoraConfig(
                r=self.config.lora_r,
                lora_alpha=self.config.lora_alpha,
                lora_dropout=self.config.lora_dropout,
                bias="none",
                task_type="CAUSAL_LM",
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            )

            model = get_peft_model(model, lora_config)

            # 打印可训练参数
            trainable_params, total_params = self._count_parameters(model)
            logger.info(
                "LoRA参数: 可训练=%d (%.2f%%), 总参数=%d",
                trainable_params,
                trainable_params / total_params * 100,
                total_params,
            )

            return model

        except ImportError:
            logger.warning("peft未安装，回退到标准微调")
            self.config.use_lora = False
            self.config.use_qlora = False
            return AutoModelForCausalLM.from_pretrained(
                self.config.model_name,
                torch_dtype=torch_dtype,
                device_map="auto" if self.device == "cuda" else None,
                trust_remote_code=True,
            )

    def _count_parameters(self, model) -> Tuple[int, int]:
        """统计模型参数"""

        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in model.parameters())
        return trainable, total

    def prepare_dataset(
        self,
        train_data: List[Dict[str, Any]],
        val_data: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        准备训练数据集

        Args:
            train_data: 训练数据列表
            val_data: 验证数据列表（可选）

        Returns:
            是否成功
        """

        if not self.tokenizer:
            logger.error("模型未初始化")
            return False

        logger.info("准备数据集: train=%d", len(train_data))

        try:
            # 创建数据集
            train_dataset = SFTDataset(
                data=train_data,
                tokenizer=self.tokenizer,
                max_length=512,
            )

            val_dataset = None
            if val_data:
                val_dataset = SFTDataset(
                    data=val_data,
                    tokenizer=self.tokenizer,
                    max_length=512,
                )

            # 数据整理器
            data_collator = DataCollatorForLanguageModeling(
                tokenizer=self.tokenizer,
                mlm=False,  # 因果LM
            )

            # 训练参数
            training_args = TrainingArguments(
                output_dir=self.config.output_dir,
                num_train_epochs=self.config.num_train_epochs,
                per_device_train_batch_size=self.config.per_device_train_batch_size,
                per_device_eval_batch_size=self.config.per_device_eval_batch_size,
                learning_rate=self.config.learning_rate,
                warmup_ratio=self.config.warmup_ratio,
                weight_decay=self.config.weight_decay,
                max_grad_norm=self.config.max_grad_norm,
                logging_steps=self.config.logging_steps,
                save_steps=self.config.save_steps,
                eval_steps=self.config.eval_steps,
                save_total_limit=self.config.save_total_limit,
                fp16=self.config.fp16,
                gradient_accumulation_steps=self.config.gradient_accumulation_steps,
                eval_strategy="steps" if val_dataset else "no",
                load_best_model_at_end=True if val_dataset else False,
                report_to=[],
            )

            # 创建Trainer
            self.trainer = Trainer(
                model=self.model,
                args=training_args,
                train_dataset=train_dataset,
                eval_dataset=val_dataset,
                data_collator=data_collator,
            )

            logger.info("数据集准备完成")
            return True

        except Exception as e:
            logger.error("数据集准备失败: %s", e)
            return False

    def train(self) -> Dict[str, Any]:
        """
        执行训练

        Returns:
            训练统计
        """

        if not self.trainer:
            logger.error("Trainer未初始化")
            return {}

        logger.info("=" * 50)
        logger.info("开始SFT训练")
        logger.info("=" * 50)

        self.training_stats["start_time"] = datetime.now().isoformat()

        try:
            # 开始训练
            train_result = self.trainer.train()

            # 保存模型
            logger.info("保存模型...")
            self.trainer.save_model()

            # 保存tokenizer
            self.tokenizer.save_pretrained(self.config.output_dir)

            # 更新统计
            self.training_stats["end_time"] = datetime.now().isoformat()
            self.training_stats["total_steps"] = train_result.training_step
            self.training_stats["completed_steps"] = train_result.training_step
            self.training_stats["final_train_loss"] = train_result.training_loss

            # 评估
            if self.trainer.evaluate():
                eval_results = self.trainer.evaluate()
                self.training_stats["eval_loss"] = eval_results.get("eval_loss", 0)

            logger.info("=" * 50)
            logger.info("训练完成!")
            logger.info("  总步数: %d", self.training_stats["total_steps"])
            logger.info("  最终训练损失: %.4f", self.training_stats["final_train_loss"])
            logger.info("  模型已保存到: %s", self.config.output_dir)
            logger.info("=" * 50)

            return self.training_stats

        except Exception as e:
            logger.error("训练失败: %s", e)
            self.training_stats["error"] = str(e)
            return self.training_stats

    def evaluate(self) -> Dict[str, float]:
        """评估模型"""

        if not self.trainer:
            logger.error("Trainer未初始化")
            return {}

        logger.info("开始评估...")

        eval_results = self.trainer.evaluate()

        logger.info("评估结果: %s", eval_results)

        return eval_results

    def predict(self, text: str) -> Dict[str, Any]:
        """
        使用微调后的模型进行预测

        Args:
            text: 输入文本

        Returns:
            预测结果
        """

        if not self.model or not self.tokenizer:
            logger.error("模型未初始化")
            return {}

        try:
            # 构建prompt
            prompt = f"""从以下文本中提取实体，以JSON格式输出。

文本：{text}

实体类型：SKILL, PROJECT_NAME, COMPANY, SCHOOL, POSITION, PROJECT_ROLE, DATE_RANGE, DEGREE, CERTIFICATE

输出格式：
{{
    "entities": [
        {{"type": "类型", "span": "原文"}}
    ]
}}"""

            # 编码
            inputs = self.tokenizer(prompt, return_tensors="pt")
            if self.device == "cuda":
                inputs = {k: v.cuda() for k, v in inputs.items()}

            # 生成
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=256,
                    temperature=0.1,
                    do_sample=True,
                )

            # 解码
            generated_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

            # 提取JSON部分
            json_start = generated_text.find("{")
            json_end = generated_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                result_json = generated_text[json_start:json_end]
                result = json.loads(result_json)
            else:
                result = {"raw_output": generated_text}

            return result

        except Exception as e:
            logger.error("预测失败: %s", e)
            return {"error": str(e)}


class SFTDataset(torch.utils.data.Dataset):
    """
    SFT数据集
    """

    def __init__(
        self,
        data: List[Dict[str, Any]],
        tokenizer,
        max_length: int = 512,
    ):
        self.data = data
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        item = self.data[idx]

        input_text = item.get("input", "")
        target_text = item.get("target", "")

        if isinstance(target_text, dict):
            import json
            target_text = json.dumps(target_text, ensure_ascii=False)

        # 构建输入: prompt + target
        full_text = f"{input_text}\n\n{target_text}"

        # 编码
        encoding = self.tokenizer(
            full_text,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        # labels: 只计算target部分的loss
        input_encoding = self.tokenizer(
            input_text,
            max_length=self.max_length,
            truncation=True,
            return_tensors="pt",
        )

        input_length = input_encoding["input_ids"].shape[1]

        labels = encoding["input_ids"].clone()
        labels[:, :input_length] = -100  # 只在target部分计算loss

        return {
            "input_ids": encoding["input_ids"].squeeze(),
            "attention_mask": encoding["attention_mask"].squeeze(),
            "labels": labels.squeeze(),
        }


class KnowledgeDistillationTrainer(SFTTrainer):
    """
    知识蒸馏训练器
    在SFT基础上加入蒸馏损失
    """

    def __init__(
        self,
        config: Optional[TrainingConfig] = None,
        teacher_model=None,
        temperature: float = 2.0,
        alpha: float = 0.5,
    ):
        super().__init__(config, device=None)

        self.teacher_model = teacher_model
        self.temperature = temperature
        self.alpha = alpha  # 蒸馏损失权重

    def setup_distillation(self) -> bool:
        """设置蒸馏环境"""

        if not self.teacher_model:
            logger.warning("没有提供teacher模型，回退到标准SFT")
            return self.setup_model()

        # 冻结teacher模型
        for param in self.teacher_model.parameters():
            param.requires_grad = False

        self.teacher_model.eval()

        logger.info("知识蒸馏环境设置完成")
        logger.info("  Temperature: %.1f", self.temperature)
        logger.info("  Alpha (蒸馏权重): %.2f", self.alpha)

        return True

    def compute_distillation_loss(
        self,
        student_logits,
        teacher_logits,
        labels,
    ) -> torch.Tensor:
        """
        计算蒸馏损失

        Args:
            student_logits: 学生模型logits
            teacher_logits: 教师模型logits
            labels: 真实标签

        Returns:
            蒸馏损失
        """

        # 温度缩放
        student_logits_scaled = student_logits / self.temperature
        teacher_logits_scaled = teacher_logits / self.temperature

        # KL散度损失（蒸馏损失）
        loss_kd = torch.nn.functional.kl_div(
            torch.log_softmax(student_logits_scaled, dim=-1),
            torch.softmax(teacher_logits_scaled, dim=-1),
            reduction="batchmean",
        ) * (self.temperature ** 2)

        # 标准交叉熵损失
        loss_ce = torch.nn.functional.cross_entropy(
            student_logits.view(-1, student_logits.size(-1)),
            labels.view(-1),
        )

        # 加权组合
        total_loss = self.alpha * loss_kd + (1 - self.alpha) * loss_ce

        return total_loss


class ContinuousLearningManager:
    """
    持续学习管理器
    管理增量微调和模型版本
    """

    def __init__(
        self,
        model_dir: Path,
        max_versions: int = 5,
    ):
        self.model_dir = model_dir
        self.max_versions = max_versions
        self.current_version = None

        self.versions = []

    def save_version(
        self,
        model,
        tokenizer,
        metrics: Dict[str, Any],
    ) -> str:
        """
        保存模型版本

        Returns:
            版本ID
        """

        version_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        version_dir = self.model_dir / f"v_{version_id}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # 保存模型
        model.save_pretrained(version_dir)
        tokenizer.save_pretrained(version_dir)

        # 保存指标
        metrics_path = version_dir / "metrics.json"
        with metrics_path.open("w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

        # 更新版本列表
        self.versions.append({
            "version_id": version_id,
            "path": str(version_dir),
            "metrics": metrics,
            "timestamp": version_id,
        })

        # 清理旧版本
        self._cleanup_old_versions()

        self.current_version = version_id

        logger.info("模型版本已保存: %s", version_id)

        return version_id

    def load_version(self, version_id: str) -> Optional[Tuple[Any, Any]]:
        """
        加载指定版本模型

        Returns:
            (model, tokenizer)
        """

        version_dir = self.model_dir / f"v_{version_id}"

        if not version_dir.exists():
            logger.error("版本不存在: %s", version_id)
            return None

        try:
            model = AutoModelForCausalLM.from_pretrained(
                version_dir,
                trust_remote_code=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(
                version_dir,
                trust_remote_code=True,
            )

            logger.info("已加载模型版本: %s", version_id)

            return model, tokenizer

        except Exception as e:
            logger.error("加载模型失败: %s", e)
            return None

    def _cleanup_old_versions(self):
        """清理旧版本"""

        if len(self.versions) <= self.max_versions:
            return

        # 按时间排序
        sorted_versions = sorted(
            self.versions,
            key=lambda v: v["timestamp"],
            reverse=True,
        )

        # 删除旧版本
        for version in sorted_versions[self.max_versions:]:
            version_dir = Path(version["path"])
            if version_dir.exists():
                import shutil
                shutil.rmtree(version_dir)
                logger.info("已删除旧版本: %s", version["version_id"])

        # 更新版本列表
        self.versions = sorted_versions[:self.max_versions]

    def list_versions(self) -> List[Dict[str, Any]]:
        """列出所有版本"""

        return [
            {
                "version_id": v["version_id"],
                "metrics": v["metrics"],
                "timestamp": v["timestamp"],
            }
            for v in self.versions
        ]


def create_sft_trainer(
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct",
    output_dir: str = "./sft_output",
    use_lora: bool = True,
    **kwargs,
) -> SFTTrainer:
    """
    创建SFT训练器的工厂函数
    """

    config = TrainingConfig(
        model_name=model_name,
        output_dir=output_dir,
        use_lora=use_lora,
        **kwargs,
    )

    trainer = SFTTrainer(config=config)

    if not trainer.setup_model():
        raise RuntimeError("模型初始化失败")

    return trainer