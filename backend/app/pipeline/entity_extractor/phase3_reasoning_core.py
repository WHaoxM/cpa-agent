"""
Phase 3: 不确定性引导推理核 (Uncertainty-Aware Reasoning Core)
基于 NS-LE 架构 + SmartResume 论文 (arXiv:2510.09722)

核心职责：
1. 并行任务分发：3个独立子任务并行调用 LLM（ThreadPoolExecutor）
2. Qwen3 chat_template：使用 tokenizer.apply_chat_template()
3. 真实熵值不确定性度量：output_scores + Shannon Entropy → 字段级置信度
4. Index-based Pointer：长文本字段返回行号范围，后处理从原文精确截取
5. 思考模式选择：复杂字段 /think，简单字段 /no_think
6. 后处理4阶段（SmartResume论文 §3.2.3）
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import torch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 不确定性度量 — 真实 Shannon Entropy
# ---------------------------------------------------------------------------

class UncertaintyMeasure:
    """
    真实熵值不确定性度量。

    使用 model.generate(output_scores=True, return_dict_in_generate=True)
    获取每个生成 token 的 logits，计算 Shannon entropy，
    聚合为序列级/字段级置信度。
    """

    @staticmethod
    def token_entropy(logits: torch.Tensor) -> float:
        """计算单个 token 的 Shannon entropy（bits）"""
        probs = torch.softmax(logits.float(), dim=-1)
        log_probs = torch.log2(probs + 1e-10)
        ent = -(probs * log_probs).sum().item()
        return ent

    @staticmethod
    def sequence_entropy(scores: Tuple[torch.Tensor, ...]) -> List[float]:
        """
        计算生成序列中每个 token 的 entropy。

        Args:
            scores: model.generate(..., output_scores=True) 返回的 scores tuple。
                    每个元素 shape: (batch_size, vocab_size)

        Returns:
            每个 token 的 entropy 列表
        """
        entropies: List[float] = []
        for score in scores:
            ent = UncertaintyMeasure.token_entropy(score[0])
            entropies.append(ent)
        return entropies

    @staticmethod
    def aggregate_confidence(
        entropies: List[float],
        max_entropy: float = 12.0,
    ) -> float:
        """
        将 token 级 entropy 聚合为序列级置信度 (0-1)。

        使用均值 entropy，归一化到 [0, 1]。
        max_entropy: Qwen3-0.6B vocab_size=151936, log2(151936) ≈ 17.2，
                     实际生成中 entropy 很少超过 12。
        """
        if not entropies:
            return 0.5
        avg_ent = np.mean(entropies)
        confidence = max(0.0, 1.0 - avg_ent / max_entropy)
        return round(float(confidence), 4)

    @staticmethod
    def is_high_uncertainty(confidence: float, threshold: float = 0.6) -> bool:
        return confidence < threshold

    @staticmethod
    def field_level_confidence(
        entropies: List[float],
        raw_text: str,
        output_json: dict,
        max_entropy: float = 12.0,
    ) -> Dict[str, float]:
        """
        将 token 级熵映射到 JSON 字段级置信度。

        策略：按输出 JSON 中各字段值在 raw_text 中的位置，
        将对应区间的 token entropy 聚合为字段级置信度。

        Args:
            entropies: 每个生成 token 的 entropy 列表
            raw_text: LLM 原始输出文本
            output_json: 解析后的 JSON 输出
            max_entropy: 最大熵值归一化参数

        Returns:
            {字段名: 置信度(0-1)} 字典
        """
        if not entropies or not output_json:
            return {}

        field_confidences: Dict[str, float] = {}

        for field_name, field_value in output_json.items():
            if isinstance(field_value, str) and field_value:
                pos = raw_text.find(field_value)
                if pos >= 0:
                    token_ratio = pos / max(len(raw_text), 1)
                    token_idx = int(token_ratio * len(entropies))
                    token_idx = min(token_idx, len(entropies) - 1)
                    window = 5
                    start = max(0, token_idx - window // 2)
                    end = min(len(entropies), token_idx + window // 2)
                    local_entropies = entropies[start:end]
                    if local_entropies:
                        avg_ent = np.mean(local_entropies)
                        conf = max(0.0, 1.0 - avg_ent / max_entropy)
                        field_confidences[field_name] = round(float(conf), 4)
                    else:
                        field_confidences[field_name] = 0.5
                else:
                    field_confidences[field_name] = 0.3
            elif isinstance(field_value, list):
                if field_value:
                    field_confidences[field_name] = 0.8
                else:
                    field_confidences[field_name] = 0.5

        return field_confidences


# ---------------------------------------------------------------------------
# JSON 提取工具
# ---------------------------------------------------------------------------

def extract_json_from_text(text: str) -> Optional[dict]:
    """
    从 LLM 输出中提取 JSON 对象。
    SmartResume 论文 §3.2.2: text.find("{") + text.rfind("}")
    """
    # 移除可能的 thinking 标签内容
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        json_str = text[start:end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试修复常见问题
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r",\s*}", "}", json_str)
            json_str = re.sub(r",\s*]", "]", json_str)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 尝试用正则提取字段
                fallback_dict = {}
                for key in ["company", "project", "position", "start_date", "end_date", "school", "degree", "major"]:
                    match = re.search(f'"{key}"\\s*:\\s*"([^"]*)"', text)
                    if match:
                        fallback_dict[key] = match.group(1)
                
                # 尝试提取技能列表
                skills_match = re.search(r'"skills"\s*:\s*\[(.*?)\]', text, re.DOTALL)
                if skills_match:
                    skills_str = skills_match.group(1)
                    skills = re.findall(r'"([^"]*)"', skills_str)
                    if skills:
                        fallback_dict["skills"] = skills
                        
                if fallback_dict:
                    return fallback_dict
    return None


# ---------------------------------------------------------------------------
# 后处理 4 阶段（SmartResume论文 §3.2.3）
# ---------------------------------------------------------------------------

class PostProcessor:
    """
    后处理管线：
    1. Grounded content re-extraction（用行号从原文截取）
    2. Domain-specific normalization（日期格式/名称清洗）
    3. Context-aware de-duplication（基于 text span 去重）
    4. Source text verification（校验 LLM 输出是否在原文中）
    """

    def __init__(self, indexed_lines: Optional[List[str]] = None):
        self._lines = indexed_lines or []

    def process(self, output: dict, original_text: str) -> dict:
        """执行全部4阶段后处理"""
        output = self._grounded_extraction(output)
        output = self._normalize(output)
        output = self._deduplicate(output)
        output = self._verify_source(output, original_text)
        return output

    def _grounded_extraction(self, output: dict) -> dict:
        """阶段1: 用行号范围从原文精确截取描述"""
        if not self._lines:
            return output

        for key in ("description_lines", "desc_lines"):
            if key in output and isinstance(output[key], list) and len(output[key]) == 2:
                start_line, end_line = output[key]
                try:
                    start_idx = max(0, int(start_line) - 1)
                    end_idx = min(len(self._lines), int(end_line))
                    desc = "\n".join(self._lines[start_idx:end_idx])
                    output["description"] = desc
                except (ValueError, TypeError):
                    pass

        # 递归处理列表字段
        for k, v in output.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        self._grounded_extraction(item)

        return output

    @staticmethod
    def _normalize(output: dict) -> dict:
        """阶段2: 领域特定标准化"""
        for key in ("start_date", "end_date"):
            if key in output and isinstance(output[key], str):
                val = output[key].strip()
                val = val.replace("/", "-").replace(".", "-")
                val = re.sub(r"\s+", "", val)
                if val in ("至今", "现在", "present", "Present", "至今"):
                    val = "至今"
                output[key] = val

        # 递归
        for k, v in output.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        PostProcessor._normalize(item)
            elif isinstance(v, dict):
                PostProcessor._normalize(v)

        return output

    @staticmethod
    def _deduplicate(output: dict) -> dict:
        """阶段3: 上下文感知去重"""
        for key in ("skills", "certifications", "required_skills", "preferred_skills"):
            if key in output and isinstance(output[key], list):
                seen = set()
                unique = []
                for item in output[key]:
                    if isinstance(item, str):
                        lower = item.strip().lower()
                        if lower not in seen:
                            seen.add(lower)
                            unique.append(item.strip())
                    elif isinstance(item, dict):
                        name = (item.get("name") or "").strip().lower()
                        if name and name not in seen:
                            seen.add(name)
                            unique.append(item)
                output[key] = unique
        return output

    @staticmethod
    def _verify_source(output: dict, original_text: str) -> dict:
        """阶段4: 源文本验证（标记未验证字段）"""
        text_lower = original_text.lower()

        for key in ("name", "school", "company", "project_name", "job_title"):
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                if val.strip().lower() not in text_lower:
                    output.setdefault("_unverified_fields", []).append(key)

        return output


# ---------------------------------------------------------------------------
# ReasoningCore — 统一入口
# ---------------------------------------------------------------------------

class ReasoningCore:
    """
    Phase 3 推理核：Qwen3 0.6B-SFT 压榨策略。

    - 使用 tokenizer.apply_chat_template() 构建输入
    - 支持并行子任务调用（ThreadPoolExecutor）
    - 真实 entropy 置信度度量
    - Index-based Pointer + 4阶段后处理
    """

    # 简单任务（/no_think）
    SIMPLE_TASKS = {"basic_info", "skills"}
    # 复杂任务（/think）
    COMPLEX_TASKS = {"education", "experience"}

    def __init__(
        self,
        model=None,
        tokenizer=None,
        max_new_tokens: int = 256,  # 减少生成长度以加速
        device: Optional[str] = None,
        batch_size: int = 1,  # 批处理大小
        timeout_seconds: int = 30,  # 推理超时时间
    ):
        self.model = model
        self.tokenizer = tokenizer
        self.max_new_tokens = max_new_tokens
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.uncertainty = UncertaintyMeasure()
        
        # GPU推理优化
        if self.device == "cuda":
            # 设置CUDA优化参数
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True  # 启用cuDNN基准测试
            
            # 设置默认CUDA流
            self.stream = torch.cuda.Stream() if torch.cuda.is_available() else None
            
            logger.info("推理核GPU优化已启用 (batch_size=%d, max_new_tokens=%d, timeout=%ds)", 
                       batch_size, max_new_tokens, timeout_seconds)
        else:
            logger.info("推理核CPU模式 (max_new_tokens=%d, timeout=%ds)", 
                       max_new_tokens, timeout_seconds)

    def _build_input(
        self,
        messages: List[Dict[str, str]],
        enable_thinking: bool = False,
    ) -> Dict[str, torch.Tensor]:
        """
        使用 tokenizer.apply_chat_template() 构建模型输入。
        """
        if hasattr(self.tokenizer, "apply_chat_template"):
            text = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=enable_thinking,
            )
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=2048)
        else:
            # fallback: 手动拼接
            parts = []
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if role == "system":
                    parts.append(f"<|im_start|>system\n{content}<|im_end|>")
                elif role == "user":
                    parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            parts.append("<|im_start|>assistant\n")
            prompt = "\n".join(parts)
            inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)

        return {k: v.to(self.device) for k, v in inputs.items()}

    def infer_single(
        self,
        messages: List[Dict[str, str]],
        task_type: str = "basic_info",
        indexed_lines: Optional[List[str]] = None,
        original_text: str = "",
    ) -> Dict[str, Any]:
        """
        单次推理：构建输入 → 生成 → 提取JSON → 计算entropy → 后处理。

        Args:
            messages: chat messages (from prompts module)
            task_type: 任务类型（决定是否启用 thinking）
            indexed_lines: 带行号的文本行列表（用于 grounded extraction）
            original_text: 原始文本（用于 source verification）

        Returns:
            {
                "output": dict,
                "confidence": float,
                "raw_text": str,
                "is_high_uncertainty": bool,
                "token_entropies": [...],
            }
        """
        if self.model is None or self.tokenizer is None:
            logger.error("模型或分词器未加载")
            return {
                "output": {},
                "confidence": 0.0,
                "raw_text": "",
                "is_high_uncertainty": True,
                "token_entropies": [],
            }

        enable_thinking = task_type in self.COMPLEX_TASKS

        inputs = self._build_input(messages, enable_thinking=enable_thinking)

        # 简单任务优先尝试 Outlines 约束解码
        if task_type in self.SIMPLE_TASKS:
            prompt_text = self.tokenizer.decode(inputs["input_ids"][0], skip_special_tokens=False)
            constrained_result = self._try_outlines_decode(prompt_text)
            if constrained_result is not None:
                post = PostProcessor(indexed_lines=indexed_lines)
                output_json = post.process(constrained_result, original_text)
                return {
                    "output": output_json,
                    "confidence": 0.95,
                    "raw_text": json.dumps(constrained_result, ensure_ascii=False),
                    "is_high_uncertainty": False,
                    "token_entropies": [],
                    "decode_method": "outlines_constrained",
                }

        # 添加推理进度监控和超时处理
        import threading
        import time
        
        # 跨平台超时处理
        timeout_occurred = threading.Event()
        
        def timeout_handler():
            timeout_occurred.set()
        
        # 启动超时计时器
        timer = threading.Timer(self.timeout_seconds, timeout_handler)
        timer.start()
        
        start_time = time.time()
        
        try:
            # GPU推理优化
            if self.device == "cuda" and self.stream is not None:
                # 使用CUDA流进行异步推理
                with torch.cuda.stream(self.stream), torch.no_grad():
                    gen_output = self.model.generate(
                        **inputs,
                        max_new_tokens=self.max_new_tokens,
                        do_sample=False,
                        output_scores=True,
                        return_dict_in_generate=True,
                        eos_token_id=self.tokenizer.eos_token_id,
                        pad_token_id=self.tokenizer.eos_token_id,  # 添加pad_token_id
                        use_cache=True,  # 启用KV缓存
                    )
                # 同步CUDA流
                torch.cuda.synchronize()
            else:
                # CPU或非流式推理
                with torch.no_grad():
                    gen_output = self.model.generate(
                        **inputs,
                        max_new_tokens=self.max_new_tokens,
                        do_sample=False,
                        output_scores=True,
                        return_dict_in_generate=True,
                        eos_token_id=self.tokenizer.eos_token_id,
                        pad_token_id=self.tokenizer.eos_token_id,
                    )
            
            # 取消超时计时器
            timer.cancel()
            
            # 检查是否超时
            if timeout_occurred.is_set():
                raise TimeoutError("推理超时")
                
        except TimeoutError:
            logger.warning("推理超时 (%.1fs)，使用fallback结果", time.time() - start_time)
            # 返回fallback结果
            return {
                "output": {},
                "confidence": 0.3,
                "raw_text": "",
                "is_high_uncertainty": True,
                "token_entropies": [],
                "field_confidences": {},
                "decode_method": "timeout_fallback",
                "_timeout": True,
            }
        except Exception as e:
            # 其他异常处理
            logger.error("推理过程中出错: %s", e)
            # 返回fallback结果
            return {
                "output": {},
                "confidence": 0.1,
                "raw_text": "",
                "is_high_uncertainty": True,
                "token_entropies": [],
                "field_confidences": {},
                "decode_method": "error_fallback",
                "_error": str(e),
            }
        finally:
            # 确保计时器被取消
            try:
                timer.cancel()
            except:
                pass

        # 解码
        generated_ids = gen_output.sequences[0][inputs["input_ids"].shape[1]:]
        raw_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True)

        # 熵计算
        scores = gen_output.scores if hasattr(gen_output, "scores") else ()
        token_entropies = self.uncertainty.sequence_entropy(scores)
        confidence = self.uncertainty.aggregate_confidence(token_entropies)

        # JSON 提取
        output_json = extract_json_from_text(raw_text) or {}

        # 字段级置信度
        field_confidences = self.uncertainty.field_level_confidence(
            token_entropies, raw_text, output_json
        )

        # 后处理
        post = PostProcessor(indexed_lines=indexed_lines)
        output_json = post.process(output_json, original_text)

        return {
            "output": output_json,
            "confidence": confidence,
            "raw_text": raw_text,
            "is_high_uncertainty": self.uncertainty.is_high_uncertainty(confidence),
            "token_entropies": token_entropies,
            "field_confidences": field_confidences,
            "decode_method": "standard",
        }

    def _try_outlines_decode(self, prompt_text: str) -> Optional[dict]:
        """
        尝试使用 Outlines 进行 JSON Schema 约束解码。
        失败时返回 None，调用方应 fallback 到标准解码。
        """
        try:
            import outlines
            from .schemas import EXTRACTION_OUTPUT_SCHEMA
        except ImportError:
            return None

        try:
            generator = outlines.generate.json(self.model, EXTRACTION_OUTPUT_SCHEMA)
            text = generator(prompt_text)
            data = json.loads(text) if isinstance(text, str) else text
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.debug("Outlines 约束解码失败: %s", e)
        return None

    def infer_parallel(
        self,
        task_configs: List[Dict[str, Any]],
        max_workers: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        并行推理多个子任务（SmartResume论文 §3.2.1）。

        Args:
            task_configs: [
                {
                    "messages": [...],
                    "task_type": "basic_info",
                    "indexed_lines": [...],
                    "original_text": "...",
                },
                ...
            ]
            max_workers: 最大并行线程数

        Returns:
            与 task_configs 顺序对应的结果列表
        """
        results = [None] * len(task_configs)

        # 注意：如果只有 CPU 上的单个模型，实际并行可能不带来加速
        # 但在 GPU 推理或 vLLM 部署时有真实并行收益
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {}
            for i, cfg in enumerate(task_configs):
                future = executor.submit(
                    self.infer_single,
                    messages=cfg["messages"],
                    task_type=cfg.get("task_type", "basic_info"),
                    indexed_lines=cfg.get("indexed_lines"),
                    original_text=cfg.get("original_text", ""),
                )
                future_to_idx[future] = i

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.error("子任务 %d 失败: %s", idx, e)
                    results[idx] = {
                        "output": {},
                        "confidence": 0.0,
                        "raw_text": "",
                        "is_high_uncertainty": True,
                        "error": str(e),
                    }

        return results

    def infer_with_retry(
        self,
        messages: List[Dict[str, str]],
        task_type: str = "basic_info",
        indexed_lines: Optional[List[str]] = None,
        original_text: str = "",
        max_retries: int = 2,
        confidence_threshold: float = 0.6,
    ) -> Dict[str, Any]:
        """
        带重试的推理：对低置信度结果自动重试。

        重试策略：
        - 首次调用使用标准 prompt
        - 重试时使用简化 prompt（移除复杂说明）
        - 最多重试 max_retries 次

        Args:
            messages: chat messages
            task_type: 任务类型
            indexed_lines: 带行号的文本行
            original_text: 原始文本
            max_retries: 最大重试次数
            confidence_threshold: 置信度阈值，低于此值触发重试

        Returns:
            推理结果字典
        """
        result = self.infer_single(
            messages=messages,
            task_type=task_type,
            indexed_lines=indexed_lines,
            original_text=original_text,
        )

        for attempt in range(1, max_retries + 1):
            if result["confidence"] >= confidence_threshold:
                break

            logger.warning(
                "Block 置信度 %.2f < %.2f，第 %d 次重试",
                result["confidence"], confidence_threshold, attempt
            )

            simplified_messages = self._simplify_prompt(messages, task_type)
            result = self.infer_single(
                messages=simplified_messages,
                task_type=task_type,
                indexed_lines=indexed_lines,
                original_text=original_text,
            )
            result["retry_count"] = attempt

        return result

    @staticmethod
    def _simplify_prompt(
        messages: List[Dict[str, str]],
        task_type: str,
    ) -> List[Dict[str, str]]:
        """
        简化 prompt 用于重试：移除复杂说明，保留核心指令。
        """
        simplified_system = (
            "Extract resume info. Output ONLY valid JSON. "
            "No explanation, no invented content."
        )

        simplified_messages = []
        for m in messages:
            if m.get("role") == "system":
                simplified_messages.append({"role": "system", "content": simplified_system})
            elif m.get("role") == "user":
                content = m.get("content", "")
                simplified_messages.append({"role": "user", "content": content})

        return simplified_messages

    # ------------------------------------------------------------------
    # 向后兼容接口
    # ------------------------------------------------------------------

    def infer(
        self,
        block_type: str,
        tagged_text: str,
        pre_extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        向后兼容接口：单块推理。
        自动使用 prompts 模块构建 messages。
        """
        try:
            from .prompts.resume_prompts import get_resume_prompt
        except ImportError:
            from prompts.resume_prompts import get_resume_prompt

        # block_type → task_type 映射
        bt_to_task = {
            "SKILLS_CERTS": "skills",
            "EDUCATION": "education",
            "PROJECT_EXPERIENCE": "experience",
            "WORK_EXPERIENCE": "experience",
            "PERSONAL_INFO": "basic_info",
        }
        task_type = bt_to_task.get(block_type, "basic_info")

        messages = get_resume_prompt(
            task_type=task_type,
            indexed_text=tagged_text,
            pre_annotations={
                "detected_skills": pre_extracted.get("skills", []),
                "detected_dates": pre_extracted.get("dates", []),
                "detected_degrees": pre_extracted.get("degrees", []),
            },
        )

        result = self.infer_single(
            messages=messages,
            task_type=task_type,
            original_text=tagged_text,
        )
        result["task_type"] = task_type
        return result
