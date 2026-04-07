"""
Input Router — 统一输入路由器
所有数据输入的第一道关卡，判断格式并分流到正确的处理通道。

两类通道：
1. 非结构化通道 (PDF/Word/Image/TXT) → Phase 1 物理解析 → document_blocks → Phase 2-4
2. 结构化通道 (JSON表单/Excel/JSON数据) → 字段映射 → 直接输出 Schema (跳过 Phase 1-4)

设计原则：
- YOLOv10 作为统一视觉骨干 (PDF/Word/Image 都可走版面检测)
- 文本提取按格式选最优方法 (MinerU/python-docx/OCR)
- 结构化输入绕过物理解析层，直接映射到冻结 Schema
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保本目录在 sys.path，使 layout/ 子包可导入
_self_dir = str(Path(__file__).resolve().parent)
if _self_dir not in sys.path:
    sys.path.insert(0, _self_dir)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举 & 数据结构
# ---------------------------------------------------------------------------

class InputFormat(Enum):
    """检测到的文件格式"""
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"           # jpg/png/bmp/tiff
    TXT = "txt"               # 纯文本 (复制粘贴)
    JSON = "json"             # 结构化 JSON
    EXCEL = "excel"           # xlsx/xls/csv
    FORM = "form"             # 前端表单提交 (dict)
    UNKNOWN = "unknown"


class DocType(Enum):
    """文档业务类型"""
    RESUME = "resume"
    JD = "jd"


class ChannelType(Enum):
    """处理通道"""
    PHYSICAL_PARSE = "physical_parse"   # 非结构化 → Phase 1-4
    STRUCTURED_BYPASS = "structured_bypass"  # 结构化 → 跳过 Phase 1-4


@dataclass
class RouteResult:
    """路由结果 — 统一输出"""
    channel: ChannelType
    doc_type: str                                  # "resume" / "jd"
    input_format: InputFormat
    blocks: Optional[List] = None                  # 非结构化通道: List[DocumentBlock]
    structured_data: Optional[Dict[str, Any]] = None  # 结构化通道: 直接映射的 Schema
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_structured(self) -> bool:
        return self.channel == ChannelType.STRUCTURED_BYPASS


# 支持的图片扩展名
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

# 支持的 Excel 扩展名
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".csv"}


# ---------------------------------------------------------------------------
# 格式检测
# ---------------------------------------------------------------------------

def detect_format(file_path: Path) -> InputFormat:
    """根据文件扩展名检测输入格式"""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return InputFormat.PDF
    if suffix in (".docx", ".doc"):
        return InputFormat.DOCX
    if suffix in IMAGE_EXTENSIONS:
        return InputFormat.IMAGE
    if suffix == ".txt":
        return InputFormat.TXT
    if suffix == ".json":
        return InputFormat.JSON
    if suffix in EXCEL_EXTENSIONS:
        return InputFormat.EXCEL
    return InputFormat.UNKNOWN


def detect_format_from_mime(mime_type: str) -> InputFormat:
    """根据 MIME 类型检测输入格式 (用于 Web 上传)"""
    mime_map = {
        "application/pdf": InputFormat.PDF,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": InputFormat.DOCX,
        "application/msword": InputFormat.DOCX,
        "text/plain": InputFormat.TXT,
        "application/json": InputFormat.JSON,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": InputFormat.EXCEL,
        "application/vnd.ms-excel": InputFormat.EXCEL,
        "text/csv": InputFormat.EXCEL,
    }
    # 图片
    if mime_type.startswith("image/"):
        return InputFormat.IMAGE
    return mime_map.get(mime_type, InputFormat.UNKNOWN)


# ---------------------------------------------------------------------------
# Word (.docx) 文本提取 — 保留段落结构
# ---------------------------------------------------------------------------

def extract_docx_paragraphs(file_path: Path) -> List[Dict[str, Any]]:
    """
    从 .docx 文件提取段落列表，保留标题层级信息。

    Returns:
        [{"text": "...", "is_heading": bool, "heading_level": int, "style": "..."}, ...]
    """
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError(
            "需要 python-docx 来解析 Word 文件: pip install python-docx"
        )

    doc = Document(str(file_path))
    paragraphs = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style_name = (para.style.name or "").lower()
        is_heading = style_name.startswith("heading")
        heading_level = 2  # 默认

        if is_heading:
            # "Heading 1" → level 1, "Heading 2" → level 2
            try:
                heading_level = int(style_name.split()[-1])
            except (ValueError, IndexError):
                heading_level = 2

        paragraphs.append({
            "text": text,
            "is_heading": is_heading,
            "heading_level": heading_level,
            "style": para.style.name,
        })

    logger.info("Word 提取: %d 段落 from %s", len(paragraphs), file_path.name)
    return paragraphs


def docx_paragraphs_to_blocks(
    paragraphs: List[Dict[str, Any]],
    doc_type: str = "resume",
) -> list:
    """
    将 Word 段落转为 document_blocks，复用 Phase 1 的块分类逻辑。
    """
    try:
        from .phase1_physical_layer import (
            DocumentBlock,
            classify_heading,
            classify_by_text_content,
            apply_section_scope_inheritance,
            apply_semantic_subsplit,
            cross_page_merge,
        )
    except ImportError:
        from phase1_physical_layer import (
            DocumentBlock,
            classify_heading,
            classify_by_text_content,
            apply_section_scope_inheritance,
            apply_semantic_subsplit,
            cross_page_merge,
        )

    blocks: List[DocumentBlock] = []
    current = None
    idx = 0

    def flush():
        nonlocal current
        if current and current["text"].strip():
            blocks.append(DocumentBlock(
                block_id=f"b_{current['idx']}",
                block_type=current["bt"],
                text=current["text"].strip(),
                confidence=current["conf"],
                heading=current["heading"],
                source={"file_type": "docx", "page": None, "bbox": None},
            ))
        current = None

    for para in paragraphs:
        idx += 1
        text = para["text"]

        if para["is_heading"]:
            flush()
            bt, conf = classify_heading(text, para["heading_level"], doc_type)
            current = {
                "idx": idx, "heading": text,
                "bt": bt, "conf": conf, "text": "",
            }
        else:
            if current is None:
                bt, conf = classify_by_text_content(text, doc_type)
                current = {
                    "idx": idx, "heading": "",
                    "bt": bt, "conf": conf, "text": "",
                }
            current["text"] = (current["text"] + "\n" + text).strip()

    flush()

    # 复用 Phase 1 的后处理流水线
    blocks = cross_page_merge(blocks)
    blocks = apply_section_scope_inheritance(blocks)
    blocks = apply_semantic_subsplit(blocks)

    return blocks


# ---------------------------------------------------------------------------
# JD 竞赛 JSON 字段映射 (结构化通道)
# ---------------------------------------------------------------------------

# 竞赛 JD JSON 字段 → job_posting Schema 映射
_JD_FIELD_MAP = {
    "岗位名称": "position_name",
    "地址": "location",
    "薪资范围": "salary_range_raw",
    "公司名称": "company_name",
    "所属行业": "industry",
    "公司规模": "company_scale",
    "公司类型": "company_type",
    "岗位编码": "job_id",
    "岗位详情": "raw_description",
    "更新日期": "update_date",
    "公司详情": "company_description",
    "岗位来源地址": "source_url",
}

# IT 行业关键词 (用于 R-01 过滤)
_IT_INDUSTRY_KEYWORDS = {
    "计算机", "互联网", "IT", "软件", "信息技术", "通信", "电子", "网络",
    "人工智能", "大数据", "云计算", "区块链", "游戏", "数据",
}

# IT 岗位关键词
_IT_POSITION_KEYWORDS = {
    "开发", "工程师", "程序员", "架构师", "运维", "测试", "前端", "后端",
    "全栈", "算法", "数据", "AI", "机器学习", "深度学习", "NLP", "CV",
    "产品经理", "项目经理", "技术", "DBA", "安全", "嵌入式", "实施",
}


def _clean_html(text: str) -> str:
    """清理 JD 详情中的 HTML 标签"""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_it_position(record: Dict[str, str]) -> bool:
    """判断是否为 IT 相关岗位 (R-01)"""
    industry = record.get("所属行业") or ""
    position = record.get("岗位名称") or ""

    industry_match = any(kw in industry for kw in _IT_INDUSTRY_KEYWORDS)
    position_match = any(kw in position for kw in _IT_POSITION_KEYWORDS)

    return industry_match or position_match


def map_jd_record(record: Dict[str, str]) -> Dict[str, Any]:
    """将单条竞赛 JD JSON 记录映射到 job_posting Schema"""
    mapped = {}
    for cn_key, en_key in _JD_FIELD_MAP.items():
        mapped[en_key] = record.get(cn_key, "")

    # 清理 HTML
    if mapped.get("raw_description"):
        mapped["raw_description_clean"] = _clean_html(mapped["raw_description"])

    # 解析薪资范围
    salary_raw = mapped.get("salary_range_raw", "")
    mapped["salary_range"] = _parse_salary(salary_raw)

    # IT 岗位标记 (R-01)
    mapped["is_it_position"] = _is_it_position(record)

    return mapped


def _parse_salary(salary_str: str) -> Dict[str, Any]:
    """解析薪资字符串 → {min, max, unit, raw}"""
    result = {"raw": salary_str, "min": None, "max": None, "unit": "元/月"}

    # 匹配 "3000-4000元" 或 "15-25K" 或 "120-150元/天"
    m = re.search(r"(\d+\.?\d*)\s*[-~至]\s*(\d+\.?\d*)\s*(.*)", salary_str)
    if m:
        result["min"] = float(m.group(1))
        result["max"] = float(m.group(2))
        unit_str = m.group(3).strip()
        if "天" in unit_str or "日" in unit_str:
            result["unit"] = "元/天"
        elif "K" in salary_str.upper() or "k" in salary_str:
            result["min"] *= 1000
            result["max"] *= 1000
            result["unit"] = "元/月"
        elif "万" in unit_str:
            result["min"] *= 10000
            result["max"] *= 10000
            result["unit"] = "元/年"

    return result


def map_jd_json_batch(
    json_path: Path,
    filter_it: bool = True,
) -> List[Dict[str, Any]]:
    """
    批量映射竞赛 JD JSON 文件。

    Args:
        json_path: jd_data.json 路径
        filter_it: 是否过滤非 IT 岗位 (R-01)

    Returns:
        映射后的 job_posting 列表
    """
    raw_records = json.loads(json_path.read_text(encoding="utf-8"))
    results = []
    filtered_count = 0

    for record in raw_records:
        mapped = map_jd_record(record)
        if filter_it and not mapped["is_it_position"]:
            filtered_count += 1
            continue
        results.append(mapped)

    logger.info(
        "JD 映射: %d 条 → %d IT 岗位 (过滤 %d 条非 IT)",
        len(raw_records), len(results), filtered_count,
    )
    return results


# ---------------------------------------------------------------------------
# 图片加载 & OCR 引擎
# ---------------------------------------------------------------------------

def load_image(file_path: Path) -> "np.ndarray":
    """加载图片为 RGB numpy array"""
    import numpy as np
    try:
        from PIL import Image
        img = Image.open(file_path).convert("RGB")
        return np.array(img)
    except ImportError:
        raise RuntimeError("需要 Pillow 来加载图片: pip install Pillow")


class _OCRBase:
    """OCR 引擎抽象基类"""
    def ocr_region(self, image: "np.ndarray", bbox=None) -> str:
        raise NotImplementedError

    def ocr_full(self, image: "np.ndarray") -> List[Dict[str, Any]]:
        """全图 OCR, 返回 [{text, bbox:(x1,y1,x2,y2), confidence}]"""
        raise NotImplementedError


class _PaddleOCREngine(_OCRBase):
    """PaddleOCR 引擎 (中英文最佳)"""
    def __init__(self):
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)

    def ocr_region(self, image, bbox=None):
        import numpy as np
        if bbox:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                return ""
        else:
            crop = image
        result = self._ocr.ocr(crop, cls=True)
        if not result or not result[0]:
            return ""
        lines = []
        for line in result[0]:
            if line and len(line) >= 2:
                lines.append(line[1][0])
        return "\n".join(lines)

    def ocr_full(self, image):
        result = self._ocr.ocr(image, cls=True)
        items = []
        if not result or not result[0]:
            return items
        for line in result[0]:
            if not line or len(line) < 2:
                continue
            pts = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text = line[1][0]
            conf = line[1][1]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            items.append({
                "text": text,
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "confidence": conf,
            })
        return items


class _TesseractOCREngine(_OCRBase):
    """Tesseract OCR 引擎 (fallback)"""
    def __init__(self):
        import pytesseract
        self._tess = pytesseract

    def ocr_region(self, image, bbox=None):
        from PIL import Image
        import numpy as np
        if bbox:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                return ""
        else:
            crop = image
        pil_img = Image.fromarray(crop)
        return self._tess.image_to_string(pil_img, lang="chi_sim+eng").strip()

    def ocr_full(self, image):
        from PIL import Image
        pil_img = Image.fromarray(image)
        data = self._tess.image_to_data(
            pil_img, lang="chi_sim+eng", output_type=self._tess.Output.DICT
        )
        items = []
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            conf = int(data["conf"][i])
            if not text or conf < 30:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            items.append({
                "text": text,
                "bbox": (x, y, x + w, y + h),
                "confidence": conf / 100.0,
            })
        return items


class _EasyOCREngine(_OCRBase):
    """EasyOCR 引擎 (第二 fallback)"""
    def __init__(self):
        import easyocr
        self._reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)

    def ocr_region(self, image, bbox=None):
        if bbox:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                return ""
        else:
            crop = image
        results = self._reader.readtext(crop)
        return "\n".join(r[1] for r in results if r[1].strip())

    def ocr_full(self, image):
        results = self._reader.readtext(image)
        items = []
        for pts, text, conf in results:
            if not text.strip():
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            items.append({
                "text": text,
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "confidence": conf,
            })
        return items


def get_ocr_engine() -> _OCRBase:
    """按优先级尝试加载 OCR 引擎: PaddleOCR > Tesseract > EasyOCR"""
    for EngineClass, name in [
        (_PaddleOCREngine, "PaddleOCR"),
        (_TesseractOCREngine, "Tesseract"),
        (_EasyOCREngine, "EasyOCR"),
    ]:
        try:
            engine = EngineClass()
            logger.info("OCR 引擎: %s", name)
            return engine
        except (ImportError, Exception) as e:
            logger.debug("OCR %s 不可用: %s", name, e)
            continue
    raise RuntimeError(
        "无可用 OCR 引擎。请安装至少一个:\n"
        "  pip install paddlepaddle paddleocr   # 推荐\n"
        "  pip install pytesseract              # 需装 Tesseract 二进制\n"
        "  pip install easyocr"
    )


# ---------------------------------------------------------------------------
# 图片 → document_blocks 转换
# ---------------------------------------------------------------------------

def _regions_ocr_to_blocks(
    image: "np.ndarray",
    regions: List,
    ocr_engine: _OCRBase,
    doc_type: str,
) -> list:
    """策略 A: YOLOv10 区域 + OCR → document_blocks"""
    try:
        from .phase1_physical_layer import (
            DocumentBlock, classify_heading, classify_by_text_content,
            apply_section_scope_inheritance, apply_semantic_subsplit,
            cross_page_merge,
        )
    except ImportError:
        from phase1_physical_layer import (
            DocumentBlock, classify_heading, classify_by_text_content,
            apply_section_scope_inheritance, apply_semantic_subsplit,
            cross_page_merge,
        )

    blocks = []
    for i, region in enumerate(regions):
        text = ocr_engine.ocr_region(image, bbox=region.bbox)
        if not text.strip():
            continue

        # 按行拆分, 第一行可能是标题
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            continue

        # 尝试标题分类 (短行 + 首行)
        first_line = lines[0]
        if len(first_line) <= 12 and len(lines) > 1:
            bt, conf = classify_heading(first_line, 2, doc_type)
            heading = first_line
            body = "\n".join(lines[1:])
        else:
            bt, conf = classify_by_text_content("\n".join(lines), doc_type)
            heading = ""
            body = "\n".join(lines)

        blocks.append(DocumentBlock(
            block_id=f"b_{i + 1}",
            block_type=bt,
            text=body,
            confidence=conf * region.confidence,
            heading=heading,
            source={
                "file_type": "image",
                "page": None,
                "bbox": list(region.bbox),
            },
        ))

    blocks = cross_page_merge(blocks)
    blocks = apply_section_scope_inheritance(blocks)
    blocks = apply_semantic_subsplit(blocks)
    return blocks


def _fullpage_ocr_to_blocks(
    image: "np.ndarray",
    ocr_engine: _OCRBase,
    doc_type: str,
) -> list:
    """策略 B: 全图 OCR → 按 bbox 排序 → document_blocks"""
    try:
        from .phase1_physical_layer import (
            DocumentBlock, classify_heading, classify_by_text_content,
            apply_section_scope_inheritance, apply_semantic_subsplit,
            cross_page_merge,
        )
    except ImportError:
        from phase1_physical_layer import (
            DocumentBlock, classify_heading, classify_by_text_content,
            apply_section_scope_inheritance, apply_semantic_subsplit,
            cross_page_merge,
        )

    ocr_items = ocr_engine.ocr_full(image)
    if not ocr_items:
        logger.warning("OCR 未识别到任何文本")
        return []

    # 按 y → x 排序 (阅读顺序)
    ocr_items.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))

    # 合并相邻行为段落块 (y 间距 < 行高 × 1.5 视为同段)
    blocks = []
    current_lines = []
    current_heading = ""
    current_bt = "OTHER"
    current_conf = 0.5
    current_bbox = None
    last_y2 = 0
    idx = 0

    def flush():
        nonlocal current_lines, current_heading, current_bt, current_conf, current_bbox, idx
        if current_lines:
            idx += 1
            text = "\n".join(current_lines)
            blocks.append(DocumentBlock(
                block_id=f"b_{idx}",
                block_type=current_bt,
                text=text,
                confidence=current_conf,
                heading=current_heading,
                source={
                    "file_type": "image",
                    "page": None,
                    "bbox": list(current_bbox) if current_bbox else None,
                },
            ))
        current_lines = []
        current_heading = ""
        current_bt = "OTHER"
        current_conf = 0.5
        current_bbox = None

    for item in ocr_items:
        text = item["text"].strip()
        bbox = item["bbox"]
        line_height = bbox[3] - bbox[1]
        gap = bbox[1] - last_y2

        # 段落断开: y 间距 > 1.5 倍行高
        if current_lines and gap > line_height * 1.5:
            flush()

        # 短文本可能是标题
        if not current_lines and len(text) <= 12:
            bt, conf = classify_heading(text, 2, doc_type)
            if conf > 0.7:
                current_heading = text
                current_bt = bt
                current_conf = conf
                current_bbox = bbox
                last_y2 = bbox[3]
                continue

        if not current_lines:
            bt, conf = classify_by_text_content(text, doc_type)
            current_bt = bt
            current_conf = conf
            current_bbox = bbox
        else:
            # 扩展 bbox
            current_bbox = (
                min(current_bbox[0], bbox[0]),
                min(current_bbox[1], bbox[1]),
                max(current_bbox[2], bbox[2]),
                max(current_bbox[3], bbox[3]),
            )

        current_lines.append(text)
        last_y2 = bbox[3]

    flush()

    blocks = cross_page_merge(blocks)
    blocks = apply_section_scope_inheritance(blocks)
    blocks = apply_semantic_subsplit(blocks)
    return blocks


# ---------------------------------------------------------------------------
# MinerU 输出文件发现
# ---------------------------------------------------------------------------

def _find_mineru_outputs(
    mineru_base: Path, pdf_stem: str,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """
    在 mineru_base 下搜索与 pdf_stem 匹配的预解析输出文件。

    MinerU 输出目录结构不固定, 支持多种命名约定:
      - {mineru_base}/{stem}/auto/content_list_v2.json          (标准)
      - {mineru_base}/{name}/auto/{name}_content_list_v2.json   (带前缀)
      - {mineru_base}/**/auto/*content_list_v2.json             (通配搜索)

    Returns: (content_v2_path, content_v1_path, middle_path) — 找不到为 None
    """
    content_v2 = content_v1 = middle = None

    # 策略 1: 精确匹配 {stem}/auto/
    exact_base = mineru_base / pdf_stem / "auto"
    if exact_base.is_dir():
        v2 = exact_base / "content_list_v2.json"
        v1 = exact_base / "content_list.json"
        mid = exact_base / "middle.json"
        if v2.exists():
            content_v2 = v2
        if v1.exists():
            content_v1 = v1
        if mid.exists():
            middle = mid
        if content_v2 or content_v1 or middle:
            return content_v2, content_v1, middle

    # 策略 2: 遍历所有子目录下的 auto/ 查找匹配文件 (带前缀命名)
    for auto_dir in mineru_base.rglob("auto"):
        if not auto_dir.is_dir():
            continue
        # 搜索 *content_list_v2.json
        for f in auto_dir.iterdir():
            name_lower = f.name.lower()
            if "content_list_v2" in name_lower and f.suffix == ".json":
                content_v2 = f
            elif "content_list" in name_lower and "v2" not in name_lower and f.suffix == ".json":
                content_v1 = f
            elif "middle" in name_lower and f.suffix == ".json":
                middle = f

        if content_v2 or content_v1 or middle:
            logger.info(
                "MinerU 输出匹配: v2=%s, v1=%s, mid=%s",
                content_v2.name if content_v2 else "N/A",
                content_v1.name if content_v1 else "N/A",
                middle.name if middle else "N/A",
            )
            return content_v2, content_v1, middle

    logger.warning("MinerU 未找到匹配的预解析输出: pdf_stem=%s", pdf_stem)
    return None, None, None


# ---------------------------------------------------------------------------
# InputRouter — 统一路由器
# ---------------------------------------------------------------------------

class InputRouter:
    """
    统一输入路由器。

    使用方式:
        router = InputRouter(yolo_model_path="path/to/best.onnx")

        # 文件上传
        result = router.route(Path("resume.pdf"), doc_type="resume")
        result = router.route(Path("resume.docx"), doc_type="resume")
        result = router.route(Path("jd_data.json"), doc_type="jd")

        # 复制粘贴
        result = router.route_text("岗位职责: ...", doc_type="jd")

        # 前端表单
        result = router.route_form({"name": "张三", ...}, doc_type="resume")

    路由逻辑:
        PDF/Word/Image → 非结构化通道 → Phase 1 物理解析 → document_blocks
        TXT            → 非结构化通道 → Phase 1 文本解析 → document_blocks
        JSON/Excel     → 结构化通道   → 字段映射 → Schema (跳过 Phase 1)
        Form (dict)    → 结构化通道   → 直接透传 → Schema (跳过 Phase 1)
    """

    def __init__(
        self,
        yolo_model_path: Optional[Union[str, Path]] = None,
        mineru_base: Optional[Path] = None,
    ):
        self._yolo_model_path = yolo_model_path
        self._mineru_base = mineru_base
        self._physical_layer = None

    @property
    def physical_layer(self):
        """延迟加载 Phase 1 物理解析层"""
        if self._physical_layer is None:
            try:
                from .phase1_physical_layer import PhysicalParseLayer
            except ImportError:
                from phase1_physical_layer import PhysicalParseLayer
            self._physical_layer = PhysicalParseLayer(
                yolo_model_path=self._yolo_model_path
            )
        return self._physical_layer

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def route(
        self,
        file_path: Union[str, Path],
        doc_type: str = "resume",
    ) -> RouteResult:
        """
        路由文件输入。

        Args:
            file_path: 文件路径
            doc_type: "resume" 或 "jd"
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        fmt = detect_format(file_path)
        logger.info("路由: %s → 格式=%s, 类型=%s", file_path.name, fmt.value, doc_type)

        # 非结构化通道
        if fmt == InputFormat.PDF:
            return self._route_pdf(file_path, doc_type)

        if fmt == InputFormat.DOCX:
            return self._route_docx(file_path, doc_type)

        if fmt == InputFormat.IMAGE:
            return self._route_image(file_path, doc_type)

        if fmt == InputFormat.TXT:
            text = file_path.read_text(encoding="utf-8")
            return self.route_text(text, doc_type)

        # 结构化通道
        if fmt == InputFormat.JSON:
            return self._route_json(file_path, doc_type)

        if fmt == InputFormat.EXCEL:
            return self._route_excel(file_path, doc_type)

        raise ValueError(f"不支持的文件格式: {file_path.suffix} ({fmt.value})")

    def route_text(self, text: str, doc_type: str = "jd") -> RouteResult:
        """路由纯文本输入 (复制粘贴场景)"""
        logger.info("路由: 纯文本 (%d 字符), 类型=%s", len(text), doc_type)

        blocks = self.physical_layer.parse_jd_txt(text) if doc_type == "jd" else []

        if doc_type == "resume" and text.strip():
            # 简历纯文本：走块分类
            try:
                from .phase1_physical_layer import (
                    DocumentBlock, classify_by_text_content, cross_page_merge,
                    apply_section_scope_inheritance, apply_semantic_subsplit,
                )
            except ImportError:
                from phase1_physical_layer import (
                    DocumentBlock, classify_by_text_content, cross_page_merge,
                    apply_section_scope_inheritance, apply_semantic_subsplit,
                )
            lines = text.strip().split("\n")
            blocks = []
            idx = 0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                idx += 1
                bt, conf = classify_by_text_content(line, "resume")
                blocks.append(DocumentBlock(
                    block_id=f"b_{idx}",
                    block_type=bt,
                    text=line,
                    confidence=conf,
                    source={"file_type": "txt", "page": None, "bbox": None},
                ))
            blocks = cross_page_merge(blocks)
            blocks = apply_section_scope_inheritance(blocks)
            blocks = apply_semantic_subsplit(blocks)

        return RouteResult(
            channel=ChannelType.PHYSICAL_PARSE,
            doc_type=doc_type,
            input_format=InputFormat.TXT,
            blocks=blocks,
            metadata={"char_count": len(text)},
        )

    def route_form(self, form_data: Dict[str, Any], doc_type: str = "resume") -> RouteResult:
        """路由前端表单输入 (结构化，跳过 Phase 1)"""
        logger.info("路由: 表单数据, 类型=%s, 字段数=%d", doc_type, len(form_data))

        return RouteResult(
            channel=ChannelType.STRUCTURED_BYPASS,
            doc_type=doc_type,
            input_format=InputFormat.FORM,
            structured_data=form_data,
            metadata={"field_count": len(form_data)},
        )

    # ------------------------------------------------------------------
    # 非结构化通道
    # ------------------------------------------------------------------

    def _route_pdf(self, file_path: Path, doc_type: str) -> RouteResult:
        """PDF 通道 → MinerU + YOLOv10"""
        content_v2 = content_v1 = middle = None

        if self._mineru_base:
            content_v2, content_v1, middle = _find_mineru_outputs(
                self._mineru_base, file_path.stem
            )

        if content_v2 or content_v1 or middle:
            blocks = self.physical_layer.parse_resume_pdf(
                content_v2_path=content_v2,
                content_v1_path=content_v1,
                middle_json_path=middle,
                pdf_path=file_path,
            )
        else:
            # 没有预解析结果，只传 PDF 路径 (需要在线调用 MinerU)
            blocks = self.physical_layer.parse_resume_pdf(pdf_path=file_path)

        return RouteResult(
            channel=ChannelType.PHYSICAL_PARSE,
            doc_type=doc_type,
            input_format=InputFormat.PDF,
            blocks=blocks,
            metadata={"file_name": file_path.name, "file_size": file_path.stat().st_size},
        )

    def _route_docx(self, file_path: Path, doc_type: str) -> RouteResult:
        """Word 通道 → python-docx 提取 + 块分类"""
        paragraphs = extract_docx_paragraphs(file_path)
        blocks = docx_paragraphs_to_blocks(paragraphs, doc_type)

        return RouteResult(
            channel=ChannelType.PHYSICAL_PARSE,
            doc_type=doc_type,
            input_format=InputFormat.DOCX,
            blocks=blocks,
            metadata={
                "file_name": file_path.name,
                "paragraph_count": len(paragraphs),
                "block_count": len(blocks),
            },
        )

    def _route_image(self, file_path: Path, doc_type: str) -> RouteResult:
        """图片通道 → YOLOv10 版面检测 + OCR 文本提取 + 块分类"""
        image = load_image(file_path)
        img_h, img_w = image.shape[:2]
        logger.info("图片加载: %s (%dx%d)", file_path.name, img_w, img_h)

        ocr_engine = get_ocr_engine()

        # --- 策略 A: YOLOv10 可用 → 区域级 OCR ---
        yolo = self._get_yolo_detector()  # 使用本地方法加载
        if yolo is not None:
            regions = yolo.detect(image, page=0)
            logger.info("YOLOv10 检测到 %d 个版面区域", len(regions))

            blocks = _regions_ocr_to_blocks(
                image, regions, ocr_engine, doc_type,
            )
        else:
            # --- 策略 B: 无 YOLOv10 → 全图 OCR + 按行分块 ---
            logger.info("YOLOv10 不可用, 回退全图 OCR")
            blocks = _fullpage_ocr_to_blocks(image, ocr_engine, doc_type)

        return RouteResult(
            channel=ChannelType.PHYSICAL_PARSE,
            doc_type=doc_type,
            input_format=InputFormat.IMAGE,
            blocks=blocks,
            metadata={
                "file_name": file_path.name,
                "image_size": f"{img_w}x{img_h}",
                "block_count": len(blocks),
                "yolo_available": yolo is not None,
                "ocr_engine": ocr_engine.__class__.__name__,
            },
        )

    def _get_yolo_detector(self):
        """直接用 importlib 加载 YOLOLayoutDetector，绕过 phase1_physical_layer"""
        if self._yolo_model_path is None:
            return None
        try:
            import importlib.util
            from pathlib import Path as _Path
            mod_path = _Path(__file__).resolve().parent / "layout" / "yolo_layout_detector.py"
            if not mod_path.exists():
                return None
            spec = importlib.util.spec_from_file_location("yolo_layout_detector", str(mod_path))
            mod = importlib.util.module_from_spec(spec)
            # 必须先注册到 sys.modules，否则 @dataclass 会报错
            import sys as _sys
            _sys.modules["yolo_layout_detector"] = mod
            spec.loader.exec_module(mod)
            return mod.YOLOLayoutDetector(self._yolo_model_path)
        except Exception as e:
            logger.warning("YOLOv10 加载失败: %s", e)
            return None

    # ------------------------------------------------------------------
    # 结构化通道
    # ------------------------------------------------------------------

    def _route_json(self, file_path: Path, doc_type: str) -> RouteResult:
        """JSON 通道 → 字段映射 (跳过 Phase 1)"""
        raw = json.loads(file_path.read_text(encoding="utf-8"))

        if doc_type == "jd":
            # 竞赛 JD 批量数据
            if isinstance(raw, list):
                mapped = map_jd_json_batch(file_path, filter_it=True)
                return RouteResult(
                    channel=ChannelType.STRUCTURED_BYPASS,
                    doc_type=doc_type,
                    input_format=InputFormat.JSON,
                    structured_data={"records": mapped, "total": len(mapped)},
                    metadata={
                        "file_name": file_path.name,
                        "raw_count": len(raw),
                        "mapped_count": len(mapped),
                    },
                )
            # 单条 JD
            mapped = map_jd_record(raw) if isinstance(raw, dict) else raw
            return RouteResult(
                channel=ChannelType.STRUCTURED_BYPASS,
                doc_type=doc_type,
                input_format=InputFormat.JSON,
                structured_data=mapped,
                metadata={"file_name": file_path.name},
            )

        # 简历 JSON (表单提交的结构化数据)
        return RouteResult(
            channel=ChannelType.STRUCTURED_BYPASS,
            doc_type=doc_type,
            input_format=InputFormat.JSON,
            structured_data=raw,
            metadata={"file_name": file_path.name},
        )

    def _route_excel(self, file_path: Path, doc_type: str) -> RouteResult:
        """Excel 通道 → pandas 读取 + 字段映射 (跳过 Phase 1)"""
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("需要 pandas 来解析 Excel 文件: pip install pandas openpyxl")

        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(file_path, encoding="utf-8")
        else:
            df = pd.read_excel(file_path)

        records = df.to_dict(orient="records")

        if doc_type == "jd":
            mapped = []
            filtered = 0
            for record in records:
                m = map_jd_record(record)
                if not m.get("is_it_position", True):
                    filtered += 1
                    continue
                mapped.append(m)

            return RouteResult(
                channel=ChannelType.STRUCTURED_BYPASS,
                doc_type=doc_type,
                input_format=InputFormat.EXCEL,
                structured_data={"records": mapped, "total": len(mapped)},
                metadata={
                    "file_name": file_path.name,
                    "raw_count": len(records),
                    "mapped_count": len(mapped),
                    "filtered_count": filtered,
                },
            )

        # 简历 Excel (罕见，但支持)
        return RouteResult(
            channel=ChannelType.STRUCTURED_BYPASS,
            doc_type=doc_type,
            input_format=InputFormat.EXCEL,
            structured_data={"records": records, "total": len(records)},
            metadata={"file_name": file_path.name, "row_count": len(records)},
        )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_supported_formats(self) -> Dict[str, List[str]]:
        """返回支持的文件格式列表"""
        return {
            "resume": {
                "physical_parse": [".pdf", ".docx", ".doc", ".jpg", ".png", ".txt"],
                "structured_bypass": [".json", "form"],
            },
            "jd": {
                "physical_parse": [".txt"],
                "structured_bypass": [".json", ".xlsx", ".xls", ".csv", "form"],
            },
        }
