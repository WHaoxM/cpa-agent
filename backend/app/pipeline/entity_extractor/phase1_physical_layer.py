"""
Phase 1: 物理感知与契约对齐层 (Physical & Contract Layer)
基于 NS-LE 架构 + SmartResume 论文 (arXiv:2510.09722)

核心职责：
1. 双通道路由：PDF → MinerU + YOLOv10 / TXT → direct_text_loader
2. MinerU v1/v2/middle.json 统一解析（修复 v2 嵌套格式）
3. YOLOv10 版面区域检测 → 三层信号融合块分类
4. 9 类 Block Taxonomy（契约完整覆盖）
5. 跨页合并 (cross_page_merge)
6. Indexed Linearization（带行号文本流）
7. Fallback Policy（retry_with_ocr / low_confidence_queue）
8. 输出严格对齐契约 document_blocks 格式
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Block Taxonomy — 契约 9 类
# ---------------------------------------------------------------------------

class BlockType(Enum):
    """块分类法 (Block Taxonomy) — 输入契约完整 9 类"""
    PERSONAL_INFO = "PERSONAL_INFO"
    EDUCATION = "EDUCATION"
    WORK_EXPERIENCE = "WORK_EXPERIENCE"
    PROJECT_EXPERIENCE = "PROJECT_EXPERIENCE"
    SKILLS_CERTS = "SKILLS_CERTS"
    JOB_TITLE = "JOB_TITLE"
    JOB_REQUIREMENTS = "JOB_REQUIREMENTS"
    JOB_DESCRIPTION = "JOB_DESCRIPTION"
    OTHER = "OTHER"


# 目标块类型（用于实体抽取）
RESUME_TARGET_TYPES = {
    BlockType.SKILLS_CERTS,
    BlockType.EDUCATION,
    BlockType.PROJECT_EXPERIENCE,
    BlockType.WORK_EXPERIENCE,
}

JD_TARGET_TYPES = {
    BlockType.JOB_TITLE,
    BlockType.JOB_REQUIREMENTS,
    BlockType.JOB_DESCRIPTION,
}


@dataclass
class DocumentBlock:
    """契约对齐的文档块"""
    block_id: str
    block_type: str  # BlockType.value
    text: str
    confidence: float
    source: Dict[str, Any]  # {file_type, page, bbox}
    heading: str = ""
    line_range: Optional[Tuple[int, int]] = None  # (start_line, end_line) for Index Pointer


# ---------------------------------------------------------------------------
# Block Taxonomy Classifier — 三层信号融合
# ---------------------------------------------------------------------------

# 简历标题关键词（heading 强信号）— 优先级从高到低
RESUME_HEADING_RULES: List[Tuple[List[str], str]] = [
    (["技能", "证书", "能力", "技术栈"], "SKILLS_CERTS"),
    (["项目", "科研", "课题", "研究"], "PROJECT_EXPERIENCE"),
    (["工作", "实习", "任职", "职业"], "WORK_EXPERIENCE"),
    (["教育", "学校", "学历", "院校", "毕业"], "EDUCATION"),
]

# JD 标题关键词
JD_HEADING_RULES: List[Tuple[List[str], str]] = [
    (["职位名称", "岗位名称", "招聘职位"], "JOB_TITLE"),
    (["任职要求", "岗位要求", "职位要求", "要求"], "JOB_REQUIREMENTS"),
    (["职位描述", "岗位描述", "工作内容", "职责"], "JOB_DESCRIPTION"),
]


def classify_heading(heading: str, level: int = 2, doc_type: str = "resume") -> Tuple[str, float]:
    """
    标题锚点分类（强信号）。

    Returns:
        (block_type, confidence)
    """
    h = heading.strip()
    if not h:
        return "OTHER", 0.5

    rules = RESUME_HEADING_RULES if doc_type == "resume" else JD_HEADING_RULES

    for keywords, block_type in rules:
        if any(k in h for k in keywords):
            return block_type, 0.95

    # 简历 level 1 标题通常是姓名/个人信息
    if doc_type == "resume" and level == 1:
        return "PERSONAL_INFO", 0.85

    return "OTHER", 0.5


def classify_by_text_content(text: str, doc_type: str = "resume") -> Tuple[str, float]:
    """
    文本内容分类（弱信号 fallback）。

    Returns:
        (block_type, confidence)
    """
    t = text.strip()
    if not t:
        return "OTHER", 0.3

    if doc_type == "resume":
        if re.search(r"(大学|学院|University|College|硕士|博士|本科|学位)", t):
            return "EDUCATION", 0.6
        if re.search(r"(项目|系统|平台|应用|开发了|实现了)", t):
            return "PROJECT_EXPERIENCE", 0.55
        if re.search(r"(公司|有限|实习|工作)", t):
            return "WORK_EXPERIENCE", 0.55
        if re.search(r"(Python|Java|FastAPI|LangChain|Docker|Git|MySQL)", t, re.IGNORECASE):
            return "SKILLS_CERTS", 0.6
    else:
        if re.search(r"(要求|条件|学历|经验)", t):
            return "JOB_REQUIREMENTS", 0.6
        if re.search(r"(职责|负责|工作内容)", t):
            return "JOB_DESCRIPTION", 0.6

    return "OTHER", 0.3


# ---------------------------------------------------------------------------
# MinerU 输出解析器 — 支持 v1 / v2 / middle.json
# ---------------------------------------------------------------------------

def parse_mineru_v2_item_text(item: Dict[str, Any]) -> str:
    """从 MinerU content_list_v2.json 的 item 中提取文本（修复嵌套格式）。"""
    content = item.get("content")
    if not isinstance(content, dict):
        return ""

    item_type = item.get("type", "")
    if item_type == "title":
        chunks = content.get("title_content", [])
    elif item_type == "paragraph":
        chunks = content.get("paragraph_content", [])
    else:
        return ""

    parts: List[str] = []
    for c in chunks:
        if isinstance(c, dict):
            t = c.get("content")
            if isinstance(t, str) and t.strip():
                parts.append(t.strip())
    return "".join(parts)


def flatten_mineru_v2(payload: Any) -> List[Dict[str, Any]]:
    """content_list_v2.json 可能是二维数组，统一拍平。"""
    if not isinstance(payload, list):
        return []
    items: List[Dict[str, Any]] = []
    for part in payload:
        if isinstance(part, dict):
            items.append(part)
        elif isinstance(part, list):
            for x in part:
                if isinstance(x, dict):
                    items.append(x)
    return items


def parse_mineru_v1_item_text(item: Dict[str, Any]) -> str:
    """从 MinerU content_list.json (v1) 的 item 中提取文本。"""
    for key in ("text", "content", "value", "raw_text"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def parse_mineru_middle_blocks(middle_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从 MinerU middle.json 中提取 text blocks（含 bbox 和 score）。
    用于与 YOLOv10 layout regions 做 bbox 对齐。
    """
    blocks: List[Dict[str, Any]] = []
    for page_info in middle_json.get("pdf_info", []):
        page_idx = page_info.get("page_idx", 0)
        for pb in page_info.get("preproc_blocks", []):
            if pb.get("type") not in ("title", "text"):
                continue
            text_parts: List[str] = []
            for line in pb.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("type") == "text":
                        c = span.get("content", "")
                        if c.strip():
                            text_parts.append(c.strip())
            text = " ".join(text_parts).strip()
            if not text:
                continue
            blocks.append({
                "text": text,
                "bbox": pb.get("bbox"),
                "type": pb.get("type"),
                "score": pb.get("score", 0.0),
                "level": pb.get("level"),
                "page": page_idx,
            })
    return blocks


