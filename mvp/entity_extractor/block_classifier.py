"""
Block 分类与分块模块

功能：
1. 按标题分块 (Heading-based Block Grouping)
2. 三级分类策略 (Three-Level Classification)
3. 语义二次拆分 (Semantic Sub-splitting)

这些功能从 phase1_physical_layer.py 中提取，用于独立的测试和验证
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Block Taxonomy — 契约 9 类
# ---------------------------------------------------------------------------

class BlockType(Enum):
    """块分类法 (Block Taxonomy)"""
    PERSONAL_INFO = "PERSONAL_INFO"
    EDUCATION = "EDUCATION"
    WORK_EXPERIENCE = "WORK_EXPERIENCE"
    PROJECT_EXPERIENCE = "PROJECT_EXPERIENCE"
    SKILLS_CERTS = "SKILLS_CERTS"
    JOB_TITLE = "JOB_TITLE"
    JOB_REQUIREMENTS = "JOB_REQUIREMENTS"
    JOB_DESCRIPTION = "JOB_DESCRIPTION"
    OTHER = "OTHER"


@dataclass
class DocumentBlock:
    """契约对齐的文档块"""
    block_id: str
    block_type: str
    text: str
    confidence: float
    source: Dict[str, Any] = field(default_factory=dict)
    heading: str = ""
    line_range: Optional[Tuple[int, int]] = None


# ---------------------------------------------------------------------------
# 第一部分：按标题分块 (Heading-based Block Grouping)
# ---------------------------------------------------------------------------

def parse_mineru_v2_item_text(item: Dict[str, Any]) -> str:
    """从 MinerU v2 item 中提取文本"""
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


def parse_mineru_v1_item_text(item: Dict[str, Any]) -> str:
    """从 MinerU v1 item 中提取文本"""
    for key in ("text", "content", "value", "raw_text"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def v2_to_document_blocks(
    items: List[Dict[str, Any]],
    doc_type: str = "resume",
    file_type: str = "pdf",
) -> List[DocumentBlock]:
    """
    将 MinerU items 按标题分块，生成契约对齐的 document_blocks。

    核心逻辑：
    1. 遇到 title 类型 → 创建新块，以该标题作为块的 heading
    2. 遇到 paragraph 类型 → 合并到当前块的 text 中
    3. 纯文本流（如V1）→ 基于标题规则动态创建块

    Args:
        items: MinerU 解析出的 items 列表
        doc_type: "resume" 或 "jd"
        file_type: "pdf" 或 "txt"

    Returns:
        List[DocumentBlock]: 分块后的文档块列表
    """
    blocks: List[DocumentBlock] = []
    current: Optional[Dict[str, Any]] = None
    idx_counter = 0

    def flush() -> None:
        nonlocal current
        if current and current["text"].strip():
            nonlocal_idx = current.pop("_idx")
            heading = current.pop("_heading", "")
            bt, conf = current.pop("_bt"), current.pop("_conf")
            blocks.append(DocumentBlock(
                block_id=f"b_{nonlocal_idx}",
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

        # 根据格式类型提取文本
        if doc_type == "resume" and item_type in ("title", "paragraph", "text"):
            # V2 格式
            txt = parse_mineru_v2_item_text(item)
            if not txt:
                txt = item.get("text", "")
        elif doc_type == "resume":
            # V1 格式（纯文本流）
            txt = parse_mineru_v1_item_text(item)
        elif item_type in ("title", "paragraph"):
            txt = parse_mineru_v2_item_text(item)
        else:
            txt = parse_mineru_v1_item_text(item)

        if item_type == "title" or (doc_type == "resume" and item.get("text_level") == 2):
            # 标题类型 → 创建新块
            flush()
            level = (item.get("content") or {}).get("level", 2) if item_type == "title" else item.get("text_level", 2)
            bt, conf = classify_heading(txt, level, doc_type)
            current = {
                "_idx": idx_counter,
                "_heading": txt,
                "_bt": bt,
                "_conf": conf,
                "_page": item.get("page_idx", 1),
                "_bbox": item.get("bbox"),
                "text": "",
            }
            continue

        # 内容类型 → 合并到当前块
        if txt:
            if current is None:
                # 没有当前块（V1格式的第一个段落）
                bt, conf = classify_by_text_content(txt, doc_type)
                current = {
                    "_idx": idx_counter,
                    "_heading": "",
                    "_bt": bt,
                    "_conf": conf,
                    "_page": item.get("page_idx", 1),
                    "_bbox": item.get("bbox"),
                    "text": "",
                }
            current["text"] = (current["text"] + "\n" + txt).strip()

    flush()
    return blocks


# ---------------------------------------------------------------------------
# 第二部分：三级分类策略 (Three-Level Classification)
# ---------------------------------------------------------------------------

# 简历标题关键词（heading 强信号）
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
    标题锚点分类（第一级：强信号）

    Args:
        heading: 标题文本
        level: 标题级别 (1=大标题, 2=小标题)
        doc_type: "resume" 或 "jd"

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
    文本内容分类（第四级：Content fallback）

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


# 一级章节标题模式（用于 Section Label 匹配）
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
    判断 heading 是否为一级章节标签

    Returns:
        匹配的 BlockType 或 None
    """
    h = heading.strip()
    if not h or len(h) > 12:
        return None
    for pattern, block_type in SECTION_LABEL_PATTERNS:
        if re.search(pattern, h):
            return block_type
    return None


