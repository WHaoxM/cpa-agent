#!/usr/bin/env python3
"""
批量 MinerU 预解析 - 对 data/resume 所有 PDF 进行 L0 解析
输出 JSON 包含 blocks，不含 LLM 调用
"""
import json
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
MINERU_OUTPUT = DATA_DIR / "mineru_output"
OUTPUT_FILE = DATA_DIR / "resume_preparse_output.json"

from mineru_caller import MinerUCaller, MinerUResult


def process_single_pdf(caller: MinerUCaller, pdf_path: Path) -> Dict[str, Any]:
    """处理单个 PDF"""
    result = {
        "file": str(pdf_path.relative_to(ROOT)),
        "file_name": pdf_path.name,
        "file_size": pdf_path.stat().st_size,
        "status": "FAIL",
        "error": None,
        "mineru_result": None,
    }
    
    t0 = time.time()
    try:
        # 调用 MinerU 解析
        mineru_result: MinerUResult = caller.parse(pdf_path)
        
        elapsed = time.time() - t0
        
        if mineru_result.has_data():
            result.update({
                "status": "SUCCESS",
                "elapsed_seconds": round(elapsed, 3),
                "format_used": mineru_result.format_used,
                "source_json": str(mineru_result.source_json.relative_to(ROOT)) if mineru_result.source_json else None,
                "block_count": len(mineru_result.blocks),
                "blocks": mineru_result.blocks,
            })
        else:
            result["error"] = "No blocks extracted"
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


def main():
    # 初始化 MinerU 调用器
    caller = MinerUCaller(
        output_dir=MINERU_OUTPUT,
        force_reparse=False,  # 有现有结果则跳过
    )
    
    # 检查 MinerU 可用性
    if not caller._check_mineru():
        print("⚠️  MinerU 不可用，尝试直接加载已有结果...")
    
    # 扫描所有 PDF
    pdf_files = sorted(RESUME_DIR.glob("*.pdf"))
    print(f"发现 {len(pdf_files)} 个 PDF 文件")
    print(f"输出目录: {MINERU_OUTPUT}")
    print("-" * 60)
    
    results = []
    success_count = 0
    has_blocks_count = 0
    
    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] {pdf_path.name}")
        r = process_single_pdf(caller, pdf_path)
        results.append(r)
        
        if r["status"] == "SUCCESS":
            success_count += 1
            blocks = r.get("blocks", [])
            if blocks:
                has_blocks_count += 1
                print(f"  ✓ {r['format_used']:3s} | {len(blocks):2d} blocks | {r['elapsed_seconds']:.2f}s")
                for b in blocks[:2]:
                    text = b.get("text", "")[:40]
                    print(f"    - [{b.get('type', '?')}] {text}...")
                if len(blocks) > 2:
                    print(f"    ... 还有 {len(blocks)-2} 个 blocks")
            else:
                print(f"  ⚠️  0 blocks")
        else:
            print(f"  ✗ {r['error'][:60]}")
    
    # 汇总
    output_data = {
        "summary": {
            "total_pdfs": len(results),
            "success": success_count,
            "has_blocks": has_blocks_count,
            "failed": len(results) - success_count,
            "mineru_output_dir": str(MINERU_OUTPUT),
            "resume_input_dir": str(RESUME_DIR),
        },
        "results": results,
    }
    
    OUTPUT_FILE.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    print("\n" + "=" * 60)
    print(f"完成: {success_count}/{len(results)} 成功")
    print(f"有 blocks: {has_blocks_count}")
    print(f"输出: {OUTPUT_FILE}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