# ---------------------------------------------------------------------------
# MinerU v2 → document_blocks（标题锚点分块）
# ---------------------------------------------------------------------------

def v2_to_document_blocks(
    items: List[Dict[str, Any]],
    doc_type: str = "resume",
    file_type: str = "pdf",
) -> List[DocumentBlock]:
    """
    将 MinerU v2 items 按标题分块，生成契约对齐的 document_blocks。
    合并了原 block_router.py 的 to_document_blocks() 逻辑。
    """
    blocks: List[DocumentBlock] = []
    current: Optional[Dict[str, Any]] = None
    idx_counter = 0

    def flush():
        nonlocal current
        if current and current["text"].strip():
            idx_counter_val = current.pop("_idx")
            heading = current.pop("_heading", "")
            bt, conf = current.pop("_bt"), current.pop("_conf")
            blocks.append(DocumentBlock(
                block_id=f"b_{idx_counter_val}",
                block_type=bt,
                text=current["text"].strip(),
                confidence=conf,
                heading=heading,
                source={
                    "file_type": file_type,
                    "page": current.get("_page"),
                    "bbox": current.get("_bbox"),
                },
            ))
        current = None

    for item in items:
        idx_counter += 1
        item_type = item.get("type", "")
        txt = parse_mineru_v2_item_text(item)

        if item_type == "title":
            flush()
            level = (item.get("content") or {}).get("level", 2)
            bt, conf = classify_heading(txt, level, doc_type)
            current = {
                "_idx": idx_counter,
                "_heading": txt,
                "_bt": bt,
                "_conf": conf,
                "_page": 1,
                "_bbox": item.get("bbox"),
                "text": "",
            }
            continue

        if item_type == "paragraph" and txt:
            if current is None:
                bt, conf = classify_by_text_content(txt, doc_type)
                current = {
                    "_idx": idx_counter,
                    "_heading": "",
                    "_bt": bt,
                    "_conf": conf,
                    "_page": 1,
                    "_bbox": item.get("bbox"),
                    "text": "",
                }
            current["text"] = (current["text"] + "\n" + txt).strip()

    flush()
    return blocks