# 实体名标题分类模式
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

# 块内容信号
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
    实体名标题分类（第二级）

    当 heading 不是章节标签（如"教育经历"）而是实体名
    （如"四川轻化工大学"、"省重点人工智能实验室"）时使用

    Returns:
        (block_type, confidence) 或 None
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
    三级分类策略：

    1. Pass 1: 一级章节标签匹配（"教育经历" → EDUCATION scope）
    2. Pass 2: 实体名标题分类（"四川轻化工大学" → EDUCATION）
    3. Pass 3: Section 继承（继承上方章节类型）
    4. Pass 4: Content fallback（文本内容分析）

    Args:
        blocks: 分块后的文档块列表

    Returns:
        分类增强后的文档块列表
    """
    if not blocks:
        return blocks

    # Pass 1 & 2: 识别 section scopes + 实体名标题分类
    current_scope: Optional[str] = None

    for block in blocks:
        # 检查该块的 heading 是否为一级章节标签
        section_type = _is_section_label(block.heading)
        if section_type is not None:
            current_scope = section_type
            if block.block_type == "OTHER":
                block.block_type = section_type
                block.confidence = 0.9
            logger.info(
                "Section Label: %s (%s) → %s",
                block.block_id, block.heading, section_type,
            )
            continue

        if block.block_type != "OTHER":
            continue

        # 尝试实体名标题分类（heading + content 双信号）
        entity_result = _classify_entity_heading(block.heading, block.text)
        if entity_result is not None:
            block.block_type, block.confidence = entity_result
            logger.info(
                "Entity Heading: %s (%s) → %s",
                block.block_id, block.heading, block.block_type,
            )
            continue

        # 继承 section scope
        if current_scope is not None:
            block.block_type = current_scope
            block.confidence = 0.7
            logger.info(
                "Section Inheritance: %s → %s (继承自上方章节)",
                block.block_id, current_scope,
            )

    # Pass 4: content-based fallback — 对仍为 OTHER 的块做内容分析
    for block in blocks:
        if block.block_type == "OTHER":
            new_type, new_conf = classify_by_text_content(block.text)
            if new_type != "OTHER":
                block.block_type = new_type
                block.confidence = new_conf
                logger.info(
                    "Content Fallback: %s → %s",
                    block.block_id, new_type,
                )

    return blocks


# ---------------------------------------------------------------------------
# 第三部分：语义二次拆分 (Semantic Sub-splitting)
# ---------------------------------------------------------------------------

# 子块切割触发模式
_ROLE_DATE_SPLIT_PATTERN = re.compile(
    r"\n"
    r"((?:负责人|核心开发者|技术负责人|项目经理|组长|工程师|开发者|实习生|"
    r"研究员|助理|成员|组员|主要开发|核心成员|学生科研组成员)"
    r"\s*\n"
    r"\d{4}[/\-\.]\d{1,2}\s*[-–~至]\s*(?:\d{4}[/\-\.]\d{1,2}|至今|现在|present))",
    re.IGNORECASE,
)

# 项目关键词集合
_PROJECT_KEYWORDS = {
    "人工智能", "AI", "机器学习", "深度学习", "NLP", "CV",
    "实验室", "科研", "研究", "实验",
    "电商", "商品", "推荐", "购物", "平台",
    "自动驾驶", "智能导航", "VLN", "强化学习",
    "课程", "知识库", "Agent", "多模态",
    "系统", "平台", "网站", "APP", "应用",
}


def _extract_project_keywords(text: str) -> Set[str]:
    """从文本中提取项目关键词集合"""
    found = set()
    for kw in _PROJECT_KEYWORDS:
        if kw in text:
            found.add(kw)
    return found


def apply_semantic_subsplit(blocks: List[DocumentBlock]) -> List[DocumentBlock]:
    """
    语义二次拆分：检测 block 内部的 "角色+时间戳" 组合，
    将包含多个项目/经历的块拆分为独立子块。

    只有当两部分语义相关时才拆分

    Args:
        blocks: 分类后的文档块列表

    Returns:
        拆分后的文档块列表
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

        if not text_before or not text_after:
            result.append(block)
            continue

        # 语义连贯性检查
        project_keywords_before = _extract_project_keywords(text_before)
        project_keywords_after = _extract_project_keywords(text_after)

        # 检测是否是完全不同的项目
        is_different_project = (
            project_keywords_before and project_keywords_after
            and not (project_keywords_before & project_keywords_after)
        )

        if is_different_project:
            # 不同项目，不拆分，合并为一个块
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
                "Semantic Merge (different projects): %s — skipped split",
                block.block_id,
            )
            continue

        # 语义连贯，执行拆分
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

    return result


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def process_blocks(
    items: List[Dict[str, Any]],
    doc_type: str = "resume",
    file_type: str = "pdf",
) -> List[DocumentBlock]:
    """
    统一的块处理入口

    完整流程：
    1. 按标题分块 (v2_to_document_blocks)
    2. 三级分类策略 (apply_section_scope_inheritance)
    3. 语义二次拆分 (apply_semantic_subsplit)

    Args:
        items: MinerU 解析出的 items 列表
        doc_type: "resume" 或 "jd"
        file_type: "pdf" 或 "txt"

    Returns:
        处理完成后的文档块列表
    """
    # Step 1: 按标题分块
    blocks = v2_to_document_blocks(items, doc_type, file_type)
    logger.info(f"Step 1 - 分块完成: {len(blocks)} blocks")

    # Step 2: 三级分类
    blocks = apply_section_scope_inheritance(blocks)
    logger.info(f"Step 2 - 分类完成: {len(blocks)} blocks")

    # Step 3: 语义拆分
    blocks = apply_semantic_subsplit(blocks)
    logger.info(f"Step 3 - 拆分完成: {len(blocks)} blocks")

    return blocks


