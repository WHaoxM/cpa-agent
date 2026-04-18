#!/usr/bin/env python3
"""
简历 MinerU 管道解析脚本
扫描 data/resume，使用物理层管道解析到 blocks，输出 JSON（不含 LLM 调用）
"""
import json
import sys
import time
from pathlib import Path
from typing import List, Dict, Any
from enum import Enum

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
OUTPUT_FILE = DATA_DIR / "resume_blocks_output.json"

from input_router import InputRouter


class EnumEncoder(json.JSONEncoder):
    """处理枚举类型的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, '_asdict'):
            return obj._asdict()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        return super().default(obj)


def parse_resume(file_path: Path, router: InputRouter) -> Dict[str, Any]:
    """解析单个简历到 blocks 级别"""
    result = {
        "file": str(file_path.relative_to(ROOT)),
        "file_name": file_path.name,
        "file_size": file_path.stat().st_size,
        "status": "FAIL",
        "error": None,
        "parse_result": None,
    }
    
    t0 = time.time()
    try:
        # 使用 InputRouter 解析（只到物理层，不含 LLM）
        route_result = router.route(file_path, doc_type="resume")
        
        # RouteResult 转 dict
        if hasattr(route_result, '_asdict'):
            parse_result = route_result._asdict()
        elif hasattr(route_result, '__dict__'):
            parse_result = route_result.__dict__
        else:
            parse_result = dict(route_result) if isinstance(route_result, dict) else {"raw": str(route_result)}
        
        elapsed = time.time() - t0
        
        # 提取 blocks（不含 LLM 后续处理）
        blocks = parse_result.get("document_blocks", [])
        
        result.update({
            "status": "SUCCESS",
            "elapsed_seconds": round(elapsed, 3),
            "channel": parse_result.get("channel"),
            "input_format": parse_result.get("input_format"),
            "structured": parse_result.get("structured"),
            "block_count": len(blocks),
            "blocks": [
                {
                    "block_id": b.get("block_id"),
                    "block_type": b.get("block_type"),
                    "confidence": b.get("confidence"),
                    "heading": b.get("heading", "")[:100],
                    "text": b.get("text", "")[:200],
                    "text_full_length": len(b.get("text", "")),
                    "source": b.get("source"),
                }
                for b in blocks
            ],
            "parse_coverage": parse_result.get("parse_coverage"),
            "metadata": parse_result.get("metadata"),
        })
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def main():
    # 初始化 InputRouter
    mineru_base = DATA_DIR / "mineru_output"
    router = InputRouter(
        yolo_model_path=ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx",
        mineru_base=mineru_base,
    )
    
    # 扫描所有简历文件
    resume_files = list(RESUME_DIR.glob("*.pdf"))
    resume_files.extend(RESUME_DIR.glob("*.jpg"))
    resume_files.extend(RESUME_DIR.glob("*.png"))
    resume_files.extend(RESUME_DIR.glob("*.docx"))
    resume_files.sort()
    
    print(f"发现 {len(resume_files)} 个简历文件")
    print(f"MinerU 输出目录: {mineru_base}")
    print("-" * 60)
    
    results = []
    success_count = 0
    
    for i, file_path in enumerate(resume_files, 1):
        print(f"\n[{i}/{len(resume_files)}] {file_path.name}")
        r = parse_resume(file_path, router)
        results.append(r)
        
        if r["status"] == "SUCCESS":
            success_count += 1
            blocks = r.get("blocks", [])
            print(f"  ✓ {r['channel']} | {r['input_format']} | {r['block_count']} blocks")
            if blocks:
                for b in blocks[:3]:
                    print(f"    - [{b['block_id']}] {b['block_type']}: {b['heading'][:30] or b['text'][:30]}...")
                if len(blocks) > 3:
                    print(f"    ... 还有 {len(blocks)-3} 个 blocks")
        else:
            print(f"  ✗ {r['error'][:60]}")
    
    # 保存结果
    output_data = {
        "summary": {
            "total_files": len(results),
            "success": success_count,
            "failed": len(results) - success_count,
            "mineru_output_dir": str(mineru_base),
            "resume_input_dir": str(RESUME_DIR),
        },
        "results": results,
    }
    
    OUTPUT_FILE.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2, cls=EnumEncoder),
        encoding="utf-8"
    )
    
    print("\n" + "=" * 60)
    print(f"完成: {success_count}/{len(results)} 成功")
    print(f"输出: {OUTPUT_FILE}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