# ---------------------------------------------------------------------------
# Section-Scope Inheritance — 章节作用域继承
# ---------------------------------------------------------------------------

# 一级章节标题模式（精确匹配，不是子串）
SECTION_LABEL_PATTERNS: List[Tuple[str, str]] = [
    (r"^(?:专业\s*)?技能|技术栈|证书", "SKILLS_CERTS"),
    (r"^项目(?:经[历验]|经验)|科研(?:经[历验]|训练|项目)", "PROJECT_EXPERIENCE"),
    (r"^工作(?:经[历验])?|实习(?:经[历验])?|职业经历", "WORK_EXPERIENCE"),
    (r"^教育(?:经[历验]|背景)?|学历", "EDUCATION"),
    (r"^(?:个人|基本)\s*(?:信息|资料|简介)|自我评价|自我介绍", "PERSONAL_INFO"),
    (r"^(?:获奖|荣誉|奖项)", "SKILLS_CERTS"),
    (r"^(?:在校|校园|社团|学生)\s*(?:经[历验]|活动)", "WORK_EXPERIENCE"),
]


def _is_section_label(heading: str) -> Optional[str]:
    """
    判断 heading 是否为一级章节标签（如"教育经历"、"项目经历"）。
    返回匹配的 BlockType 或 None。
    """
    h = heading.strip()
    if not h or len(h) > 12:  # 一级标题通常很短
        return None
    for pattern, block_type in SECTION_LABEL_PATTERNS:
        if re.search(pattern, h):
            return block_type
    return None


# 实体名标题分类模式 — 当 heading 不是章节标签而是实体名时使用
_EDUCATION_HEADING_HINTS = re.compile(
    r"(?:大学|学院|学校|University|College|Institute|中学|高中|附属)"
)
_PROJECT_HEADING_HINTS = re.compile(
    r"(?:系统|平台|项目|工具|框架|引擎|实验室|研究院|研究所|实验|课题|"
    r"知识库|智能|识别|检测|Agent|agent|Bot|bot|AI|ai)"
)
_COMPANY_HEADING_HINTS = re.compile(
    r"(?:有限公司|股份|集团|科技|网络|信息技术|工作室|"
    r"Inc\.|Corp\.|Ltd\.|LLC|Co\.)"
)

