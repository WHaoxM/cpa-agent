"""
L0 完整解析模块 (Full L0 Parser)
整合 MinerU + YOLOv10 + OCR + DeepSeek

支持:
1. PDF 文件 → MinerU 解析 / PyMuPDF fallback
2. DOCX 文件 → python-docx
3. 图片 PDF → YOLOv10 + OCR
4. 文本 → 直接分块
"""

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field

from app.utils.logger import get_logger
from .pdf_engines import (
    EngineStatus,
    get_mineru_state,
    get_sidecar_state,
    mark_mineru_failed,
    mark_sidecar_failed,
    mineru_ready,
)

logger = get_logger('cpa-agent.l0_full_parser')

# 项目根目录 (backend/app/pipeline/entity_extractor/ 向上 4 级)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# YOLOv10 ONNX 模型路径 (与 run.py 模型缓存分层保持一致)
_YOLO_MODEL_PATH = _PROJECT_ROOT / 'models' / 'yolo' / 'SmartResume' / 'yolov10' / 'best.onnx'

logging.getLogger('mineru').setLevel(logging.WARNING)
logging.getLogger('mineru.backend').setLevel(logging.WARNING)
logging.getLogger('mineru.backend.pipeline').setLevel(logging.WARNING)
logging.getLogger('modelscope').setLevel(logging.WARNING)
logging.getLogger('modelscope.hub').setLevel(logging.WARNING)

_mineru_cache = {}


@dataclass
class L0Block:
    """L0 解析的 Block"""
    block_id: str
    block_type: str
    text: str
    confidence: float
    source: str
    bbox: Optional[tuple] = None
    page: int = 0


@dataclass
class L0Result:
    """L0 完整解析结果"""
    success: bool
    text: str
    blocks: List[L0Block]
    metadata: Dict[str, Any]
    engine: str
    error: Optional[str] = None


