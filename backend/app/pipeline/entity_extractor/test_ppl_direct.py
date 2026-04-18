#!/usr/bin/env python3
"""
PhysicalParseLayer 直接测试 — 对 data/resume 所有 PDF 进行 L0 解析测试
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
MINERU_BASE = DATA_DIR / "mineru_output"
OUTPUT_FILE = DATA_DIR / "ppl_test_report.json"

from phase1_physical_layer import PhysicalParseLayer, DocumentBlock


def find_mineru_outputs(pdf_stem: str):
    """查找 PDF 对应的 MinerU 预解析文件"""
    v2 = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_content_list_v2.json"
    v1 = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_content_list.json"
    mid = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_middle.json"
    return (
        v2 if v2.exists() else None,
        v1 if v1.exists() else None,
        mid if mid.exists() else None
    )


def test_pdf(ppl: PhysicalParseLayer, pdf_path: Path) -> dict:
    """使用 PhysicalParseLayer 测试单个 PDF"""
    result = {
        "file": pdf_path.name,
        "file_size": pdf_path.stat().st_size,
        "has_preparse": False,
        "status": "FAIL",
        "error": None,
        "blocks": [],
    }

    # 检查预解析状态
    v2, v1, mid = find_mineru_outputs(pdf_path.stem)
    result["has_preparse"] = bool(v2 or v1 or mid)
    result["preparse_files"] = {
        "v2": str(v2) if v2 else None,
        "v1": str(v1) if v1 else None,
        "middle": str(mid) if mid else None,
    }

    t0 = time.time()
    try:
        # 直接调用 PhysicalParseLayer
        blocks = ppl.parse_resume_pdf(
            content_v2_path=v2,
            content_v1_path=v1,
            middle_json_path=mid,
        )

        elapsed = time.time() - t0

        # 分析 blocks
        block_data = []
        type_counter = Counter()
        for b in blocks:
            type_counter[b.block_type] += 1
            block_data.append({
                "id": b.block_id,
                "type": b.block_type,
                "conf": round(b.confidence, 2),
                "heading": b.heading[:30] if b.heading else "",
                "text_len": len(b.text),
            })

        result.update({
            "status": "PASS",
            "elapsed": round(elapsed, 3),
            "block_count": len(blocks),
            "block_types": dict(type_counter),
            "classification_coverage": round(
                sum(1 for b in blocks if b.block_type != "OTHER") / len(blocks), 3
            ) if blocks else 0,
            "blocks": block_data,
        })

    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    # 初始化 PhysicalParseLayer
    ppl = PhysicalParseLayer()

    # 测试所有 PDF
    pdf_files = sorted(RESUME_DIR.glob("*.pdf"))
    print(f"测试 {len(pdf_files)} 个 PDF...\n")

    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        r = test_pdf(ppl, pdf_path)
        results.append(r)

        status = "✓" if r["status"] == "PASS" else "✗"
        pre = "P" if r["has_preparse"] else "N"
        if r["status"] == "PASS":
            print(f"[{i:2d}] {status} [{pre}] {r['file']:20s} "
                  f"blocks={r['block_count']:2d} coverage={r['classification_coverage']:.0%}")
        else:
            print(f"[{i:2d}] {status} [{pre}] {r['file']:20s} ERROR: {r['error'][:40]}")

    # 汇总
    passed = [r for r in results if r["status"] == "PASS"]
    has_prep = [r for r in results if r["has_preparse"]]

    summary = {
        "total": len(results),
        "passed": len(passed),
        "has_preparse": len(has_prep),
        "no_preparse": len(results) - len(has_prep),
        "avg_blocks": sum(r["block_count"] for r in passed) / len(passed) if passed else 0,
        "avg_coverage": sum(r["classification_coverage"] for r in passed) / len(passed) if passed else 0,
    }

    print(f"\n{'='*60}")
    print("PhysicalParseLayer 测试汇总")
    print(f"{'='*60}")
    print(f"  总样本: {summary['total']}")
    print(f"  有预解析: {summary['has_preparse']} | 无预解析: {summary['no_preparse']}")
    print(f"  解析成功: {summary['passed']}")
    print(f"  平均 blocks: {summary['avg_blocks']:.1f}")
    print(f"  平均分类覆盖率: {summary['avg_coverage']:.1%}")
    print(f"\n  报告文件: {OUTPUT_FILE}")

    # 保存
    OUTPUT_FILE.write_text(
        json.dumps({"summary": summary, "details": results}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