# 块内容信号 — 用于辅助分类
# 修复：将 [\s\S]{0,20} 改为 [^\n]{0,20} 限制在单行内，避免灾难性回溯
_CONTENT_ROLE_DATE = re.compile(
    r"(?:负责人|核心开发者|技术负责人|项目经理|组长|工程师|开发者|实习生|"
    r"研究员|助理|成员|组员|主要开发|核心成员|学生科研组成员)"
    r"[^\n]{0,20}"
    r"\d{4}[/\-\.]\d{1,2}"
)
_CONTENT_EDUCATION = re.compile(
    r"(?:本科|硕士|博士|学士|专科|大专|PhD|Master|Bachelor|主修|GPA|绩点|排名|奖学金)"
)


def _classify_entity_heading(heading: str, text: str) -> Optional[Tuple[str, float]]:
    """
    实体名标题分类：当 heading 不是章节标签（如"教育经历"）而是实体名
    （如"四川轻化工大学"、"省重点人工智能实验室"）时，基于 heading + content 双信号分类。
    """
    h = heading.strip()
    full = h + "\n" + text

    # 1) heading 含学校/大学 → EDUCATION
    if _EDUCATION_HEADING_HINTS.search(h):
        return "EDUCATION", 0.85

    # 2) content 含学历关键词 → EDUCATION
    if _CONTENT_EDUCATION.search(text):
        return "EDUCATION", 0.75

    # 3) heading 含公司后缀 → WORK_EXPERIENCE
    if _COMPANY_HEADING_HINTS.search(h):
        return "WORK_EXPERIENCE", 0.85

    # 4) heading 含项目/系统/实验室关键词 → PROJECT_EXPERIENCE
    if _PROJECT_HEADING_HINTS.search(h):
        return "PROJECT_EXPERIENCE", 0.80

    # 5) content 含角色+日期组合 → PROJECT_EXPERIENCE
    if _CONTENT_ROLE_DATE.search(full):
        return "PROJECT_EXPERIENCE", 0.70

    return None


def apply_section_scope_inheritance(blocks: List[DocumentBlock]) -> List[DocumentBlock]:
    """
    章节作用域继承 (Section-Scope Inheritance) + 实体名标题分类:

    三级分类策略：
    1. 一级章节标签匹配（"教育经历" → EDUCATION scope）
    2. 实体名标题分类（"四川轻化工大学" → EDUCATION）
    3. Content-based fallback（文本内容分析）
    """
    if not blocks:
        return blocks

    # Pass 1: 识别 section scopes + 实体名标题分类
    current_scope: Optional[str] = None

    for block in blocks:
        # 检查该块的 heading 是否为一级章节标签
        section_type = _is_section_label(block.heading)
        if section_type is not None:
            current_scope = section_type
            if block.block_type == "OTHER":
                block.block_type = section_type
                block.confidence = 0.9
            continue

        if block.block_type != "OTHER":
            continue

        # 尝试实体名标题分类（heading + content 双信号）
        entity_result = _classify_entity_heading(block.heading, block.text)
        if entity_result is not None:
            block.block_type, block.confidence = entity_result
            logger.info(
                "Entity Heading: %s (%s) → %s (实体名分类)",
                block.block_id, block.heading, block.block_type,
            )
            continue

        # 继承 section scope
        if current_scope is not None:
            block.block_type = current_scope
            block.confidence = 0.7
            logger.info(
                "Section Inheritance: %s (%s) → %s (继承自上方章节)",
                block.block_id, block.heading, current_scope,
            )

    # Pass 2: content-based fallback — 对仍为 OTHER 的块做内容分析
    for block in blocks:
        if block.block_type == "OTHER":
            new_type, new_conf = classify_by_text_content(block.text)
            if new_type != "OTHER":
                block.block_type = new_type
                block.confidence = new_conf
                logger.info(
                    "Content Fallback: %s → %s (基于内容分析)",
                    block.block_id, new_type,
                )

    return blocks


