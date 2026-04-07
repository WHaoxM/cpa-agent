#!/usr/bin/env python3
"""
L0 批量预处理 — 为所有无 MinerU 输出的 PDF 运行预解析

用法:
    cd mvp/entity_extractor
    python batch_l0_preprocess.py [--dry-run] [--limit N]
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
MINERU_BASE = DATA_DIR / "mineru_output"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def find_mineru_outputs(pdf_stem: str) -> Tuple[Path | None, Path | None, Path | None]:
    """查找 PDF 对应的 MinerU 输出文件"""
    # 标准命名格式
    v2 = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_content_list_v2.json"
    v1 = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_content_list.json"
    mid = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_middle.json"

    # 也检查旧格式 (简历/auto/简历_content_list_v2.json)
    if not v2.exists():
        v2_alt = MINERU_BASE / pdf_stem / "auto" / f"{pdf_stem}_content_list_v2.json"
        if v2_alt.exists():
            v2 = v2_alt

    return (
        v2 if v2.exists() else None,
        v1 if v1.exists() else None,
        mid if mid.exists() else None
    )


def scan_pdfs() -> List[Tuple[Path, bool]]:
    """扫描所有 PDF，返回 (路径, 是否有预解析) 列表"""
    pdf_files = sorted(RESUME_DIR.glob("*.pdf"))
    results = []
    for pdf_path in pdf_files:
        v2, v1, mid = find_mineru_outputs(pdf_path.stem)
        has_preparsed = bool(v2 or v1 or mid)
        results.append((pdf_path, has_preparsed))
    return results


def run_mineru(pdf_path: Path, output_dir: Path) -> bool:
    """为单个 PDF 运行 MinerU 预解析"""
    try:
        # 直接调用 mkg 环境的 Python 解释器运行 magic-pdf
        import sys
        mkg_python = Path(sys.executable).parent.parent / "envs" / "mkg" / "python.exe"
        if not mkg_python.exists():
            # 备选路径
            mkg_python = Path("D:/Miniconda/miniconda3/envs/mkg/python.exe")
        
        cmd = [
            str(mkg_python), "-m", "magic_pdf.tools.cli",
            "-p", str(pdf_path),
            "-o", str(output_dir),
            "-m", "auto"
        ]
        logger.info(f"运行: {mkg_python.name} -m magic_pdf.tools.cli -p {pdf_path.name} ...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode == 0:
            logger.info(f"✓ 成功: {pdf_path.name}")
            return True
        else:
            logger.error(f"✗ 失败: {pdf_path.name}")
            if result.stderr:
                logger.error(f"  stderr: {result.stderr[:400]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"✗ 超时: {pdf_path.name}")
        return False
    except Exception as e:
        logger.error(f"✗ 异常: {pdf_path.name} - {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="L0 批量预处理")
    parser.add_argument("--dry-run", action="store_true", help="只扫描，不执行")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量 (0=全部)")
    args = parser.parse_args()

    # 扫描
    logger.info("=" * 60)
    logger.info("扫描 PDF 文件...")
    logger.info("=" * 60)

    pdf_list = scan_pdfs()
    total = len(pdf_list)
    preparsed = sum(1 for _, has in pdf_list if has)
    need_parse = [p for p, has in pdf_list if not has]

    logger.info(f"总 PDF 数: {total}")
    logger.info(f"已预解析: {preparsed}")
    logger.info(f"待预解析: {len(need_parse)}")

    if args.dry_run:
        logger.info("\n[DRY RUN] 待处理文件:")
        for pdf_path in need_parse[:20]:
            logger.info(f"  - {pdf_path.name}")
        if len(need_parse) > 20:
            logger.info(f"  ... 还有 {len(need_parse) - 20} 个")
        return 0

    if not need_parse:
        logger.info("所有 PDF 已完成预解析，无需处理")
        return 0

    # 批量处理
    to_process = need_parse[:args.limit] if args.limit > 0 else need_parse
    logger.info(f"\n开始处理 {len(to_process)} 个 PDF...")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0

    for i, pdf_path in enumerate(to_process, 1):
        logger.info(f"\n[{i}/{len(to_process)}] {pdf_path.name}")
        if run_mineru(pdf_path, MINERU_BASE):
            success_count += 1
        else:
            fail_count += 1

    # 汇总
    logger.info("\n" + "=" * 60)
    logger.info("L0 批量预处理完成")
    logger.info("=" * 60)
    logger.info(f"成功: {success_count}")
    logger.info(f"失败: {fail_count}")
    logger.info(f"总计: {len(to_process)}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