def demo():
    """演示三种功能"""
    from pathlib import Path
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from mineru_caller import MinerUCaller

    # 加载已有的解析结果
    base_dir = Path(__file__).resolve().parents[2] / "data" / "mineru_output" / "\u7b80\u5386" / "auto"
    caller = MinerUCaller()
    result = caller.load_existing(base_dir)

    if not result.has_data():
        print("❌ 无法加载解析结果")
        return

    print(f"✅ 加载 {result.format_used} 格式，共 {len(result.blocks)} blocks")

    # 演示处理流程
    print("\n" + "=" * 80)
    print("演示：完整的块处理流程")
    print("=" * 80)

    # Step 1: 分块
    print("\n--- Step 1: 按标题分块 ---")
    blocks = v2_to_document_blocks(result.blocks, doc_type="resume")
    print(f"分块结果: {len(blocks)} blocks")
    for i, block in enumerate(blocks[:5]):
        print(f"\n[{i}] {block.block_type} (conf={block.confidence:.2f})")
        print(f"    Heading: {block.heading or '(无)'}")
        print(f"    Text: {block.text[:60]}...")

    # Step 2: 分类
    print("\n--- Step 2: 三级分类 ---")
    blocks = apply_section_scope_inheritance(blocks)
    print("分类完成")
    for i, block in enumerate(blocks[:5]):
        print(f"  [{i}] {block.block_id}: {block.block_type} (conf={block.confidence:.2f})")

    # Step 3: 语义拆分
    print("\n--- Step 3: 语义二次拆分 ---")
    original_count = len(blocks)
    blocks = apply_semantic_subsplit(blocks)
    print(f"拆分: {original_count} → {len(blocks)} blocks")

    # 最终结果
    print("\n" + "=" * 80)
    print("最终结果")
    print("=" * 80)
    for block in blocks:
        print(f"\n{block.block_id}: {block.block_type}")
        print(f"  Heading: {block.heading or '(无)'}")
        print(f"  Text: {block.text[:80]}...")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    demo()