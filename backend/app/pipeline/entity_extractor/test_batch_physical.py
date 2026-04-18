#!/usr/bin/env python3
"""
批量测试物理解析层 — 遍历 data/resume/ 下所有 PDF 简历
输出统计报告到 data/physical_parse_batch_report.json

用法:
    cd mvp/entity_extractor
    python test_batch_physical.py
"""

import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
OUTPUT_FILE = DATA_DIR / "physical_parse_batch_report.json"

DEFAULT_MINERU_BASE = DATA_DIR / "mineru_output"
DEFAULT_YOLO_MODEL = ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_single_pdf(router, pdf_path: Path, mineru_base: Path) -> dict:
    """测试单个 PDF，返回详细结果"""
    from input_router import _find_mineru_outputs

    t0 = time.time()
    result = {
        "file_name": pdf_path.name,
        "file_size": pdf_path.stat().st_size,
        "status": "FAIL",
        "error": None,
        "blocks": [],
        "block_count": 0,
        "block_type_distribution": {},
        "avg_confidence": 0.0,
        "classification_coverage": 0.0,  # 非 OTHER 的比例
        "elapsed_seconds": 0.0,
        "mineru_matched": False,
    }

    try:
        # 检查 MinerU 输出是否存在
        content_v2, content_v1, middle = _find_mineru_outputs(mineru_base, pdf_path.stem)
        result["mineru_matched"] = bool(content_v2 or content_v1 or middle)
        result["mineru_paths"] = {
            "v2": str(content_v2) if content_v2 else None,
            "v1": str(content_v1) if content_v1 else None,
            "middle": str(middle) if middle else None,
        }

        # 执行路由
        route_result = router.route(pdf_path, doc_type="resume")
        elapsed = time.time() - t0

        result["status"] = "PASS"
        result["elapsed_seconds"] = round(elapsed, 3)
        result["block_count"] = len(route_result.blocks or [])

        # 分析 blocks
        blocks_data = []
        type_counter = Counter()
        conf_sum = 0.0

        for b in route_result.blocks or []:
            type_counter[b.block_type] += 1
            conf_sum += b.confidence
            blocks_data.append({
                "block_id": b.block_id,
                "block_type": b.block_type,
                "confidence": b.confidence,
                "heading": b.heading[:50] if b.heading else "",
                "text_length": len(b.text),
                "text_preview": b.text[:100] + "..." if len(b.text) > 100 else b.text,
            })

        result["blocks"] = blocks_data
        result["block_type_distribution"] = dict(type_counter)
        result["avg_confidence"] = round(conf_sum / len(route_result.blocks), 3) if route_result.blocks else 0.0

        # 分类覆盖率 (非 OTHER 的比例)
        total = len(route_result.blocks or [])
        classified = sum(1 for b in route_result.blocks or [] if b.block_type != "OTHER")
        result["classification_coverage"] = round(classified / total, 3) if total > 0 else 0.0

    except Exception as e:
        result["error"] = str(e)
        result["elapsed_seconds"] = round(time.time() - t0, 3)
        logger.error(f"✗ {pdf_path.name}: {e}")

    return result


def main():
    from input_router import InputRouter

    # 发现所有 PDF
    pdf_files = sorted(RESUME_DIR.glob("*.pdf"))
    logger.info(f"发现 {len(pdf_files)} 个 PDF 简历文件")

    if not pdf_files:
        logger.warning(f"目录为空: {RESUME_DIR}")
        return 1

    # 初始化 Router
    yolo_path = DEFAULT_YOLO_MODEL if DEFAULT_YOLO_MODEL.exists() else None
    mineru_base = DEFAULT_MINERU_BASE if DEFAULT_MINERU_BASE.exists() else None

    logger.info(f"YOLOv10: {yolo_path or 'N/A'}")
    logger.info(f"MinerU: {mineru_base or 'N/A'}")

    router = InputRouter(
        yolo_model_path=yolo_path,
        mineru_base=mineru_base,
    )

    # 批量测试
    results = []
    total_blocks = 0
    all_type_counter = Counter()
    coverage_sum = 0.0
    pass_count = 0
    fail_count = 0
    mineru_match_count = 0

    logger.info("=" * 70)
    logger.info("开始批量测试...")
    logger.info("=" * 70)

    for i, pdf_path in enumerate(pdf_files, 1):
        logger.info(f"[{i}/{len(pdf_files)}] 测试: {pdf_path.name}")
        r = test_single_pdf(router, pdf_path, mineru_base or Path())
        results.append(r)

        if r["status"] == "PASS":
            pass_count += 1
            total_blocks += r["block_count"]
            all_type_counter.update(r["block_type_distribution"])
            coverage_sum += r["classification_coverage"]
            if r["mineru_matched"]:
                mineru_match_count += 1
            logger.info(f"  ✓ {r['block_count']} blocks, coverage={r['classification_coverage']:.1%}, time={r['elapsed_seconds']:.2f}s")
        else:
            fail_count += 1
            logger.info(f"  ✗ FAIL: {r['error'][:50] if r['error'] else 'Unknown'}")

    # 汇总统计
    summary = {
        "test_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(pdf_files),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": round(pass_count / len(pdf_files), 3) if pdf_files else 0,
        "mineru_match_rate": round(mineru_match_count / len(pdf_files), 3) if pdf_files else 0,
        "total_blocks": total_blocks,
        "avg_blocks_per_resume": round(total_blocks / pass_count, 1) if pass_count > 0 else 0,
        "avg_classification_coverage": round(coverage_sum / pass_count, 3) if pass_count > 0 else 0,
        "block_type_distribution": dict(all_type_counter),
    }

    # 分类质量分析
    target_types = {"PERSONAL_INFO", "EDUCATION", "WORK_EXPERIENCE", "PROJECT_EXPERIENCE", "SKILLS_CERTS"}
    target_found = {t for t in all_type_counter if t in target_types}
    summary["target_type_coverage"] = f"{len(target_found)}/{len(target_types)} ({', '.join(target_found)})"

    report = {
        "summary": summary,
        "details": results,
    }

    # 保存报告
    OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印汇总
    print("\n" + "=" * 70)
    print("物理解析层批量测试报告")
    print("=" * 70)
    print(f"  测试文件: {summary['total_files']} 个 PDF")
    print(f"  成功率: {summary['pass_count']}/{summary['total_files']} ({summary['pass_rate']:.1%})")
    print(f"  MinerU 匹配率: {summary['mineru_match_rate']:.1%}")
    print(f"  总 blocks: {summary['total_blocks']}")
    print(f"  平均每简历: {summary['avg_blocks_per_resume']:.1f} blocks")
    print(f"  分类覆盖率: {summary['avg_classification_coverage']:.1%}")
    print(f"  目标类型覆盖: {summary['target_type_coverage']}")
    print("-" * 70)
    print("  Block 类型分布:")
    for block_type, count in sorted(all_type_counter.items(), key=lambda x: -x[1]):
        pct = count / total_blocks * 100 if total_blocks > 0 else 0
        print(f"    {block_type:20s}: {count:4d} ({pct:5.1f}%)")
    print("=" * 70)
    print(f"\n详细报告已保存: {OUTPUT_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