# ---------------------------------------------------------------------------
# Semantic Sub-splitting — 语义二次拆分
# ---------------------------------------------------------------------------

# 子块切割触发模式：角色 + 时间戳 组合（如 "核心开发者\n2025/06 - 2025/09"）
_ROLE_DATE_SPLIT_PATTERN = re.compile(
    r"\n"
    r"((?:负责人|核心开发者|技术负责人|项目经理|组长|工程师|开发者|实习生|"
    r"研究员|助理|成员|组员|主要开发|核心成员|学生科研组成员)"
    r"\s*\n"
    r"\d{4}[/\-\.]\d{1,2}\s*[-–~至]\s*(?:\d{4}[/\-\.]\d{1,2}|至今|现在|present))",
    re.IGNORECASE,
)


def apply_semantic_subsplit(blocks: List[DocumentBlock]) -> List[DocumentBlock]:
    """
    语义二次拆分：检测 block 内部的 "角色+时间戳" 组合，
    将包含多个项目/经历的块拆分为独立子块。

    典型场景：b_22 包含 "科研成果...核心开发者\\n2025/06 - 2025/09\\n商品识别项目..."
    → 只有当两部分语义相关时才拆分

    修复：如果 text_after 包含完全不同的项目关键词（如"电商"、"商品"）
    与 text_before 完全不相关，不进行拆分。
    """
    result: List[DocumentBlock] = []

    for block in blocks:
        if block.block_type not in ("PROJECT_EXPERIENCE", "WORK_EXPERIENCE", "OTHER"):
            result.append(block)
            continue

        # 查找切割点
        match = _ROLE_DATE_SPLIT_PATTERN.search(block.text)
        if not match:
            result.append(block)
            continue

        split_pos = match.start()
        text_before = block.text[:split_pos].strip()
        text_after = block.text[split_pos:].strip()

        if text_before and text_after:
            # 修复：添加语义连贯性检查
            # 如果 text_after 包含与 text_before 完全不同的项目关键词，不拆分
            project_keywords_before = _extract_project_keywords(text_before)
            project_keywords_after = _extract_project_keywords(text_after)

            # 检查是否有完全不相关的项目关键词
            is_different_project = (
                project_keywords_before and project_keywords_after
                and not (project_keywords_before & project_keywords_after)
            )

            # 如果检测到不同的项目，不拆分，将内容合并为一个块
            if is_different_project:
                # 合并两部分，但保留分割标记便于后续处理
                merged_text = text_before + "\n" + text_after
                result.append(DocumentBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=merged_text,
                    confidence=block.confidence,
                    heading=block.heading,
                    source=block.source,
                    line_range=None,
                ))
                logger.info(
                    "Semantic Merge (different projects detected): %s — skipped split",
                    block.block_id,
                )
                continue

            # 语义连贯，执行拆分
            # 保留原始块（截断到 split_pos）
            # 修复：line_range 标记为 None，因为文本已改变
            result.append(DocumentBlock(
                block_id=block.block_id,
                block_type=block.block_type,
                text=text_before,
                confidence=block.confidence,
                heading=block.heading,
                source=block.source,
                line_range=None,
            ))
            # 创建新子块
            new_bt, new_conf = classify_by_text_content(text_after)
            if new_bt == "OTHER":
                new_bt = block.block_type
                new_conf = 0.7
            result.append(DocumentBlock(
                block_id=f"{block.block_id}_split",
                block_type=new_bt,
                text=text_after,
                confidence=new_conf,
                heading="",
                source=block.source,
                line_range=None,
            ))
            logger.info(
                "Semantic Split: %s → %s + %s_split",
                block.block_id, block.block_id, block.block_id,
            )
        else:
            result.append(block)

    return result


