"""
复制 mvp entity_extractor 到 backend/app/pipeline
"""
import shutil
import os

src = r"d:\AGeneral Workspace\cpa-agent\mvp\entity_extractor"
dst = r"d:\AGeneral Workspace\cpa-agent\backend\app\pipeline\entity_extractor"

# 创建目标目录
os.makedirs(dst, exist_ok=True)

# 复制所有文件
for item in os.listdir(src):
    src_path = os.path.join(src, item)
    dst_path = os.path.join(dst, item)
    
    if os.path.isdir(src_path):
        shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
    else:
        shutil.copy2(src_path, dst_path)
    print(f"Copied: {item}")

print("\n复制完成!")
