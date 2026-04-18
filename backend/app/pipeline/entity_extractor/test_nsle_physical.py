#!/usr/bin/env python3
"""
NSLEPipeline 物理层解析测试 — 直接测试 Pipeline 中的 L0 效果

用法:
    cd mvp/entity_extractor
    python test_nsle_physical.py [pdf_path]
"""

import json
import sys
import time
from pathlib import Path
from collections import Counter

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
OUTPUT_FILE = DATA_DIR / "nsle_physical_test_report.json"

from nsle_pipeline import NSLEPipeline


def test_single_pdf(pipeline: NSLEPipeline, pdf_path: Path) -> dict:
    """使用 NSLEPipeline 测试单个 PDF 的物理层解析效果"""
    result = {
        "file": pdf_path.name,
        "status": "FAIL",
        "error": None,
        "physical_parse": {},
    }

    t0 = time.time()
    try:
        # 直接调用 pipeline 处理，观察物理层输出
        output = pipeline.process(
            file_path=str(pdf_path),
            doc_type="resume",
        )

        elapsed = time.time() - t0

        # 解析 pipeline 输出
        blocks = output.get("document_blocks", [])
        coverage = output.get("parse_coverage", {})

        # 分析 blocks
        type_counter = Counter()
        confidences = []
        for b in blocks:
            type_counter[b.get("block_type", "UNKNOWN")] += 1
            confidences.append(b.get("confidence", 0))

        result.update({
            "status": "PASS",
            "elapsed_seconds": round(elapsed, 3),
            "block_count": len(blocks),
            "block_types": dict(type_counter),
            "classification_coverage": coverage.get("block_classification_coverage", 0),
            "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
            "blocks_detail": [
                {
                    "id": b.get("block_id"),
                    "type": b.get("block_type"),
                    "conf": b.get("confidence"),
                    "heading": b.get("heading", "")[:40],
                    "text_len": len(b.get("text", "")),
                }
                for b in blocks[:20]  # 只记录前 20 个
            ],
        })

    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    # 初始化 Pipeline
    pipeline = NSLEPipeline(
        output_dir=str(DATA_DIR / "extracted"),
    )

    # 测试所有 PDF
    pdf_files = sorted(RESUME_DIR.glob("*.pdf"))
    print(f"测试 {len(pdf_files)} 个 PDF...")

    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] {pdf_path.name}")
        r = test_single_pdf(pipeline, pdf_path)
        results.append(r)

        status = "✓" if r["status"] == "PASS" else "✗"
        if r["status"] == "PASS":
            print(f"  {status} {r['block_count']} blocks, "
                  f"coverage={r['classification_coverage']:.1%}, "
                  f"time={r['elapsed_seconds']:.2f}s")
        else:
            print(f"  {status} {r['error'][:80]}")

    # 汇总统计
    passed = [r for r in results if r["status"] == "PASS"]
    failed = [r for r in results if r["status"] == "FAIL"]

    summary = {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": len(passed) / len(results) if results else 0,
        "avg_blocks": sum(r["block_count"] for r in passed) / len(passed) if passed else 0,
        "avg_coverage": sum(r["classification_coverage"] for r in passed) / len(passed) if passed else 0,
    }

    report = {
        "summary": summary,
        "details": results,
    }

    # 保存报告
    OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印汇总
    print("\n" + "=" * 60)
    print("NSLEPipeline 物理层解析测试汇总")
    print("=" * 60)
    print(f"  总样本: {summary['total']}")
    print(f"  成功: {summary['passed']} ({summary['pass_rate']:.1%})")
    print(f"  失败: {summary['failed']}")
    print(f"  平均 blocks: {summary['avg_blocks']:.1f}")
    print(f"  平均分类覆盖率: {summary['avg_coverage']:.1%}")
    print(f"\n  详细报告: {OUTPUT_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