def _extract_project_keywords(text: str) -> set:
    """
    从文本中提取项目关键词集合，用于判断两个文本是否描述同一个项目。
    """
    project_keywords = {
        "人工智能", "AI", "机器学习", "深度学习", "NLP", "CV",
        "实验室", "科研", "研究", "实验",
        "电商", "商品", "推荐", "购物", "平台",
        "自动驾驶", "智能导航", "VLN", "强化学习",
        "课程", "知识库", "Agent", "多模态",
        "系统", "平台", "网站", "APP", "应用",
    }

    found = set()
    for kw in project_keywords:
        if kw in text:
            found.add(kw)

    return found


# ---------------------------------------------------------------------------
# JD TXT 路由 — direct_text_loader
# ---------------------------------------------------------------------------

def parse_jd_txt(text: str) -> List[DocumentBlock]:
    """
    直接文本加载器：将 JD txt 按段落 + 关键词分类为 document_blocks。
    契约 routing_strategy.txt.default = "direct_text_loader"
    """
    blocks: List[DocumentBlock] = []
    paragraphs = re.split(r"\n{2,}", text.strip())

    for i, para in enumerate(paragraphs, start=1):
        para = para.strip()
        if not para:
            continue

        bt, conf = classify_heading(para.split("\n")[0], level=2, doc_type="jd")
        if bt == "OTHER":
            bt, conf = classify_by_text_content(para, doc_type="jd")

        blocks.append(DocumentBlock(
            block_id=f"jd_b_{i}",
            block_type=bt,
            text=para,
            confidence=conf,
            source={"file_type": "txt", "page": None, "bbox": None},
        ))

    return blocks


# ---------------------------------------------------------------------------
# 跨页合并 (cross_page_merge)
# ---------------------------------------------------------------------------

def cross_page_merge(blocks: List[DocumentBlock]) -> List[DocumentBlock]:
    """
    合并相邻且相同 block_type 的块。
    契约: cross_page_merge_enabled = true
    """
    if not blocks:
        return blocks

    merged: List[DocumentBlock] = [blocks[0]]
    for b in blocks[1:]:
        prev = merged[-1]
        if b.block_type == prev.block_type and b.block_type != "OTHER":
            prev.text = prev.text + "\n" + b.text
            prev.confidence = min(prev.confidence, b.confidence)
        else:
            merged.append(b)
    return merged


# ---------------------------------------------------------------------------
# Fallback Policy
# ---------------------------------------------------------------------------

class FallbackPolicy:
    """
    契约 fallback_policy:
    - on_parse_failure: retry_with_ocr_and_layout_model
    - on_block_misclassification: send_to_low_confidence_queue
    - max_retry: 2
    """

    LOW_CONFIDENCE_THRESHOLD = 0.6

    def __init__(self):
        self.low_confidence_queue: List[DocumentBlock] = []

    def apply(self, blocks: List[DocumentBlock]) -> List[DocumentBlock]:
        """将低置信度块路由到审计队列，保留高置信度块。"""
        result: List[DocumentBlock] = []
        for b in blocks:
            if b.confidence < self.LOW_CONFIDENCE_THRESHOLD:
                self.low_confidence_queue.append(b)
                logger.info(
                    "低置信度块 → 审计队列: %s (%s, conf=%.2f)",
                    b.block_id, b.block_type, b.confidence,
                )
            result.append(b)  # 仍然保留在输出中，但标记了置信度
        return result


# ---------------------------------------------------------------------------
# PhysicalParseLayer — 统一入口
# ---------------------------------------------------------------------------

