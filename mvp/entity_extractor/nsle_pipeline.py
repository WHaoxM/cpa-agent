"""
NS-LE 架构完整管道：神经符号逻辑增强提取系统
基于 SmartResume 论文 (arXiv:2510.09722) + NS-LE 架构

唯一 pipeline 入口，整合 Phase 1-4：
- Phase 1: 物理解析层（MinerU + YOLOv10）
- Phase 2: 符号化隐私层（伦理过滤 + PII + AC + XML）
- Phase 3: 推理核（Qwen3-0.6B-SFT + 真实熵值 + 并行任务 + Index Pointer）
- Phase 4: 逻辑守卫与闭环
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch

try:
    from .phase1_physical_layer import PhysicalParseLayer, DocumentBlock
    from .phase2_symbolic_privacy_layer import SymbolicPreprocessor
    from .phase3_reasoning_core import ReasoningCore
    from .phase4_logic_guard import LogicGuard, FeedbackLoop, ActiveLearningDistiller
    from .output_adapter import OutputAdapter
    from .prompts.resume_prompts import get_resume_prompt
    from .prompts.jd_prompts import get_jd_prompt
except ImportError:
    from phase1_physical_layer import PhysicalParseLayer, DocumentBlock
    from phase2_symbolic_privacy_layer import SymbolicPreprocessor
    from phase3_reasoning_core import ReasoningCore
    from phase4_logic_guard import LogicGuard, FeedbackLoop, ActiveLearningDistiller
    from output_adapter import OutputAdapter
    from prompts.resume_prompts import get_resume_prompt
    from prompts.jd_prompts import get_jd_prompt

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = ROOT / "model" / "SmartResume" / "Qwen3-0.6B"
DEFAULT_YOLO_PATH = ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx"
DEFAULT_MINERU_BASE = ROOT / "data" / "mineru_output" / "简历" / "auto"
DEFAULT_OUT_PATH = ROOT / "outdata" / "entity_extraction_result_nsle.json"


class NSLEPipeline:
    """
    NS-LE 架构完整管道。

    使用方式：
        pipeline = NSLEPipeline()
        result = pipeline.run_resume(content_v2_path=..., pdf_path=...)
        result = pipeline.run_jd(jd_text="...")
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        yolo_model_path: Optional[Path] = None,
        load_model: bool = True,
    ):
        logger.info("[初始化] NS-LE 架构...")

        self._model_path = model_path or DEFAULT_MODEL_PATH
        self._yolo_path = yolo_model_path or DEFAULT_YOLO_PATH

        # Phase 1: 物理解析层
        yolo_path = self._yolo_path if self._yolo_path.exists() else None
        self.physical_layer = PhysicalParseLayer(yolo_model_path=yolo_path)

        # Phase 2: 符号化隐私层
        self.symbolic_layer = SymbolicPreprocessor(doc_type="resume")

        # Phase 3: 推理核
        self.model = None
        self.tokenizer = None
        self.reasoning_core = None

        if load_model:
            self._load_model()

        # Phase 4: 逻辑守卫
        self.logic_guard = LogicGuard()
        self.feedback_loop = FeedbackLoop(confidence_threshold=0.7)
        self.active_learner = ActiveLearningDistiller()

        logger.info("[初始化] 完成")

    def _load_model(self):
        """加载 Qwen3-0.6B-SFT 模型 - GPU优化版本"""
        if not self._model_path.exists():
            logger.warning("模型路径不存在: %s，跳过模型加载", self._model_path)
            self.reasoning_core = ReasoningCore(model=None, tokenizer=None)
            return

        logger.info("加载 Qwen3-0.6B 模型 (GPU优化): %s", self._model_path)
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            from modelscope import AutoModelForCausalLM, AutoTokenizer

        # GPU优化配置
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        
        # 优化GPU内存使用
        torch.cuda.empty_cache()
        
        # 加载tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self._model_path), 
            trust_remote_code=True,
            padding_side="left"  # 优化批处理
        )

        # 优化模型加载参数
        load_kwargs = {
            "dtype": dtype,  # 修复: 使用dtype而不是torch_dtype
            "trust_remote_code": True,
        }
        
        # 添加GPU特定优化
        if device == "cuda":
            # 检查是否安装了accelerate
            try:
                import accelerate
                load_kwargs.update({
                    "device_map": "auto",
                    "low_cpu_mem_usage": True,
                })
                logger.info("已启用accelerate设备映射")
            except ImportError:
                logger.warning("未安装accelerate，使用手动设备映射")
                load_kwargs["device_map"] = None
            
            # 检查Flash Attention支持 - 安全处理
            try:
                # 检查是否安装了flash_attn
                import flash_attn
                if hasattr(torch.nn.functional, 'scaled_dot_product_attention'):
                    load_kwargs["attn_implementation"] = "flash_attention_2"
                    logger.info("已启用Flash Attention 2")
                else:
                    load_kwargs["attn_implementation"] = "eager"
                    logger.info("Flash Attention 2不可用，使用eager模式")
            except ImportError:
                # Flash Attention 2未安装，使用eager模式
                load_kwargs["attn_implementation"] = "eager"
                logger.info("Flash Attention 2未安装，使用eager模式")

        self.model = AutoModelForCausalLM.from_pretrained(
            str(self._model_path), 
            **load_kwargs
        )
        
        # 如果device_map未自动设置，手动移动到GPU
        if device == "cuda" and not hasattr(self.model, 'hf_device_map'):
            self.model = self.model.to(device)
        
        self.model.eval()
        
        # 启用推理优化
        if device == "cuda":
            # 启用torch.compile (PyTorch 2.0+)
            if hasattr(torch, 'compile') and torch.cuda.get_device_capability()[0] >= 7:
                try:
                    self.model = torch.compile(self.model, mode="reduce-overhead", fullgraph=True)
                    logger.info("已启用torch.compile优化")
                except Exception as e:
                    logger.warning("torch.compile优化失败: %s", e)
            
            # 设置CUDA流优化
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            logger.info("已启用TF32优化")

        self.reasoning_core = ReasoningCore(
            model=self.model,
            tokenizer=self.tokenizer,
            device=device,
            max_new_tokens=256,  # 减少生成长度以加速
        )
        
        # 检查GPU内存使用
        if device == "cuda":
            memory_allocated = torch.cuda.memory_allocated() / 1024**3
            memory_reserved = torch.cuda.memory_reserved() / 1024**3
            logger.info("模型加载完成 (device=%s, dtype=%s, 显存使用: %.1fGB/%.1fGB)", 
                       device, dtype, memory_allocated, memory_reserved)
        else:
            logger.info("模型加载完成 (device=%s, dtype=%s)", device, dtype)

    # ------------------------------------------------------------------
    # 简历处理
    # ------------------------------------------------------------------

    def run_resume(
        self,
        content_v2_path: Optional[Path] = None,
        content_v1_path: Optional[Path] = None,
        middle_json_path: Optional[Path] = None,
        pdf_path: Optional[Path] = None,
        out_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        运行简历抽取管道。

        Args:
            content_v2_path: MinerU content_list_v2.json
            content_v1_path: MinerU content_list.json (fallback)
            middle_json_path: MinerU middle.json (fallback)
            pdf_path: 原始 PDF（用于 YOLOv10 版面检测）
            out_path: 输出文件路径
        """
        # 生成 trace 信息
        trace_id = str(uuid.uuid4())[:8]
        input_hash = self._compute_input_hash(content_v2_path or content_v1_path or middle_json_path)

        self.symbolic_layer = SymbolicPreprocessor(doc_type="resume")

        # Phase 1: 物理解析
        logger.info("[Phase 1] 物理解析与契约对齐...")
        blocks = self.physical_layer.parse_resume_pdf(
            content_v2_path=content_v2_path,
            middle_json_path=middle_json_path,
            content_v1_path=content_v1_path,
            pdf_path=pdf_path,
        )
        target_blocks = self.physical_layer.filter_target_blocks(blocks, doc_type="resume")
        coverage = self.physical_layer.compute_coverage(blocks)
        logger.info("识别到 %d 个目标块 (共 %d 块)", len(target_blocks), len(blocks))

        # 处理每个块（带进度监控）
        logger.info("[进度监控] 开始处理 %d 个目标块", len(target_blocks))
        results = self._process_blocks(target_blocks, blocks)

        # 组装最终输出
        final_result = self._assemble_output(
            results=results,
            all_blocks=blocks,
            coverage=coverage,
            trace_id=trace_id,
            input_hash=input_hash,
            doc_type="resume",
            out_path=out_path or DEFAULT_OUT_PATH,
        )
        
        # 输出处理统计
        logger.info("[处理完成] 总块数: %d, 成功: %d, 失败: %d", 
                   len(target_blocks),
                   len([r for r in results if r.get("is_valid", False)]),
                   len([r for r in results if not r.get("is_valid", True)]))
        
        return final_result

    # ------------------------------------------------------------------
    # JD 处理
    # ------------------------------------------------------------------

    def run_jd(
        self,
        jd_text: str,
        out_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """运行 JD 抽取管道"""
        trace_id = str(uuid.uuid4())[:8]
        input_hash = hashlib.sha256(jd_text.encode()).hexdigest()[:12]

        self.symbolic_layer = SymbolicPreprocessor(doc_type="jd")

        # Phase 1: JD TXT 路由
        logger.info("[Phase 1] JD 文本解析...")
        blocks = self.physical_layer.parse_jd_txt(jd_text)
        target_blocks = self.physical_layer.filter_target_blocks(blocks, doc_type="jd")
        coverage = self.physical_layer.compute_coverage(blocks)

        # 处理
        results = self._process_blocks(target_blocks, blocks, doc_type="jd")

        return self._assemble_output(
            results=results,
            all_blocks=blocks,
            coverage=coverage,
            trace_id=trace_id,
            input_hash=input_hash,
            doc_type="jd",
            out_path=out_path,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _process_blocks(
        self,
        target_blocks: List[DocumentBlock],
        all_blocks: List[DocumentBlock],
        doc_type: str = "resume",
    ) -> List[Dict[str, Any]]:
        """Phase 2-4 处理每个目标块"""
        results: List[Dict[str, Any]] = []

        # 上下文状态机：用于解决跨块图谱断裂
        state_context = {
            "COMPANY": None,
            "PROJECT_NAME": None,
            "SCHOOL": None,
        }
        previous_block_result: Optional[Dict[str, Any]] = None

        # 进度监控
        total_blocks = len(target_blocks)
        processed_count = 0
        
        for i, block in enumerate(target_blocks):
            processed_count += 1
            
            # 进度显示
            if processed_count % 5 == 0 or processed_count == total_blocks:
                logger.info("[进度] 处理中: %d/%d (%.1f%%)", 
                           processed_count, total_blocks, 
                           processed_count/total_blocks*100)
            
            # 修复：如果当前块是 WORK_DESCRIPTION 类型，从前一个块的输出中获取项目/公司上下文
            is_work_description = block.block_type == "WORK_EXPERIENCE" and any(
                kw in (block.heading or "")
                for kw in ["主要工作", "工作内容", "职责描述", "项目描述", "详细描述"]
            )

            if is_work_description and previous_block_result:
                # 从前一个块获取项目/公司上下文
                prev_llm_output = previous_block_result.get("llm_output", {})
                prev_entities = prev_llm_output.get("entities", [])

                for e in prev_entities:
                    if e.get("type") == "PROJECT_NAME":
                        state_context["PROJECT_NAME"] = {"type": "PROJECT_NAME", "span": e.get("span", "")}
                    elif e.get("type") == "COMPANY":
                        state_context["COMPANY"] = {"type": "COMPANY", "span": e.get("span", "")}
                    elif e.get("type") == "SCHOOL":
                        state_context["SCHOOL"] = {"type": "SCHOOL", "span": e.get("span", "")}

                logger.info(
                    "  WORK_DESCRIPTION 上下文继承: project=%s, company=%s",
                    state_context.get("PROJECT_NAME", {}).get("span") if state_context.get("PROJECT_NAME") else None,
                    state_context.get("COMPANY", {}).get("span") if state_context.get("COMPANY") else None,
                )

            logger.info("[处理] %s (%s)", block.block_id, block.block_type)

            # Phase 2: 符号化隐私处理
            symbolic_output = self.symbolic_layer.process(block.text)

            # Phase 3: 推理核 — 注入 heading 作为上下文锚点
            llm_input_text = symbolic_output["tagged_text"]

            # 修复：将 state_context 的更新提前到 LLM 调用之前
            # 这样 WORK_DESCRIPTION 块可以在 prompt 中获得前一个块的项目上下文
            # 从 symbolic_output 中提取实体信息（不重新调用）
            temp_entities = []
            for e in symbolic_output.get("entities", []):
                if e.get("type") == "COMPANY":
                    temp_entities.append({"type": "COMPANY", "span": e.get("span", "")})
                elif e.get("type") == "PROJECT_NAME":
                    temp_entities.append({"type": "PROJECT_NAME", "span": e.get("span", "")})

            found_company_in_block = next((e for e in temp_entities if e["type"] == "COMPANY"), None)
            found_project_in_block = next((e for e in temp_entities if e["type"] == "PROJECT_NAME"), None)

            if found_company_in_block and block.block_type in ("WORK_EXPERIENCE", "PROJECT_EXPERIENCE"):
                state_context["COMPANY"] = found_company_in_block
                if found_project_in_block is None:
                    state_context["PROJECT_NAME"] = None
            if found_project_in_block and block.block_type in ("PROJECT_EXPERIENCE", "WORK_EXPERIENCE"):
                state_context["PROJECT_NAME"] = found_project_in_block

            # 构建上下文前缀，注入到 LLM prompt 中
            context_prefix = ""
            if block.heading:
                # 检查是否是描述性标题（如"项目描述："、"工作内容："）
                is_descriptive_heading = any(
                    kw in block.heading
                    for kw in ["项目描述", "工作内容", "职责描述", "详细描述"]
                )
                if is_descriptive_heading:
                    # 从状态机获取当前项目/公司名
                    current_project = state_context.get("PROJECT_NAME")
                    current_company = state_context.get("COMPANY")
                    if current_project:
                        context_prefix = f"【当前项目】{current_project.get('span', '')}\n"
                    elif current_company:
                        context_prefix = f"【当前公司】{current_company.get('span', '')}\n"

                llm_input_text = f"【标题】{block.heading}\n{llm_input_text}"

            # 注入上下文前缀
            if context_prefix:
                llm_input_text = context_prefix + llm_input_text

            if self.reasoning_core:
                reasoning_output = self.reasoning_core.infer(
                    block.block_type,
                    llm_input_text,
                    symbolic_output["pre_extracted"],
                )
            else:
                reasoning_output = {
                    "output": {},
                    "confidence": 0.0,
                    "raw_text": "",
                    "is_high_uncertainty": True,
                }

            # Phase 3.5: OutputAdapter — task-specific JSON → 契约 entities/relations
            task_type = reasoning_output.get("task_type", "basic_info")
            adapted = OutputAdapter.adapt(
                task_output=reasoning_output["output"],
                task_type=task_type,
                block_type=block.block_type,
            )

            # Phase 4: 逻辑守卫（只做验证，不做格式转换）
            sanitized = self.logic_guard.sanitize_output(adapted)

            # 修复：为每个实体添加 confidence 字段
            block_confidence = reasoning_output.get("confidence", 0.0)
            field_confidences = reasoning_output.get("field_confidences", {})
            for entity in sanitized.get("entities", []):
                if "confidence" not in entity:
                    # 尝试从 field_confidences 获取字段级置信度
                    entity_span = entity.get("span", "")
                    entity_confidence = block_confidence  # 默认使用块级置信度
                    
                    # 查找匹配的字段级置信度
                    for field_name, field_conf in field_confidences.items():
                        if entity_span and field_name in entity_span or entity_span in str(field_name):
                            entity_confidence = max(entity_confidence, field_conf)
                            break
                    
                    entity["confidence"] = round(entity_confidence, 4)

            # ====== Phase 4.5: SFT 安全门（必须在 Stateful Context 之前） ======
            pre = symbolic_output["pre_extracted"]
            pre_has_data = (
                len(pre.get("skills", [])) > 0
                or len(pre.get("dates", [])) > 0
                or len(pre.get("roles", [])) > 0
                or len(pre.get("degrees", [])) > 0
            )
            llm_empty = len(sanitized.get("entities", [])) == 0

            if llm_empty and pre_has_data:
                # LLM 交了白卷但 Phase 2 有数据 → 用 pre_extracted 兜底填充
                fallback_entities = []
                # 修复：添加继承的项目/公司上下文到兜底实体
                if state_context.get("PROJECT_NAME"):
                    fallback_entities.append({
                        **state_context["PROJECT_NAME"],
                        "_inherited": True
                    })
                if state_context.get("COMPANY"):
                    fallback_entities.append({
                        **state_context["COMPANY"],
                        "_inherited": True
                    })
                if state_context.get("SCHOOL"):
                    fallback_entities.append({
                        **state_context["SCHOOL"],
                        "_inherited": True
                    })

                for sk in pre.get("skills", []):
                    fallback_entities.append({"type": "SKILL", "span": sk})
                for dt in pre.get("dates", []):
                    fallback_entities.append({"type": "DATE_RANGE", "span": dt})
                for rl in pre.get("roles", []):
                    fallback_entities.append({"type": "PROJECT_ROLE", "span": rl})
                for dg in pre.get("degrees", []):
                    fallback_entities.append({"type": "DEGREE", "span": dg})
                sanitized["entities"] = fallback_entities
                # pre_extracted 是确定性数据（AC算法+正则），知识图谱可用，但禁止 SFT
                sanitized["is_valid"] = True
                sanitized["_sft_eligible"] = False
                sanitized["_fallback_source"] = "pre_extracted"
                logger.warning(
                    "  SFT Gate: %s LLM空输出，已用pre_extracted兜底（%d entities），sft_eligible=false",
                    block.block_id, len(fallback_entities),
                )

            # 占位符检测 — 如果 entity span 是模板占位符，强制拦截
            _PLACEHOLDER_WORDS = {"公司名称", "学校名称", "技能名称", "项目", "角色名", "姓名", "职位名称"}
            has_placeholder = any(
                e.get("span", "") in _PLACEHOLDER_WORDS
                for e in sanitized.get("entities", [])
            )
            if has_placeholder:
                sanitized["entities"] = [
                    e for e in sanitized["entities"]
                    if e.get("span", "") not in _PLACEHOLDER_WORDS
                ]
                sanitized["is_valid"] = False
                sanitized["_sft_eligible"] = False
                logger.warning("  SFT Gate: %s 检测到占位符复制，已清除", block.block_id)

            # ====== Phase 4.6: DATE_RANGE 合并后处理 ======
            # 修复：如果有多个 DATE_RANGE 实体，尝试合并为一个带 start/end 的实体
            date_entities = [e for e in sanitized.get("entities", []) if e.get("type") == "DATE_RANGE"]
            if len(date_entities) >= 2:
                # 尝试找到开始日期和结束日期
                start_date = None
                end_date = None
                end_keywords = ["至今", "现在", "present", "Present"]

                for de in date_entities:
                    span = de.get("span", "")
                    if any(kw in span for kw in end_keywords):
                        end_date = de
                    elif re.match(r"\d{4}[/-]\d{1,2}", span):
                        if start_date is None:
                            start_date = de
                        else:
                            # 可能有多个开始日期，取最早的那个
                            if span < start_date.get("span", ""):
                                start_date = de

                # 如果找到成对的开始和结束日期，合并为一个实体
                if start_date and end_date:
                    # 记录要被合并的实体的索引
                    start_idx = next(
                        i for i, e in enumerate(sanitized["entities"]) if e is start_date
                    )
                    end_idx = next(
                        i for i, e in enumerate(sanitized["entities"]) if e is end_date
                    )

                    merged_entity = {
                        "type": "DATE_RANGE",
                        "start": start_date.get("span", ""),
                        "end": end_date.get("span", ""),
                        "confidence": min(start_date.get("confidence", 1.0), end_date.get("confidence", 1.0)),
                    }
                    # 移除原来的两个 DATE_RANGE 实体
                    sanitized["entities"] = [
                        e for e in sanitized["entities"]
                        if e not in (start_date, end_date)
                    ]
                    # 将合并后的实体添加到 start_idx 位置
                    sanitized["entities"].insert(start_idx, merged_entity)

                    # 更新 relations 中的引用
                    merged_idx = start_idx
                    for rel in sanitized.get("relations", []):
                        if rel["head_entity_id"] in (start_idx, end_idx):
                            rel["head_entity_id"] = merged_idx
                        if rel["tail_entity_id"] in (start_idx, end_idx):
                            rel["tail_entity_id"] = merged_idx

                    # 移除重复的 HAS_PERIOD 关系
                    seen = set()
                    unique_relations = []
                    for rel in sanitized.get("relations", []):
                        key = (rel["head_entity_id"], rel["tail_entity_id"], rel["relation_type"])
                        if key not in seen:
                            seen.add(key)
                            unique_relations.append(rel)
                    sanitized["relations"] = unique_relations

                    logger.info(
                        "  DATE_RANGE 合并: %s - %s",
                        merged_entity["start"], merged_entity["end"]
                    )

            # ====== Stateful Context 状态机注入（在 SFT gate 之后） ======
            if doc_type == "resume":
                current_entities = sanitized.get("entities", [])
                current_relations = sanitized.get("relations", [])

                found_company = None
                found_project = None
                found_school = None

                for e in current_entities:
                    if e["type"] == "COMPANY":
                        found_company = e
                    elif e["type"] == "PROJECT_NAME":
                        found_project = e
                    elif e["type"] == "SCHOOL":
                        found_school = e

                # 检测当前块是否是新项目/工作经历的开始
                # 如果有明确的角色（POSITION/PROJECT_ROLE）但没有项目名，
                # 且 heading 或文本中包含项目开始的关键词，说明这是一个独立的新项目
                # 修复：排除描述性 heading（如"主要工作："），这类块应该继承前一个项目的上下文
                is_descriptive_heading = any(
                    kw in (block.heading or "")
                    for kw in ["主要工作", "工作内容", "职责描述", "项目描述", "详细描述"]
                )
                is_new_project_start = (
                    any(e["type"] in ("POSITION", "PROJECT_ROLE") for e in current_entities)
                    and not found_project
                    and not is_descriptive_heading  # 修复：描述性 heading 不触发新项目开始
                    and any(kw in (block.heading or "") + block.text
                            for kw in ["核心开发者", "主要开发", "系统设计", "2025/", "2024/", "2023/"])
                )

                # 如果检测到新项目开始，清除 state_context 中的项目上下文
                if is_new_project_start:
                    state_context["PROJECT_NAME"] = None
                    state_context["COMPANY"] = None

                # 只有当块类型匹配时才更新状态
                if found_company and block.block_type in ("WORK_EXPERIENCE", "PROJECT_EXPERIENCE"):
                    state_context["COMPANY"] = found_company
                    if found_project is None:
                        state_context["PROJECT_NAME"] = None
                if found_project and block.block_type in ("PROJECT_EXPERIENCE", "WORK_EXPERIENCE"):
                    state_context["PROJECT_NAME"] = found_project
                if found_school and block.block_type == "EDUCATION":
                    state_context["SCHOOL"] = found_school

                has_company = any(e["type"] == "COMPANY" for e in current_entities)
                has_project = any(e["type"] == "PROJECT_NAME" for e in current_entities)

                # 只有当块类型匹配时才继承，且不继承与当前上下文语义不一致的项目
                inherited_entity = None
                if block.block_type == "PROJECT_EXPERIENCE":
                    if not has_project and state_context["PROJECT_NAME"]:
                        inherited_entity = state_context["PROJECT_NAME"]
                    elif not has_company and state_context["COMPANY"]:
                        inherited_entity = state_context["COMPANY"]
                elif block.block_type == "WORK_EXPERIENCE":
                    if not has_company and state_context["COMPANY"]:
                        inherited_entity = state_context["COMPANY"]
                    elif not has_project and state_context["PROJECT_NAME"]:
                        # 修复：WORK_EXPERIENCE 不继承 PROJECT_NAME，避免跨项目继承
                        pass

                if inherited_entity:
                    needs_inheritance = any(
                        e["type"] in ("SKILL", "POSITION", "PROJECT_ROLE", "DATE_RANGE", "DESCRIPTION")
                        for e in current_entities
                    )
                    if needs_inheritance:
                        new_ent = inherited_entity.copy()
                        new_ent["_inherited"] = True
                        current_entities.append(new_ent)
                        head_idx = len(current_entities) - 1

                        for i, e in enumerate(current_entities):
                            if i == head_idx:
                                continue
                            if e["type"] == "SKILL":
                                current_relations.append({
                                    "head_entity_id": head_idx,
                                    "tail_entity_id": i,
                                    "relation_type": "HAS_SKILL"
                                })
                            elif e["type"] in ("POSITION", "PROJECT_ROLE"):
                                current_relations.append({
                                    "head_entity_id": head_idx,
                                    "tail_entity_id": i,
                                    "relation_type": "HAS_ROLE"
                                })
                            elif e["type"] == "DATE_RANGE":
                                current_relations.append({
                                    "head_entity_id": head_idx,
                                    "tail_entity_id": i,
                                    "relation_type": "HAS_PERIOD"
                                })

                sanitized["entities"] = current_entities
                sanitized["relations"] = current_relations
            # ============================================================

            result = {
                "block_id": block.block_id,
                "block_type": block.block_type,
                "heading": block.heading,
                "source": block.source,
                "confidence": reasoning_output["confidence"],
                "is_high_uncertainty": reasoning_output.get("is_high_uncertainty", False),
                "original_text": block.text,
                "masked_text": symbolic_output["masked_text"],
                "tagged_text": symbolic_output["tagged_text"],
                "pre_extracted": symbolic_output["pre_extracted"],
                "privacy_masks": symbolic_output["privacy_masks"],
                "ethics_detections": symbolic_output.get("ethics_detections", []),
                "llm_output": sanitized,
                "is_valid": sanitized.get("is_valid", False),
            }

            # 反馈循环分类
            result = self.feedback_loop.process(result)
            results.append(result)

            # 更新前一个块的引用，用于后续块的上下文继承
            previous_block_result = result

            logger.info(
                "  分类: %s | 置信度: %.2f%%",
                result.get("classification", "UNKNOWN"),
                result["confidence"] * 100,
            )

        return results

    def _assemble_output(
        self,
        results: List[Dict[str, Any]],
        all_blocks: List[DocumentBlock],
        coverage: Dict[str, float],
        trace_id: str,
        input_hash: str,
        doc_type: str,
        out_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """组装最终输出"""
        high_conf = [r for r in results if r.get("classification") == "HIGH"]
        low_conf = [r for r in results if r.get("classification") == "LOW"]
        invalid = [r for r in results if r.get("classification") == "INVALID"]

        # 主动学习数据蒸馏
        silver_data = self.active_learner.create_silver_standard(high_conf) if high_conf else []
        sft_eligible = [r for r in results if FeedbackLoop.is_sft_eligible(r)]

        final_result = {
            "schema_version": "nsle_2_0",
            "mode": "INFER",
            "doc_type": doc_type,
            "pipeline_type": "neural_symbolic_logic_enhanced",
            "contract_metadata": {
                "trace_id": trace_id,
                "input_hash": input_hash,
                "run_id": str(uuid.uuid4()),
            },
            "phase1_summary": {
                "total_blocks": len(all_blocks),
                "target_blocks": len(results),
                "block_types": [
                    PhysicalParseLayer.to_dict(b) for b in all_blocks
                ],
                "coverage": coverage,
            },
            "blocks": results,
            "feedback_loop": {
                "high_confidence": len(high_conf),
                "low_confidence": len(low_conf),
                "invalid": len(invalid),
            },
            "active_learning": {
                "silver_standard_count": len(silver_data),
                "sft_eligible_count": len(sft_eligible),
                "ready_for_sft": len(sft_eligible) > 0,
            },
            "metadata": {
                "total_entities": sum(
                    len(r["llm_output"].get("entities", [])) for r in results
                ),
                "avg_confidence": (
                    sum(r["confidence"] for r in results) / max(len(results), 1)
                ),
            },
        }

        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(final_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("结果已保存到: %s", out_path)

        return final_result

    @staticmethod
    def _compute_input_hash(path: Optional[Path]) -> str:
        if path and path.exists():
            content = path.read_bytes()
            return hashlib.sha256(content).hexdigest()[:12]
        return "unknown"


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pipeline = NSLEPipeline()
    result = pipeline.run_resume(
        content_v2_path=DEFAULT_MINERU_BASE / "简历_content_list_v2.json",
        content_v1_path=DEFAULT_MINERU_BASE / "简历_content_list.json",
        middle_json_path=DEFAULT_MINERU_BASE / "简历_middle.json",
    )

    print(f"\n[统计]")
    print(f"  总块数: {result['phase1_summary']['total_blocks']}")
    print(f"  目标块: {result['phase1_summary']['target_blocks']}")
    print(f"  高置信度: {result['feedback_loop']['high_confidence']}")
    print(f"  低置信度: {result['feedback_loop']['low_confidence']}")
    print(f"  无效: {result['feedback_loop']['invalid']}")
    print(f"  平均置信度: {result['metadata']['avg_confidence']:.2%}")


if __name__ == "__main__":
    main()
