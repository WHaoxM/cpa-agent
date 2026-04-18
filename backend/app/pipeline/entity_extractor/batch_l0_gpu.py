#!/usr/bin/env python3
"""GPU 批量 L0 预处理 — 一次性处理所有 PDF"""
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
RESUME_DIR = ROOT / "data" / "resume"
MINERU_BASE = ROOT / "data" / "mineru_output"

def process_one(pdf_path: Path) -> tuple:
    """处理单个 PDF"""
    try:
        cmd = [
            "magic-pdf", "-p", str(pdf_path),
            "-o", str(MINERU_BASE),
            "-m", "auto"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return (pdf_path.name, True, None)
        return (pdf_path.name, False, result.stderr[:100])
    except Exception as e:
        return (pdf_path.name, False, str(e)[:100])

def main():
    pdfs = sorted(RESUME_DIR.glob("*.pdf"))
    logger.info(f"发现 {len(pdfs)} 个 PDF，开始 GPU 批量处理...")
    
    success = fail = 0
    with ProcessPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(process_one, p): p for p in pdfs}
        for future in as_completed(futures):
            name, ok, err = future.result()
            if ok:
                success += 1
                logger.info(f"✓ {name}")
            else:
                fail += 1
                logger.error(f"✗ {name}: {err}")
    
    logger.info(f"\n完成: {success} 成功, {fail} 失败")
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
