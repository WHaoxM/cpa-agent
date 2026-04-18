#!/usr/bin/env python3
"""测试图片简历的 YOLOv10 + OCR 通道"""
import sys
from pathlib import Path

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
IMAGE_PATH = DATA_DIR / "resume" / "简历.jpg"

from input_router import InputRouter

def main():
    print(f"测试图片简历: {IMAGE_PATH}")
    print(f"文件存在: {IMAGE_PATH.exists()}")
    print("-" * 60)
    
    # 初始化 InputRouter
    router = InputRouter(
        yolo_model_path=ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx",
        mineru_base=DATA_DIR / "mineru_output",
    )
    
    try:
        result = router.route(IMAGE_PATH, doc_type="resume")
        print(f"\n通道: {result.channel}")
        print(f"格式: {result.input_format}")
        print(f"Blocks: {len(result.blocks)}")
        
        if result.blocks:
            print(f"\n前 3 个 blocks:")
            for b in result.blocks[:3]:
                print(f"  [{b.block_id}] {b.block_type}: {b.text[:50]}...")
        
        if result.metadata:
            print(f"\n元数据:")
            for k, v in result.metadata.items():
                print(f"  {k}: {v}")
                
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
