#!/usr/bin/env python3
"""
物理解析层 (Phase 1) 测试脚本
测试 PDF、图片通道的物理解析效果，输出详细结果到 data/physical_parse_test.json

用法:
    cd mvp/entity_extractor
    python test_physical_layer.py
"""

import json
import logging
import sys
import time
from pathlib import Path

# 路径设置
work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "physical_parse_test.json"

# 默认测试文件
DEFAULT_PDF = DATA_DIR / "resume" / "简历样本_1.pdf"
DEFAULT_IMAGE = DATA_DIR / "resume" / "简历.jpg"
DEFAULT_MINERU_BASE = DATA_DIR / "mineru_output"
DEFAULT_YOLO_MODEL = ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_pdf_channel(router, pdf_path: Path) -> dict:
    """测试 PDF 通道"""
    logger.info(f"=" * 60)
    logger.info(f"测试 PDF 通道: {pdf_path.name}")
    logger.info(f"=" * 60)

    if not pdf_path.exists():
        return {"status": "SKIP", "reason": f"文件不存在: {pdf_path}"}

    t0 = time.time()
    try:
        result = router.route(pdf_path, doc_type="resume")
        elapsed = time.time() - t0

        blocks_data = []
        for b in result.blocks or []:
            blocks_data.append({
                "block_id": b.block_id,
                "block_type": b.block_type,
                "confidence": b.confidence,
                "heading": b.heading,
                "text_preview": b.text[:200] + "..." if len(b.text) > 200 else b.text,
                "text_length": len(b.text),
            })

        output = {
            "status": "PASS",
            "file_name": pdf_path.name,
            "file_size": pdf_path.stat().st_size,
            "elapsed_seconds": round(elapsed, 3),
            "channel": result.channel.value,
            "input_format": result.input_format.value,
            "block_count": len(result.blocks or []),
            "blocks": blocks_data,
        }
        logger.info(f"✓ PDF 解析成功: {len(blocks_data)} 个 blocks ({elapsed:.2f}s)")
        return output

    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"✗ PDF 解析失败 ({elapsed:.2f}s): {e}")
        return {"status": "FAIL", "error": str(e), "elapsed_seconds": round(elapsed, 3)}


def test_image_channel(router, image_path: Path) -> dict:
    """测试图片通道"""
    logger.info(f"=" * 60)
    logger.info(f"测试图片通道: {image_path.name}")
    logger.info(f"=" * 60)

    if not image_path.exists():
        return {"status": "SKIP", "reason": f"文件不存在: {image_path}"}

    t0 = time.time()
    try:
        result = router.route(image_path, doc_type="resume")
        elapsed = time.time() - t0

        blocks_data = []
        for b in result.blocks or []:
            blocks_data.append({
                "block_id": b.block_id,
                "block_type": b.block_type,
                "confidence": b.confidence,
                "heading": b.heading,
                "text_preview": b.text[:200] + "..." if len(b.text) > 200 else b.text,
                "text_length": len(b.text),
            })

        output = {
            "status": "PASS",
            "file_name": image_path.name,
            "elapsed_seconds": round(elapsed, 3),
            "channel": result.channel.value,
            "input_format": result.input_format.value,
            "block_count": len(result.blocks or []),
            "yolo_available": result.metadata.get("yolo_available", False),
            "ocr_engine": result.metadata.get("ocr_engine", "unknown"),
            "image_size": result.metadata.get("image_size", "unknown"),
            "blocks": blocks_data,
        }
        logger.info(f"✓ 图片解析成功: {len(blocks_data)} 个 blocks, YOLO={output['yolo_available']} ({elapsed:.2f}s)")
        return output

    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"✗ 图片解析失败 ({elapsed:.2f}s): {e}")
        import traceback
        traceback.print_exc()
        return {"status": "FAIL", "error": str(e), "elapsed_seconds": round(elapsed, 3)}


def main():
    from input_router import InputRouter

    # 初始化
    logger.info("初始化 InputRouter...")
    yolo_path = DEFAULT_YOLO_MODEL if DEFAULT_YOLO_MODEL.exists() else None
    mineru_base = DEFAULT_MINERU_BASE if DEFAULT_MINERU_BASE.exists() else None

    logger.info(f"YOLOv10 模型: {yolo_path or 'N/A'}")
    logger.info(f"MinerU 基础路径: {mineru_base or 'N/A'}")

    router = InputRouter(
        yolo_model_path=yolo_path,
        mineru_base=mineru_base,
    )

    # 运行测试
    results = {
        "test_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "test_files": {
            "pdf": str(DEFAULT_PDF),
            "image": str(DEFAULT_IMAGE),
        },
        "pdf_channel": test_pdf_channel(router, DEFAULT_PDF),
        "image_channel": test_image_channel(router, DEFAULT_IMAGE),
    }

    # 保存结果
    OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"\n测试结果已保存到: {OUTPUT_FILE}")

    # 打印汇总
    print("\n" + "=" * 60)
    print("物理解析层测试汇总")
    print("=" * 60)
    for channel, data in results.items():
        if channel in ["test_timestamp", "test_files"]:
            continue
        status = data.get("status", "UNKNOWN")
        symbol = "✓" if status == "PASS" else ("⊘" if status == "SKIP" else "✗")
        print(f"  {symbol} {channel:15s}: {status}")
        if status == "PASS":
            print(f"      blocks: {data.get('block_count', 0)}, time: {data.get('elapsed_seconds', 0):.2f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
