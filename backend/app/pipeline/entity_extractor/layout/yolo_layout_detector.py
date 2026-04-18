"""
YOLOv10 版面检测器 (Layout Detector)
基于 SmartResume 论文 (arXiv:2510.09722) §3.1.2

核心职责：
1. ONNX Runtime 推理：检测 PDF 页面中的 layout segments
2. 层级排序：inter-segment + intra-segment 排序
3. 与 MinerU text blocks 做 bbox IoU 对齐
4. 生成带行号的线性化文本流 (Indexed Linearization)

模型规格 (best.onnx)：
- Input:  images [1, 3, 640, 640] float32
- Output: output0 [1, 300, 5] float32 (x1, y1, x2, y2, score)
- 单类检测：只检测 layout segment 区域，不区分具体类型
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class LayoutRegion:
    """版面区域：YOLOv10 检测到的一个 layout segment"""
    region_id: str
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2) 原图坐标
    confidence: float
    page: int = 0
    text_blocks: List[dict] = field(default_factory=list)  # 对齐后的 MinerU text blocks
    sort_key: Tuple[float, float] = (0.0, 0.0)  # (y_center, x_center)


@dataclass
class IndexedLine:
    """带行号的文本行：用于 Index-based Pointer Mechanism"""
    line_idx: int
    text: str
    region_id: str
    block_idx: int  # 原始 MinerU block 的索引
    bbox: Optional[Tuple[float, float, float, float]] = None


# ---------------------------------------------------------------------------
# YOLOv10 Layout Detector
# ---------------------------------------------------------------------------

class YOLOLayoutDetector:
    """YOLOv10 版面检测器

    使用 SmartResume 的 YOLOv10 ONNX 模型检测 PDF 页面中的版面区域，
    然后通过层级排序重建线性阅读顺序。
    """

    INPUT_SIZE = 640  # 模型输入尺寸
    CONFIDENCE_THRESHOLD = 0.25
    IOU_THRESHOLD = 0.45  # NMS 用
    ALIGNMENT_IOU_THRESHOLD = 0.3  # text block 与 layout region 对齐阈值

    def __init__(self, model_path: str | Path):
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"YOLOv10 ONNX 模型不存在: {self.model_path}")

        self._session = None

    @property
    def session(self):
        """延迟加载 ONNX session"""
        if self._session is None:
            import onnxruntime as ort
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            available = ort.get_available_providers()
            providers = [p for p in providers if p in available]
            self._session = ort.InferenceSession(
                str(self.model_path), providers=providers
            )
            logger.info(
                "YOLOv10 模型已加载: %s (providers=%s)",
                self.model_path.name, providers
            )
        return self._session

    # ------------------------------------------------------------------
    # 图像预处理
    # ------------------------------------------------------------------

    def _preprocess(self, image: np.ndarray) -> Tuple[np.ndarray, float, float, int, int]:
        """
        将图像预处理为模型输入格式。
        letterbox resize → normalize → CHW → batch

        返回:
            input_tensor: (1, 3, 640, 640) float32
            scale: 缩放比例
            pad_w, pad_h: padding 像素
            orig_h, orig_w: 原图尺寸
        """
        orig_h, orig_w = image.shape[:2]

        # letterbox resize: 保持长宽比，填充灰色
        scale = min(self.INPUT_SIZE / orig_w, self.INPUT_SIZE / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)

        # 使用 numpy 实现 resize（避免 cv2 依赖）
        resized = self._resize_image(image, new_w, new_h)

        # padding
        pad_w = (self.INPUT_SIZE - new_w) // 2
        pad_h = (self.INPUT_SIZE - new_h) // 2
        canvas = np.full(
            (self.INPUT_SIZE, self.INPUT_SIZE, 3), 114, dtype=np.uint8
        )
        canvas[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized

        # normalize + CHW + batch
        input_tensor = canvas.astype(np.float32) / 255.0
        input_tensor = np.transpose(input_tensor, (2, 0, 1))  # HWC → CHW
        input_tensor = np.expand_dims(input_tensor, axis=0)    # → NCHW

        return input_tensor, scale, pad_w, pad_h, orig_h, orig_w

    @staticmethod
    def _resize_image(image: np.ndarray, new_w: int, new_h: int) -> np.ndarray:
        """双线性插值 resize（优先使用 PIL，fallback numpy）"""
        try:
            from PIL import Image
            pil_img = Image.fromarray(image)
            pil_img = pil_img.resize((new_w, new_h), Image.BILINEAR)
            return np.array(pil_img)
        except ImportError:
            pass

        # numpy fallback（简单最近邻）
        orig_h, orig_w = image.shape[:2]
        row_indices = (np.arange(new_h) * orig_h / new_h).astype(int)
        col_indices = (np.arange(new_w) * orig_w / new_w).astype(int)
        return image[np.ix_(row_indices, col_indices)]

    # ------------------------------------------------------------------
    # 推理 + 后处理
    # ------------------------------------------------------------------

    def detect(
        self,
        image: np.ndarray,
        page: int = 0,
        confidence_threshold: Optional[float] = None,
    ) -> List[LayoutRegion]:
        """
        对单张图像进行版面区域检测。

        Args:
            image: RGB numpy array (H, W, 3) uint8
            page: 页码
            confidence_threshold: 置信度阈值

        Returns:
            检测到的 LayoutRegion 列表（已按阅读顺序排序）
        """
        if confidence_threshold is None:
            confidence_threshold = self.CONFIDENCE_THRESHOLD

        input_tensor, scale, pad_w, pad_h, orig_h, orig_w = self._preprocess(image)

        # ONNX 推理
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: input_tensor})

        # output shape: (1, 300, 5) → (300, 5)
        # 格式: (cx, cy, w, h, score) 归一化到 [0, 1]
        detections = outputs[0][0]

        # 过滤低置信度
        mask = detections[:, 4] >= confidence_threshold
        detections = detections[mask]

        if len(detections) == 0:
            logger.warning("页面 %d 未检测到任何版面区域", page)
            return []

        # 坐标转换: (cx, cy, w, h) normalized → (x1, y1, x2, y2) 原图像素
        regions = []
        for i, det in enumerate(detections):
            cx, cy, w, h, score = det

            # normalized → 640 pixel space
            cx_px = cx * self.INPUT_SIZE
            cy_px = cy * self.INPUT_SIZE
            w_px = w * self.INPUT_SIZE
            h_px = h * self.INPUT_SIZE

            # (cx, cy, w, h) → (x1, y1, x2, y2) in 640 space
            x1_640 = cx_px - w_px / 2.0
            y1_640 = cy_px - h_px / 2.0
            x2_640 = cx_px + w_px / 2.0
            y2_640 = cy_px + h_px / 2.0

            # 去除 letterbox padding + 缩放恢复到原图坐标
            x1 = (x1_640 - pad_w) / scale
            y1 = (y1_640 - pad_h) / scale
            x2 = (x2_640 - pad_w) / scale
            y2 = (y2_640 - pad_h) / scale

            # clip 到原图范围
            x1 = max(0, min(x1, orig_w))
            y1 = max(0, min(y1, orig_h))
            x2 = max(0, min(x2, orig_w))
            y2 = max(0, min(y2, orig_h))

            # 过滤过小区域
            if (x2 - x1) < 10 or (y2 - y1) < 10:
                continue

            y_center = (y1 + y2) / 2.0
            x_center = (x1 + x2) / 2.0

            regions.append(LayoutRegion(
                region_id=f"r_{page}_{i}",
                bbox=(float(x1), float(y1), float(x2), float(y2)),
                confidence=float(score),
                page=page,
                sort_key=(y_center, x_center),
            ))

        # inter-segment sorting: top→bottom, left→right
        regions.sort(key=lambda r: r.sort_key)

        logger.info("页面 %d 检测到 %d 个版面区域", page, len(regions))
        return regions

    def detect_from_pdf_page(
        self, pdf_path: str | Path, page_idx: int = 0, dpi: int = 144
    ) -> List[LayoutRegion]:
        """
        从 PDF 页面渲染图片并进行版面检测。

        Args:
            pdf_path: PDF 文件路径
            page_idx: 页码索引（0-based）
            dpi: 渲染 DPI

        Returns:
            检测到的 LayoutRegion 列表
        """
        image = self._render_pdf_page(pdf_path, page_idx, dpi)
        return self.detect(image, page=page_idx)

    @staticmethod
    def _render_pdf_page(pdf_path: str | Path, page_idx: int, dpi: int) -> np.ndarray:
        """将 PDF 页面渲染为 RGB numpy array"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError("需要 PyMuPDF: pip install pymupdf")

        doc = fitz.open(str(pdf_path))
        if page_idx >= len(doc):
            raise ValueError(f"页码 {page_idx} 超出范围（共 {len(doc)} 页）")

        page = doc[page_idx]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3
        )
        doc.close()
        return image

    # ------------------------------------------------------------------
    # 与 MinerU text blocks 对齐
    # ------------------------------------------------------------------

    @staticmethod
    def compute_iou(box1: Tuple, box2: Tuple) -> float:
        """计算两个 bbox 的 IoU"""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])

        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter == 0:
            return 0.0

        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - inter

        return inter / union if union > 0 else 0.0

    @staticmethod
    def compute_containment(inner: Tuple, outer: Tuple) -> float:
        """计算 inner bbox 被 outer bbox 包含的比例"""
        x1 = max(inner[0], outer[0])
        y1 = max(inner[1], outer[1])
        x2 = min(inner[2], outer[2])
        y2 = min(inner[3], outer[3])

        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area_inner = (inner[2] - inner[0]) * (inner[3] - inner[1])

        return inter / area_inner if area_inner > 0 else 0.0

    def align_text_blocks(
        self,
        regions: List[LayoutRegion],
        text_blocks: List[dict[str, Any]],
        bbox_key: str = "bbox",
    ) -> Tuple[List[LayoutRegion], List[dict[str, Any]]]:
        """
        将 MinerU text blocks 按 bbox 对齐到 layout regions。

        Args:
            regions: YOLOv10 检测到的 layout regions
            text_blocks: MinerU 输出的 text blocks（需含 bbox 字段）
            bbox_key: text block 中 bbox 字段的 key

        Returns:
            (已对齐的 regions, 未匹配的 text_blocks)
        """
        unmatched = []

        for block in text_blocks:
            block_bbox = block.get(bbox_key)
            if not block_bbox or not isinstance(block_bbox, (list, tuple)):
                unmatched.append(block)
                continue

            # 标准化 bbox 为 (x1, y1, x2, y2)
            if len(block_bbox) == 4:
                bb = tuple(float(v) for v in block_bbox)
            else:
                unmatched.append(block)
                continue

            # 找最佳匹配的 region（优先 containment，其次 IoU）
            best_region = None
            best_score = 0.0

            for region in regions:
                containment = self.compute_containment(bb, region.bbox)
                iou = self.compute_iou(bb, region.bbox)
                score = max(containment, iou)

                if score > best_score:
                    best_score = score
                    best_region = region

            if best_region and best_score >= self.ALIGNMENT_IOU_THRESHOLD:
                best_region.text_blocks.append(block)
            else:
                unmatched.append(block)

        # intra-segment sorting: 每个 region 内按 y→x 排序
        for region in regions:
            region.text_blocks.sort(
                key=lambda b: (
                    self._block_y_center(b, bbox_key),
                    self._block_x_center(b, bbox_key),
                )
            )

        return regions, unmatched

    @staticmethod
    def _block_y_center(block: dict, bbox_key: str = "bbox") -> float:
        bb = block.get(bbox_key, [0, 0, 0, 0])
        if isinstance(bb, (list, tuple)) and len(bb) >= 4:
            return (float(bb[1]) + float(bb[3])) / 2.0
        return 0.0

    @staticmethod
    def _block_x_center(block: dict, bbox_key: str = "bbox") -> float:
        bb = block.get(bbox_key, [0, 0, 0, 0])
        if isinstance(bb, (list, tuple)) and len(bb) >= 4:
            return (float(bb[0]) + float(bb[2])) / 2.0
        return 0.0

    # ------------------------------------------------------------------
    # 线性化输出 (Indexed Linearization)
    # ------------------------------------------------------------------

    def linearize(
        self,
        regions: List[LayoutRegion],
        unmatched_blocks: List[dict[str, Any]],
        text_fn=None,
        bbox_key: str = "bbox",
    ) -> List[IndexedLine]:
        """
        将已排序的 layout regions 转换为带行号的线性文本流。

        Args:
            regions: 已排序且对齐了 text blocks 的 regions
            unmatched_blocks: 未匹配到任何 region 的 text blocks
            text_fn: 从 block 提取文本的函数，默认取 block["text"]
            bbox_key: bbox 字段 key

        Returns:
            IndexedLine 列表（全局行号从 1 开始）
        """
        if text_fn is None:
            text_fn = lambda b: b.get("text", "")

        lines: List[IndexedLine] = []
        line_idx = 1

        # 已匹配的 text blocks（按 region 顺序）
        for region in regions:
            for block_i, block in enumerate(region.text_blocks):
                text = text_fn(block)
                if not text.strip():
                    continue

                # 按换行拆分为多行
                for sub_line in text.split("\n"):
                    sub_line = sub_line.strip()
                    if not sub_line:
                        continue
                    bb = block.get(bbox_key)
                    bb_tuple = tuple(float(v) for v in bb) if bb and len(bb) == 4 else None

                    lines.append(IndexedLine(
                        line_idx=line_idx,
                        text=sub_line,
                        region_id=region.region_id,
                        block_idx=block_i,
                        bbox=bb_tuple,
                    ))
                    line_idx += 1

        # 未匹配的 text blocks（坐标排序后追加）
        unmatched_blocks.sort(
            key=lambda b: (
                self._block_y_center(b, bbox_key),
                self._block_x_center(b, bbox_key),
            )
        )
        for block_i, block in enumerate(unmatched_blocks):
            text = text_fn(block)
            if not text.strip():
                continue
            for sub_line in text.split("\n"):
                sub_line = sub_line.strip()
                if not sub_line:
                    continue
                bb = block.get(bbox_key)
                bb_tuple = tuple(float(v) for v in bb) if bb and len(bb) == 4 else None

                lines.append(IndexedLine(
                    line_idx=line_idx,
                    text=sub_line,
                    region_id="unmatched",
                    block_idx=block_i,
                    bbox=bb_tuple,
                ))
                line_idx += 1

        return lines

    def build_indexed_text(self, lines: List[IndexedLine]) -> str:
        """将 IndexedLine 列表拼接为带行号的文本（供 LLM 使用）"""
        return "\n".join(f"[{l.line_idx}] {l.text}" for l in lines)
