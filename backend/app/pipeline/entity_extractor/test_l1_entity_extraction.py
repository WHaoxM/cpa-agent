#!/usr/bin/env python3
"""
L1 实体抽取层测试
使用 NSLEPipeline 处理 L0 输出的 blocks，进行实体识别和关系抽取
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
PREPARSE_FILE = DATA_DIR / "resume_preparse_output.json"
OUTPUT_FILE = DATA_DIR / "l1_entity_extraction_output.json"

from nsle_pipeline import NSLEPipeline


def load_l0_blocks(preparse_file: Path) -> List[Dict[str, Any]]:
    """从 L0 预解析结果加载 blocks"""
    data = json.loads(preparse_file.read_text(encoding="utf-8"))
    
    results = []
    for item in data.get("results", []):
        if item.get("status") == "SUCCESS" and item.get("blocks"):
            results.append({
                "file": item["file"],
                "file_name": item["file_name"],
                "blocks": item["blocks"],
            })
    
    return results


def process_entity_extraction(pipeline: NSLEPipeline, item: Dict) -> Dict[str, Any]:
    """对单个简历进行 L1-L4 完整处理"""
    result = {
        "file": item["file"],
        "file_name": item["file_name"],
        "status": "FAIL",
        "error": None,
        "extraction_result": None,
    }
    
    t0 = time.time()
    try:
        # 构建 MinerU 输出路径
        file_stem = Path(item["file"]).stem
        mineru_dir = DATA_DIR / "mineru_output" / file_stem / f"{file_stem}" / "auto"
        
        content_v2 = mineru_dir / f"{file_stem}_content_list_v2.json"
        content_v1 = mineru_dir / f"{file_stem}_content_list.json"
        middle_json = mineru_dir / f"{file_stem}_middle.json"
        
        # 使用 NSLEPipeline.run_resume 进行完整处理 (L1-L4)
        extraction_result = pipeline.run_resume(
            content_v2_path=content_v2 if content_v2.exists() else None,
            content_v1_path=content_v1 if content_v1.exists() else None,
            middle_json_path=middle_json if middle_json.exists() else None,
            pdf_path=ROOT / item["file"],
        )
        
        elapsed = time.time() - t0
        
        # 统计实体和关系
        total_entities = sum(
            len(b["llm_output"].get("entities", [])) 
            for b in extraction_result.get("blocks", [])
        )
        total_relations = sum(
            len(b["llm_output"].get("relations", [])) 
            for b in extraction_result.get("blocks", [])
        )
        
        result.update({
            "status": "SUCCESS",
            "elapsed_seconds": round(elapsed, 3),
            "total_blocks": extraction_result["phase1_summary"]["total_blocks"],
            "target_blocks": extraction_result["phase1_summary"]["target_blocks"],
            "high_confidence": extraction_result["feedback_loop"]["high_confidence"],
            "low_confidence": extraction_result["feedback_loop"]["low_confidence"],
            "entity_count": total_entities,
            "relation_count": total_relations,
            "avg_confidence": extraction_result["metadata"]["avg_confidence"],
            "trace_id": extraction_result["contract_metadata"]["trace_id"],
        })
        
    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return result


def main():
    print("=" * 60)
    print("L1 实体抽取层测试")
    print("=" * 60)
    
    # 加载 L0 输出
    l0_items = load_l0_blocks(PREPARSE_FILE)
    print(f"\n从 L0 加载: {len(l0_items)} 个简历")
    print(f"输出文件: {OUTPUT_FILE}")
    print("-" * 60)
    
    # 初始化 NSLEPipeline
    pipeline = NSLEPipeline(
        model_path=ROOT / "model" / "SmartResume" / "Qwen3-0.6B",
        yolo_model_path=ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx",
        load_model=False,  # 不加载 LLM，只测试实体抽取逻辑
    )
    
    # 测试前 5 个简历
    test_items = l0_items[:5]
    print(f"\n测试前 {len(test_items)} 个简历的实体抽取:\n")
    
    results = []
    for i, item in enumerate(test_items, 1):
        print(f"[{i}/{len(test_items)}] {item['file_name']}")
        r = process_entity_extraction(pipeline, item)
        results.append(r)
        
        if r["status"] == "SUCCESS":
            print(f"  ✓ entities: {r['entity_count']}, relations: {r['relation_count']} | {r['elapsed_seconds']:.2f}s")
            print(f"    blocks: {r['target_blocks']}/{r['total_blocks']} | 高置信: {r['high_confidence']} | 低置信: {r['low_confidence']}")
        else:
            print(f"  ✗ {r['error'][:60]}")
            if "traceback" in r:
                print(f"    {r['traceback'][:200]}")
    
    # 保存结果
    output_data = {
        "summary": {
            "total_tested": len(results),
            "success": sum(1 for r in results if r["status"] == "SUCCESS"),
            "failed": sum(1 for r in results if r["status"] == "FAIL"),
            "total_entities": sum(r.get("entity_count", 0) for r in results),
            "total_relations": sum(r.get("relation_count", 0) for r in results),
        },
        "results": results,
    }
    
    OUTPUT_FILE.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    print("\n" + "=" * 60)
    print(f"完成: {output_data['summary']['success']}/{len(results)} 成功")
    print(f"总实体: {output_data['summary']['total_entities']}")
    print(f"总关系: {output_data['summary']['total_relations']}")
    print(f"输出: {OUTPUT_FILE}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