class L0FullParser:
    """
    L0 完整解析器

    支持多种输入格式和解析引擎:
    - MinerU (优先, 需要 GPU)
    - PyMuPDF (备选)
    - YOLOv10 + OCR (图片PDF)
    - python-docx (DOCX)
    """

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu
        self._yolo_available = None

    def parse_file(self, file_path: Union[str, Path]) -> L0Result:
        """解析文件"""
        file_path = Path(file_path)

        if not file_path.exists():
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="none", error=f"文件不存在: {file_path}"
            )

        suffix = file_path.suffix.lower()

        try:
            if suffix == ".pdf":
                return self._parse_pdf(str(file_path))
            elif suffix in [".docx", ".doc"]:
                return self._parse_docx(str(file_path))
            elif suffix == ".txt":
                return self._parse_txt(str(file_path))
            elif suffix in [".jpg", ".jpeg", ".png", ".bmp"]:
                return self._parse_image(str(file_path))
            else:
                return L0Result(
                    success=False, text="", blocks=[], metadata={},
                    engine="none", error=f"不支持格式: {suffix}"
                )
        except Exception as e:
            logger.error(f"解析失败: {e}")
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="error", error=str(e)
            )

    def parse_text(self, text: str, doc_type: str = "resume") -> L0Result:
        """解析纯文本"""
        if not text or not text.strip():
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="text", error="文本为空"
            )

        blocks = self._text_to_blocks(text, doc_type)

        return L0Result(
            success=True,
            text=text,
            blocks=blocks,
            metadata={"length": len(text), "doc_type": doc_type, "block_count": len(blocks)},
            engine="text"
        )

    def _parse_pdf(self, file_path: str) -> L0Result:
        """解析 PDF：auto = Sidecar → PyMuPDF；显式 mineru 才进进程内 MinerU。"""
        sc = get_sidecar_state()
        if sc.status == EngineStatus.READY:
            result = self._parse_pdf_sidecar(file_path)
            if result.success:
                return result
            mark_sidecar_failed(result.error or "sidecar parse failed")
            logger.info("Sidecar 失败，降级 PyMuPDF: %s", result.error)

        mineru_state = get_mineru_state()
        if mineru_state.status == EngineStatus.READY:
            return self._parse_pdf_mineru(
                file_path,
                formula_enable=mineru_state.formula_enable,
            )
        if mineru_state.status in (EngineStatus.FAILED, EngineStatus.DISABLED):
            logger.debug(
                "跳过 MinerU (%s: %s)",
                mineru_state.status.value,
                mineru_state.reason,
            )
        return self._parse_pdf_pymupdf(file_path)

    def _parse_pdf_sidecar(self, file_path: str) -> L0Result:
        from .pdf_sidecar import parse_pdf_via_sidecar

        payload = parse_pdf_via_sidecar(file_path)
        if not payload.get("success"):
            return L0Result(
                success=False,
                text="",
                blocks=[],
                metadata=payload.get("metadata") or {},
                engine="sidecar",
                error=payload.get("error") or "sidecar failed",
            )
        text = payload.get("text") or ""
        blocks = self._text_to_blocks(text, "resume")
        return L0Result(
            success=True,
            text=text,
            blocks=blocks,
            metadata=payload.get("metadata") or {"engine": "sidecar"},
            engine="sidecar",
        )

    def _parse_pdf_mineru(self, file_path: str, formula_enable: bool = False) -> L0Result:
        """使用 MinerU 解析 PDF (mineru 3.x API)。简历默认 formula_enable=False。"""
        try:
            import pymupdf
            pdf_doc = pymupdf.open(file_path)
            total_pages = len(pdf_doc)
            pdf_doc.close()

            cache_key = f"mineru_{total_pages}p"
            if cache_key in _mineru_cache:
                logger.info(f"复用缓存的 MinerU 模型: {cache_key}")

            from mineru.cli.common import do_parse, read_fn
            import json

            pdf_path = Path(file_path)
            output_dir = Path("./mineru_output") / pdf_path.stem
            output_dir.mkdir(parents=True, exist_ok=True)

            pdf_bytes = read_fn(file_path, "pdf")

            do_parse(
                output_dir=str(output_dir),
                pdf_file_names=[pdf_path.stem],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=["ch"],
                backend="pipeline",
                parse_method="auto",
                formula_enable=formula_enable,
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_md=False,
                f_dump_middle_json=True,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_dump_content_list=True,
            )

            # 优先找 content_list.json（V1），其次 _middle.json
            json_files = list(output_dir.rglob("*_content_list.json"))
            if not json_files:
                json_files = list(output_dir.rglob("*_middle.json"))
            if not json_files:
                raise FileNotFoundError(f"MinerU 未生成输出文件, output_dir={output_dir}")

            latest_json = max(json_files, key=lambda p: p.stat().st_mtime)
            with open(latest_json, 'r', encoding='utf-8') as f:
                data = json.load(f)

            logger.info(f"MinerU JSON 数据类型: {type(data)}, 长度: {len(data) if isinstance(data, list) else 'N/A'}")

            blocks = self._mineru_json_to_blocks(data)
            logger.info(f"MinerU 提取到 {len(blocks)} 个文本块")
            full_text = "\n".join([b.text for b in blocks])

            return L0Result(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={
                    "file": file_path,
                    "engine": "mineru",
                    "json": str(latest_json),
                    "formula_enable": formula_enable,
                },
                engine="mineru"
            )

        except Exception as e:
            import traceback
            logger.warning(
                "MinerU 运行时失败，本文件降级 PyMuPDF（非粘性）: %s\n%s",
                e,
                traceback.format_exc(),
            )
            # Only ImportError/stack incompatibility sticks; layout glitches must not
            # disable MinerU for every later scanned resume in this process.
            mark_mineru_failed(str(e), permanent=isinstance(e, ImportError))
            return self._parse_pdf_pymupdf(file_path)

    def _parse_pdf_pymupdf(self, file_path: str) -> L0Result:
        """使用 PyMuPDF 解析 PDF"""
        try:
            import pymupdf

            doc = pymupdf.open(file_path)
            num_pages = len(doc)
            all_text_parts = []

            for page_num in range(num_pages):
                page = doc[page_num]
                text = page.get_text("text")
                if text.strip():
                    blocks = self._extract_blocks_from_text(text, "resume", page_num)
                    all_text_parts.append(text)

            full_text = "\n".join(all_text_parts)
            doc.close()

            blocks = self._text_to_blocks(full_text, "resume")

            return L0Result(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={"file": file_path, "pages": num_pages, "engine": "pymupdf"},
                engine="pymupdf"
            )

        except ImportError:
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="pymupdf", error="需要安装 pymupdf: pip install pymupdf"
            )

    def _parse_image(self, file_path: str) -> L0Result:
        """解析图片 (使用 OCR)"""
        if self._check_yolo():
            return self._parse_image_yolo_ocr(file_path)
        else:
            return self._parse_image_pytesseract(file_path)

    def _parse_image_yolo_ocr(self, file_path: str) -> L0Result:
        """使用 YOLOv10 + OCR 解析图片"""
        try:
            from layout.yolo_layout_detector import YOLOLayoutDetector
            import numpy as np
            from PIL import Image

            model_path = _YOLO_MODEL_PATH
            detector = YOLOLayoutDetector(str(model_path))

            image = Image.open(file_path)
            regions = detector.detect(image)

            ocr_texts = []
            for region in regions:
                cropped = image.crop(region.bbox)
                text = self._ocr_image(cropped)
                ocr_texts.append((region.bbox, text))

            ocr_texts.sort(key=lambda x: (x[0][1], x[0][0]))
            full_text = "\n".join([t[1] for t in ocr_texts])
            blocks = self._text_to_blocks(full_text, "resume")

            return L0Result(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={"file": file_path, "engine": "yolo+ocr", "regions": len(regions)},
                engine="yolo+ocr"
            )

        except Exception as e:
            logger.error(f"YOLOv10 OCR 失败: {e}")
            return self._parse_image_pytesseract(file_path)

    def _parse_image_pytesseract(self, file_path: str) -> L0Result:
        """使用 pytesseract OCR 解析图片"""
        try:
            import pytesseract
            from PIL import Image

            image = Image.open(file_path)
            text = pytesseract.image_to_string(image, lang='chi+eng')

            blocks = self._text_to_blocks(text, "resume")

            return L0Result(
                success=True,
                text=text,
                blocks=blocks,
                metadata={"file": file_path, "engine": "tesseract"},
                engine="tesseract"
            )

        except ImportError:
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="tesseract", error="需要安装 pytesseract"
            )

    def _ocr_image(self, image) -> str:
        """OCR 单张图片"""
        try:
            import pytesseract
            return pytesseract.image_to_string(image, lang='chi+eng')
        except:
            return ""

    def _parse_docx(self, file_path: str) -> L0Result:
        """解析 DOCX 文件"""
        try:
            from docx import Document

            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs]
            full_text = "\n".join(paragraphs)
            blocks = self._text_to_blocks(full_text, "resume")

            return L0Result(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={"file": file_path, "engine": "docx"},
                engine="docx"
            )

        except ImportError:
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="docx", error="需要安装 python-docx"
            )

    def _parse_txt(self, file_path: str) -> L0Result:
        """解析 TXT 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_text = f.read()

            blocks = self._text_to_blocks(full_text, "resume")

            return L0Result(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={"file": file_path, "engine": "txt"},
                engine="txt"
            )

        except Exception as e:
            return L0Result(
                success=False, text="", blocks=[], metadata={},
                engine="txt", error=str(e)
            )

    def _mineru_json_to_blocks(self, data: Any) -> List[L0Block]:
        """将 MinerU JSON 转换为 L0Block"""
        blocks = []
        block_id = 1

        if isinstance(data, list):
            elements = data
        else:
            pages = data.get("pages", [])
            elements = []
            for page in pages:
                children = page.get("children", [])
                for child in children:
                    child["page_idx"] = page.get("page_idx", 0)
                elements.extend(children)

        for element in elements:
            if element.get("type") == "text":
                text = element.get("text", "")
                if isinstance(text, dict):
                    text = text.get("content", "")
                if text and text.strip():
                    blocks.append(L0Block(
                        block_id=f"b{block_id}",
                        block_type="TEXT",
                        text=text.strip(),
                        confidence=element.get("score", 0.9),
                        source="mineru",
                        page=element.get("page_idx", 0)
                    ))
                    block_id += 1

        return blocks

    def _text_to_blocks(self, text: str, doc_type: str, page: int = 0) -> List[L0Block]:
        """将文本转换为 L0Block"""
        import re

        lines = text.split("\n")
        blocks = []
        current_lines = []
        current_type = "OTHER"
        block_id = 1

        resume_patterns = [
            (["技能", "技术", "证书", "专长", "能力"], "SKILLS_CERTS"),
            (["项目", "科研", "课题"], "PROJECT_EXPERIENCE"),
            (["工作", "经历", "经验"], "WORK_EXPERIENCE"),
            (["教育", "学历", "毕业", "学校"], "EDUCATION"),
            (["个人", "基本信息", "简介", "contact"], "PERSONAL_INFO"),
        ]

        jd_patterns = [
            (["岗位", "职位", "title"], "JOB_TITLE"),
            (["要求", "任职", "qualification"], "JOB_REQUIREMENTS"),
            (["职责", "工作内容", "description"], "JOB_DESCRIPTION"),
        ]

        patterns = jd_patterns if doc_type == "jd" else resume_patterns

        for line in lines:
            line = line.strip()
            if not line:
                continue

            matched_type = None
            for keywords, block_type in patterns:
                for keyword in keywords:
                    if keyword.lower() in line.lower():
                        matched_type = block_type
                        break
                if matched_type:
                    break

            if matched_type:
                if current_lines:
                    blocks.append(L0Block(
                        block_id=f"b{block_id}",
                        block_type=current_type,
                        text="\n".join(current_lines),
                        confidence=0.9,
                        source="text",
                        page=page
                    ))
                    block_id += 1
                    current_lines = []

                current_type = matched_type
                current_lines.append(line)
            else:
                current_lines.append(line)

        if current_lines:
            blocks.append(L0Block(
                block_id=f"b{block_id}",
                block_type=current_type,
                text="\n".join(current_lines),
                confidence=0.8,
                source="text",
                page=page
            ))

        return blocks

    def _extract_blocks_from_text(self, text: str, doc_type: str, page: int = 0) -> List[L0Block]:
        """提取 blocks (兼容方法)"""
        return self._text_to_blocks(text, doc_type, page)

    def _check_mineru(self) -> bool:
        """兼容旧调用：委托进程级能力登记。"""
        return mineru_ready()

    def _check_yolo(self) -> bool:
        """检查 YOLOv10 是否可用"""
        if self._yolo_available is None:
            try:
                model_path = _YOLO_MODEL_PATH
                if not model_path.exists():
                    self._yolo_available = False
                    return False

                import onnxruntime as ort
                sess = ort.InferenceSession(str(model_path))
                self._yolo_available = True
                logger.info("YOLOv10 可用")
                return True
            except Exception as e:
                self._yolo_available = False
                logger.warning(f"YOLOv10 不可用: {e}")
                return False
        return self._yolo_available


def parse_l0_file(file_path: str, use_gpu: bool = False) -> L0Result:
    """便捷函数: 解析文件"""
    parser = L0FullParser(use_gpu=use_gpu)
    return parser.parse_file(file_path)


def parse_l0_text(text: str, doc_type: str = "resume") -> L0Result:
    """便捷函数: 解析文本"""
    parser = L0FullParser()
    return parser.parse_text(text, doc_type)


if __name__ == "__main__":
    test_text = """
    张三 | Python后端开发 | 3年经验

    个人信息
    手机: 138xxxx8888
    邮箱: zhangsan@email.com

    教育背景
    2015-2019 XX大学 计算机科学与技术 本科

    技能特长
    - Python, Django, Flask
    - MySQL, Redis
    - Docker, Kubernetes
    """

    print("=" * 50)
    print("L0 Full Parser 测试")
    print("=" * 50)

    result = parse_l0_text(test_text, "resume")

    print(f"\n成功: {result.success}")
    print(f"引擎: {result.engine}")
    print(f"Blocks: {len(result.blocks)}")

    for block in result.blocks:
        print(f"  [{block.block_type}] {block.text[:50]}...")
