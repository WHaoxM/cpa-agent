#!/usr/bin/env python3
"""
YOLOv10 版面检测可视化脚本

用法:
    python viz_yolo_detect.py                          # 默认检测 简历.jpg
    python viz_yolo_detect.py --image path/to/img.jpg  # 指定图片
    python viz_yolo_detect.py --pdf path/to/resume.pdf # 从 PDF 渲染后检测
    python viz_yolo_detect.py --pdf path/to/resume.pdf --page 0  # 指定页码

输出:
    在 data/resume/ 下生成 *_yolo_detected.jpg 标注图
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 路径设置
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]

DEFAULT_IMAGE = ROOT / "data" / "resume" / "简历.jpg"
DEFAULT_MODEL = ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx"
OUTPUT_DIR = ROOT / "data" / "resume"

# ---------------------------------------------------------------------------
# 直接用 importlib 加载 YOLOLayoutDetector (绕过包导入问题)
# ---------------------------------------------------------------------------
def load_yolo_detector_class():
    """用 importlib 从文件路径直接加载 YOLOLayoutDetector"""
    module_path = SCRIPT_DIR / "layout" / "yolo_layout_detector.py"
    if not module_path.exists():
        raise FileNotFoundError(f"找不到 YOLOv10 检测器模块: {module_path}")

    spec = importlib.util.spec_from_file_location(
        "yolo_layout_detector", str(module_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["yolo_layout_detector"] = mod  # 必须先注册，否则 @dataclass 报错
    spec.loader.exec_module(mod)
    return mod.YOLOLayoutDetector, mod.LayoutRegion


# ---------------------------------------------------------------------------
# 颜色 & 绘制
# ---------------------------------------------------------------------------
COLORS = [
    (255, 87, 51),   # 红橙
    (51, 255, 87),   # 绿
    (51, 87, 255),   # 蓝
    (255, 215, 0),   # 金
    (148, 103, 189), # 紫
    (255, 127, 80),  # 珊瑚
    (0, 206, 209),   # 青
    (255, 20, 147),  # 粉
    (50, 205, 50),   # 浅绿
    (30, 144, 255),  # 天蓝
]


def draw_regions(image: Image.Image, regions, title: str = "") -> Image.Image:
    """在图片上绘制 YOLOv10 检测到的版面区域"""
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    # 尝试加载字体
    try:
        font = ImageFont.truetype("arial.ttf", 18)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except (IOError, OSError):
        font = ImageFont.load_default()
        font_small = font

    img_w, img_h = annotated.size

    for i, region in enumerate(regions):
        x1, y1, x2, y2 = region.bbox
        color = COLORS[i % len(COLORS)]
        conf = region.confidence

        # 画框 (3px 粗边框)
        for offset in range(3):
            draw.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=color,
            )

        # 标签背景
        label = f"R{i+1}  conf={conf:.2f}"
        bbox_text = draw.textbbox((0, 0), label, font=font)
        text_w = bbox_text[2] - bbox_text[0]
        text_h = bbox_text[3] - bbox_text[1]

        label_y = max(0, y1 - text_h - 6)
        draw.rectangle(
            [x1, label_y, x1 + text_w + 8, label_y + text_h + 6],
            fill=color,
        )
        draw.text((x1 + 4, label_y + 2), label, fill=(255, 255, 255), font=font)

        # 区域编号 (中心)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        num_label = str(i + 1)
        draw.text(
            (cx - 8, cy - 10), num_label,
            fill=color, font=font,
        )

    # 标题
    if title:
        draw.rectangle([0, 0, img_w, 32], fill=(0, 0, 0, 180))
        draw.text((10, 6), title, fill=(255, 255, 255), font=font)

    return annotated


def render_pdf_page(pdf_path: Path, page_idx: int = 0, dpi: int = 144) -> np.ndarray:
    """将 PDF 页面渲染为 RGB numpy array"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("需要 PyMuPDF: pip install pymupdf")

    doc = fitz.open(str(pdf_path))
    if page_idx >= len(doc):
        raise ValueError(f"页码 {page_idx} 超出范围 (共 {len(doc)} 页)")

    page = doc[page_idx]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    doc.close()
    return image


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="YOLOv10 版面检测可视化")
    parser.add_argument("--image", type=Path, default=None, help="输入图片路径")
    parser.add_argument("--pdf", type=Path, default=None, help="输入 PDF 路径")
    parser.add_argument("--page", type=int, default=0, help="PDF 页码 (0-based)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="ONNX 模型路径")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    parser.add_argument("--dpi", type=int, default=144, help="PDF 渲染 DPI")
    args = parser.parse_args()

    # 确定输入源
    if args.pdf:
        print(f"[1/4] 渲染 PDF: {args.pdf.name} (page={args.page}, dpi={args.dpi})")
        image_np = render_pdf_page(args.pdf, args.page, args.dpi)
        source_name = f"{args.pdf.stem}_p{args.page}"
    elif args.image:
        print(f"[1/4] 加载图片: {args.image.name}")
        image_np = np.array(Image.open(args.image).convert("RGB"))
        source_name = args.image.stem
    else:
        if DEFAULT_IMAGE.exists():
            print(f"[1/4] 加载默认图片: {DEFAULT_IMAGE.name}")
            image_np = np.array(Image.open(DEFAULT_IMAGE).convert("RGB"))
            source_name = DEFAULT_IMAGE.stem
        else:
            # 回退到 PDF
            pdf_fallback = ROOT / "data" / "resume" / "简历样本_1.pdf"
            print(f"[1/4] 无默认图片，渲染 PDF: {pdf_fallback.name}")
            image_np = render_pdf_page(pdf_fallback, 0, args.dpi)
            source_name = f"{pdf_fallback.stem}_p0"

    h, w = image_np.shape[:2]
    print(f"       图像尺寸: {w} x {h}")

    # 加载模型
    print(f"[2/4] 加载 YOLOv10 模型: {args.model.name}")
    YOLOLayoutDetector, LayoutRegion = load_yolo_detector_class()

    if not args.model.exists():
        print(f"  ✗ 模型文件不存在: {args.model}")
        return 1

    detector = YOLOLayoutDetector(args.model)

    # 检测
    print(f"[3/4] 运行版面检测 (conf_threshold={args.conf}) ...")
    t0 = time.time()
    regions = detector.detect(image_np, page=0, confidence_threshold=args.conf)
    elapsed = time.time() - t0

    print(f"       检测到 {len(regions)} 个版面区域 ({elapsed:.3f}s)")
    print()

    # 打印每个区域的详细信息
    print(f"  {'#':>3s}  {'Confidence':>10s}  {'BBox (x1,y1,x2,y2)':>40s}  {'Size':>12s}")
    print(f"  {'─'*3}  {'─'*10}  {'─'*40}  {'─'*12}")
    for i, r in enumerate(regions):
        x1, y1, x2, y2 = r.bbox
        rw, rh = x2 - x1, y2 - y1
        print(f"  {i+1:3d}  {r.confidence:10.3f}  ({x1:7.1f}, {y1:7.1f}, {x2:7.1f}, {y2:7.1f})  {rw:.0f} x {rh:.0f}")

    # 可视化
    print(f"\n[4/4] 生成标注图 ...")
    pil_image = Image.fromarray(image_np)
    title = f"YOLOv10 Layout Detection — {len(regions)} regions (conf≥{args.conf})"
    annotated = draw_regions(pil_image, regions, title=title)

    output_path = OUTPUT_DIR / f"{source_name}_yolo_detected.jpg"
    annotated.save(str(output_path), quality=95)
    print(f"       保存至: {output_path}")
    print(f"\n  ✓ 完成! 用图片查看器打开 {output_path.name} 查看检测效果")

    return 0


if __name__ == "__main__":
    sys.exit(main())