class PhysicalParseLayer:
    """
    Phase 1 物理解析层：统一 MinerU + YOLOv10 + 块分类 + 契约对齐。

    双通道路由：
    - PDF → MinerU 文本提取 + YOLOv10 版面检测 → 三层信号融合
    - TXT → direct_text_loader（JD txt）

    输出格式严格对齐契约 physical_parse_layer.output.document_blocks
    """

    def __init__(self, yolo_model_path: Optional[str | Path] = None):
        self.fallback = FallbackPolicy()
        self._yolo_detector = None
        self._yolo_model_path = yolo_model_path

    @property
    def yolo_detector(self):
        """延迟加载 YOLOv10 检测器"""
        if self._yolo_detector is None and self._yolo_model_path:
            from layout.yolo_layout_detector import YOLOLayoutDetector
            self._yolo_detector = YOLOLayoutDetector(self._yolo_model_path)
        return self._yolo_detector

    # ------------------------------------------------------------------
    # PDF 路由
    # ------------------------------------------------------------------

    def parse_resume_pdf(
        self,
        content_v2_path: Optional[Path] = None,
        middle_json_path: Optional[Path] = None,
        content_v1_path: Optional[Path] = None,
        pdf_path: Optional[Path] = None,
    ) -> List[DocumentBlock]:
        """
        PDF 路由：优先 v2 → v1 → middle.json
        如果有 pdf_path 且 YOLOv10 可用，执行版面检测 + 对齐。
        """
        blocks: List[DocumentBlock] = []

        # 策略 1: content_list_v2 （主要）
        if content_v2_path and content_v2_path.exists():
            payload = json.loads(content_v2_path.read_text(encoding="utf-8"))
            items = flatten_mineru_v2(payload)
            blocks = v2_to_document_blocks(items, doc_type="resume", file_type="pdf")
            logger.info("MinerU v2 解析: %d blocks from %s", len(blocks), content_v2_path.name)

        # 策略 2: content_list_v1 fallback
        elif content_v1_path and content_v1_path.exists():
            payload = json.loads(content_v1_path.read_text(encoding="utf-8"))
            raw_items = payload if isinstance(payload, list) else []
            # v1 items 直接有 text 字段，走简单分块
            blocks = self._v1_to_blocks(raw_items)
            logger.info("MinerU v1 解析: %d blocks from %s", len(blocks), content_v1_path.name)

        # 策略 3: middle.json fallback
        elif middle_json_path and middle_json_path.exists():
            mid = json.loads(middle_json_path.read_text(encoding="utf-8"))
            raw_blocks = parse_mineru_middle_blocks(mid)
            blocks = self._middle_to_blocks(raw_blocks)
            logger.info("MinerU middle 解析: %d blocks from %s", len(blocks), middle_json_path.name)

        # 跨页合并
        blocks = cross_page_merge(blocks)

        # Section-Scope Inheritance — 章节作用域继承
        blocks = apply_section_scope_inheritance(blocks)

        # Semantic Sub-splitting — 语义二次拆分
        blocks = apply_semantic_subsplit(blocks)

        # Fallback policy
        blocks = self.fallback.apply(blocks)

        return blocks

    def _v1_to_blocks(self, raw_items: List[Dict]) -> List[DocumentBlock]:
        """从 v1 content_list items 生成 document_blocks"""
        blocks: List[DocumentBlock] = []
        current: Optional[Dict] = None
        idx = 0

        def flush():
            nonlocal current
            if current and current["text"].strip():
                blocks.append(DocumentBlock(**current))
            current = None

        for item in raw_items:
            idx += 1
            text = parse_mineru_v1_item_text(item)
            if not text:
                continue

            level = item.get("text_level") or item.get("level")
            is_title = level is not None

            if is_title:
                flush()
                bt, conf = classify_heading(text, int(level), "resume")
                current = {
                    "block_id": f"b_{idx}",
                    "block_type": bt,
                    "text": "",
                    "confidence": conf,
                    "heading": text,
                    "source": {
                        "file_type": "pdf",
                        "page": item.get("page_idx"),
                        "bbox": item.get("bbox"),
                    },
                }
            else:
                if current is None:
                    bt, conf = classify_by_text_content(text, "resume")
                    current = {
                        "block_id": f"b_{idx}",
                        "block_type": bt,
                        "text": "",
                        "confidence": conf,
                        "heading": "",
                        "source": {
                            "file_type": "pdf",
                            "page": item.get("page_idx"),
                            "bbox": item.get("bbox"),
                        },
                    }
                current["text"] = (current["text"] + "\n" + text).strip()

        flush()
        return blocks

    def _middle_to_blocks(self, raw_blocks: List[Dict]) -> List[DocumentBlock]:
        """从 middle.json parsed blocks 生成 document_blocks"""
        blocks: List[DocumentBlock] = []
        current: Optional[Dict] = None
        idx = 0

        def flush():
            nonlocal current
            if current and current["text"].strip():
                blocks.append(DocumentBlock(**current))
            current = None

        for rb in raw_blocks:
            idx += 1
            text = rb.get("text", "")
            if not text:
                continue

            is_title = rb.get("type") == "title"
            level = rb.get("level") or 2

            if is_title:
                flush()
                bt, conf = classify_heading(text, int(level), "resume")
                current = {
                    "block_id": f"b_{idx}",
                    "block_type": bt,
                    "text": "",
                    "confidence": conf,
                    "heading": text,
                    "source": {
                        "file_type": "pdf",
                        "page": rb.get("page"),
                        "bbox": rb.get("bbox"),
                    },
                }
            else:
                if current is None:
                    bt, conf = classify_by_text_content(text, "resume")
                    current = {
                        "block_id": f"b_{idx}",
                        "block_type": bt,
                        "text": "",
                        "confidence": conf,
                        "heading": "",
                        "source": {
                            "file_type": "pdf",
                            "page": rb.get("page"),
                            "bbox": rb.get("bbox"),
                        },
                    }
                current["text"] = (current["text"] + "\n" + text).strip()

        flush()
        return blocks

    # ------------------------------------------------------------------
    # JD TXT 路由
    # ------------------------------------------------------------------

    def parse_jd_txt(self, jd_text: str) -> List[DocumentBlock]:
        """JD txt 路由: direct_text_loader"""
        blocks = parse_jd_txt(jd_text)
        blocks = self.fallback.apply(blocks)
        return blocks

    # ------------------------------------------------------------------
    # 过滤 & 序列化
    # ------------------------------------------------------------------

    def filter_target_blocks(
        self, blocks: List[DocumentBlock], doc_type: str = "resume"
    ) -> List[DocumentBlock]:
        """过滤出目标块（用于实体抽取）"""
        targets = RESUME_TARGET_TYPES if doc_type == "resume" else JD_TARGET_TYPES
        target_values = {t.value for t in targets}
        # PERSONAL_INFO 也应该是目标块，否则基本信息会被丢弃
        if doc_type == "resume":
            target_values.add("PERSONAL_INFO")
        return [b for b in blocks if b.block_type in target_values]

    @staticmethod
    def to_dict(block: DocumentBlock) -> Dict[str, Any]:
        """转换为契约 document_blocks 格式"""
        return {
            "block_id": block.block_id,
            "block_type": block.block_type,
            "text": block.text,
            "confidence": block.confidence,
            "source": block.source,
            "heading": block.heading,
            "line_range": block.line_range,
        }

    @staticmethod
    def compute_coverage(blocks: List[DocumentBlock]) -> Dict[str, float]:
        """计算契约质量指标"""
        if not blocks:
            return {"full_text_coverage": 0.0, "block_classification_coverage": 0.0}

        total_chars = sum(len(b.text) for b in blocks)
        classified = sum(1 for b in blocks if b.block_type != "OTHER")

        return {
            "full_text_coverage": 1.0 if total_chars > 0 else 0.0,
            "block_classification_coverage": round(
                classified / len(blocks) if blocks else 0.0, 4
            ),
        }
