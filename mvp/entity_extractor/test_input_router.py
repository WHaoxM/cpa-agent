#!/usr/bin/env python3
"""
Input Router 一键测试脚本
测试所有输入通道的物理解析效果

使用方式:
    cd d:\AGeneral Workspace\cpa-agent\mvp\entity_extractor
    python test_input_router.py

可选参数:
    --pdf   PATH    指定 PDF 简历路径
    --docx  PATH    指定 Word 简历路径
    --image PATH    指定图片简历路径
    --json  PATH    指定 JD JSON 路径
    --txt   TEXT    指定 JD 纯文本
    --all           测试所有通道 (默认)
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# 确保当前目录在 sys.path
work_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]  # cpa-agent 根目录

# 默认测试文件
DEFAULT_PDF = ROOT / "data" / "resume" / "简历样本_1.pdf"
DEFAULT_JD_JSON = ROOT / "data" / "jd" / "jd_data.json"
DEFAULT_MINERU_BASE = ROOT / "data" / "mineru_output"
DEFAULT_YOLO_MODEL = ROOT / "model" / "SmartResume" / "yolov10" / "best.onnx"

# 颜色输出
class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    FAIL = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"
    BLUE = "\033[94m"


def print_header(title: str):
    print(f"\n{'='*70}")
    print(f"{C.BOLD}{C.BLUE}  {title}{C.END}")
    print(f"{'='*70}")


def print_result(result, max_blocks: int = 5):
    """打印 RouteResult 的详细信息"""
    print(f"\n  {C.BOLD}通道{C.END}:     {result.channel.value}")
    print(f"  {C.BOLD}格式{C.END}:     {result.input_format.value}")
    print(f"  {C.BOLD}类型{C.END}:     {result.doc_type}")
    print(f"  {C.BOLD}结构化{C.END}:   {result.is_structured}")

    if result.metadata:
        print(f"  {C.BOLD}元数据{C.END}:")
        for k, v in result.metadata.items():
            print(f"    {k}: {v}")

    if result.blocks is not None:
        print(f"\n  {C.BOLD}Blocks ({len(result.blocks)} 个):{C.END}")
        for i, block in enumerate(result.blocks[:max_blocks]):
            bt = block.block_type
            conf = block.confidence
            text_preview = block.text[:80].replace("\n", " ")
            heading = f" [{block.heading}]" if block.heading else ""
            color = C.OK if conf >= 0.7 else (C.WARN if conf >= 0.5 else C.FAIL)
            print(f"    [{i+1}] {color}{bt:20s}{C.END} conf={conf:.2f}{heading}")
            print(f"        {text_preview}...")
        if len(result.blocks) > max_blocks:
            print(f"    ... 还有 {len(result.blocks) - max_blocks} 个块")

    if result.structured_data is not None:
        print(f"\n  {C.BOLD}结构化数据:{C.END}")
        if isinstance(result.structured_data, dict):
            if "records" in result.structured_data:
                records = result.structured_data["records"]
                total = result.structured_data.get("total", len(records))
                print(f"    共 {total} 条记录")
                for i, rec in enumerate(records[:3]):
                    pos = rec.get("position_name", "?")
                    company = rec.get("company_name", "?")[:20]
                    is_it = rec.get("is_it_position", "?")
                    print(f"    [{i+1}] {pos} @ {company}  IT={is_it}")
                if total > 3:
                    print(f"    ... 还有 {total - 3} 条")
            else:
                for k, v in list(result.structured_data.items())[:5]:
                    val_str = str(v)[:60]
                    print(f"    {k}: {val_str}")


def test_pdf(router, pdf_path: Path):
    """测试 PDF 通道"""
    print_header(f"PDF 通道: {pdf_path.name}")

    if not pdf_path.exists():
        print(f"  {C.FAIL}文件不存在: {pdf_path}{C.END}")
        return False

    t0 = time.time()
    try:
        result = router.route(pdf_path, doc_type="resume")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def test_docx(router, docx_path: Path):
    """测试 Word 通道"""
    print_header(f"Word 通道: {docx_path.name}")

    if not docx_path.exists():
        print(f"  {C.WARN}跳过: 文件不存在 {docx_path}{C.END}")
        print(f"  提示: 将任意 .docx 简历放到 data/resume/ 目录下")
        return None

    t0 = time.time()
    try:
        result = router.route(docx_path, doc_type="resume")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def test_image(router, image_path: Path):
    """测试图片通道"""
    print_header(f"图片通道: {image_path.name}")

    if not image_path.exists():
        print(f"  {C.WARN}跳过: 文件不存在 {image_path}{C.END}")
        print(f"  提示: 将简历截图/扫描件放到 data/resume/ 目录下")
        return None

    t0 = time.time()
    try:
        result = router.route(image_path, doc_type="resume")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def test_jd_json(router, json_path: Path):
    """测试 JD JSON 结构化通道"""
    print_header(f"JD JSON 通道: {json_path.name}")

    if not json_path.exists():
        print(f"  {C.FAIL}文件不存在: {json_path}{C.END}")
        return False

    t0 = time.time()
    try:
        result = router.route(json_path, doc_type="jd")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def test_jd_text(router, text: str):
    """测试 JD 纯文本通道"""
    print_header("JD 纯文本通道")

    t0 = time.time()
    try:
        result = router.route_text(text, doc_type="jd")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def test_form(router):
    """测试表单通道"""
    print_header("表单通道 (结构化绕过)")

    form_data = {
        "name": "张三",
        "school": "四川大学",
        "degree": "硕士",
        "major": "计算机科学与技术",
        "skills": ["Python", "PyTorch", "NLP", "知识图谱"],
        "internship": [{"company": "腾讯", "position": "NLP算法实习生", "months": 6}],
    }

    t0 = time.time()
    try:
        result = router.route_form(form_data, doc_type="resume")
        elapsed = time.time() - t0
        print(f"  {C.OK}✓ 成功{C.END} ({elapsed:.2f}s)")
        print_result(result)
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {C.FAIL}✗ 失败 ({elapsed:.2f}s): {e}{C.END}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Input Router 一键测试")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--docx", type=Path, default=None)
    parser.add_argument("--image", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=DEFAULT_JD_JSON)
    parser.add_argument("--txt", type=str, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # 日志
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s: %(message)s")

    # 自动发现 Word 和图片文件
    resume_dir = ROOT / "data" / "resume"
    if args.docx is None:
        docx_files = list(resume_dir.glob("*.docx"))
        args.docx = docx_files[0] if docx_files else resume_dir / "resume_sample.docx"

    if args.image is None:
        img_files = []
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.bmp"]:
            img_files.extend(resume_dir.glob(ext))
        args.image = img_files[0] if img_files else resume_dir / "resume_sample.png"

    # JD 示例文本
    if args.txt is None:
        args.txt = (
            "职位名称：Python后端开发工程师\n"
            "岗位要求：\n"
            "1. 本科及以上学历，计算机相关专业\n"
            "2. 熟练掌握 Python，熟悉 FastAPI/Flask 框架\n"
            "3. 熟悉 MySQL、Redis、消息队列\n"
            "4. 有良好的编码习惯和团队协作能力\n"
            "工作内容：\n"
            "1. 负责后端服务的设计与开发\n"
            "2. 参与系统架构设计和技术选型\n"
            "3. 编写技术文档和单元测试"
        )

    # 初始化 Router
    print_header("初始化 InputRouter")
    yolo_path = DEFAULT_YOLO_MODEL if DEFAULT_YOLO_MODEL.exists() else None
    mineru_base = DEFAULT_MINERU_BASE if DEFAULT_MINERU_BASE.exists() else None

    print(f"  YOLOv10: {yolo_path or 'N/A (将跳过版面检测)'}")
    print(f"  MinerU:  {mineru_base or 'N/A (PDF 将无预解析结果)'}")

    from input_router import InputRouter
    router = InputRouter(
        yolo_model_path=yolo_path,
        mineru_base=mineru_base,
    )

    print(f"\n  支持格式: {router.get_supported_formats()}")

    # 运行测试
    results = {}

    # 1. PDF
    results["PDF"] = test_pdf(router, args.pdf)

    # 2. Word
    results["Word"] = test_docx(router, args.docx)

    # 3. Image
    results["Image"] = test_image(router, args.image)

    # 4. JD JSON
    results["JD-JSON"] = test_jd_json(router, args.json)

    # 5. JD Text
    results["JD-Text"] = test_jd_text(router, args.txt)

    # 6. Form
    results["Form"] = test_form(router)

    # 汇总
    print_header("测试汇总")
    for name, status in results.items():
        if status is True:
            icon = f"{C.OK}✓ PASS{C.END}"
        elif status is False:
            icon = f"{C.FAIL}✗ FAIL{C.END}"
        else:
            icon = f"{C.WARN}⊘ SKIP{C.END}"
        print(f"  {name:12s} {icon}")

    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    print(f"\n  {C.BOLD}总计: {passed} 通过, {failed} 失败, {skipped} 跳过{C.END}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
