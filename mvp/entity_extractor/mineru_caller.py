"""
MinerU PDF 解析调用模块

功能：
1. 调用 MinerU 3.x CLI API 解析 PDF
2. 支持三种输出格式：V2 (content_list_v2.json)、V1 (content_list.json)、Middle (middle.json)
3. 提供统一的数据加载接口

使用方法：
    from mineru_caller import MinerUCaller

    caller = MinerUCaller(output_dir="./output")
    result = caller.parse(Path("input.pdf"))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MinerUResult:
    """MinerU解析结果"""
    engine: str = "MinerU (PDF-Extract-Kit)"
    route: str = "mineru_pipeline_auto"
    source_json: Optional[Path] = None
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    format_used: str = ""  # "v2", "v1", "middle", ""
    raw_data: Optional[Dict[str, Any]] = None  # 原始完整数据

    def has_data(self) -> bool:
        """检查是否有有效的解析结果"""
        return len(self.blocks) > 0 and self.source_json is not None


class MinerUCaller:
    """
    MinerU PDF 解析调用器

    提供两种使用模式：
    1. 解析模式：调用 MinerU API 解析新 PDF
    2. 加载模式：加载已有的解析结果
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        force_reparse: bool = False,
    ):
        """
        初始化 MinerU 调用器

        Args:
            output_dir: MinerU 输出目录，默认为 ./mineru_output
            force_reparse: 是否强制重新解析（忽略已有结果）
        """
        self.output_dir = output_dir or Path("./mineru_output")
        self.force_reparse = force_reparse
        self._mineru_available = None

    def _check_mineru(self) -> bool:
        """检查 MinerU 是否可用"""
        if self._mineru_available is not None:
            return self._mineru_available

        try:
            from mineru.cli.common import do_parse, read_fn
            from mineru.utils.enum_class import MakeMode
            self._mineru_available = True
            self._do_parse = do_parse
            self._read_fn = read_fn
            self._MakeMode = MakeMode
            logger.info("MinerU 检查通过")
            return True
        except ImportError as e:
            logger.warning("MinerU 不可用: %s", e)
            self._mineru_available = False
            return False

    def parse(
        self,
        pdf_path: Path,
        output_subdir: Optional[str] = None,
    ) -> MinerUResult:
        """
        解析 PDF 文件

        Args:
            pdf_path: PDF 文件路径
            output_subdir: 输出子目录名（默认为 PDF 文件名）

        Returns:
            MinerUResult: 解析结果
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        # 确定输出目录
        if output_subdir:
            output_dir = self.output_dir / output_subdir
        else:
            output_dir = self.output_dir / pdf_path.stem

        # 检查是否已有解析结果
        if not self.force_reparse:
            existing = self._find_existing_results(output_dir)
            if existing:
                logger.info("发现已有解析结果，直接加载: %s", output_dir)
                return existing

        # 调用 MinerU 解析
        if not self._check_mineru():
            logger.error("MinerU 不可用，无法解析 PDF")
            return MinerUResult()

        return self._call_mineru(pdf_path, output_dir)

    def _call_mineru(self, pdf_path: Path, output_dir: Path) -> MinerUResult:
        """调用 MinerU API 解析 PDF"""
        logger.info("开始调用 MinerU 解析: %s", pdf_path)

        try:
            pdf_bytes = self._read_fn(str(pdf_path), "pdf")
            pdf_name = pdf_path.stem

            output_dir.mkdir(parents=True, exist_ok=True)

            self._do_parse(
                output_dir=str(output_dir),
                pdf_file_names=[pdf_name],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=["ch"],  # 中文
                backend="pipeline",
                parse_method="auto",
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_md=False,
                f_dump_middle_json=True,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_dump_content_list=True,
                f_make_md_mode=self._MakeMode.MM_MD,
            )

            logger.info("MinerU 解析完成，正在加载结果...")

            # 加载解析结果
            return self._load_from_dir(output_dir)

        except Exception as e:
            logger.error("MinerU 解析失败: %s", e)
            raise

    def _find_existing_results(self, output_dir: Path) -> Optional[MinerUResult]:
        """查找已有的解析结果"""
        # 优先找 V2
        v2_files = list(output_dir.glob("**/*_content_list_v2.json"))
        if v2_files:
            return self._load_single_format(v2_files[0], "v2")

        # 再找 V1
        v1_files = list(output_dir.glob("**/*_content_list.json"))
        if v1_files:
            return self._load_single_format(v1_files[0], "v1")

        # 最后找 Middle
        middle_files = list(output_dir.glob("**/*_middle.json"))
        if middle_files:
            return self._load_single_format(middle_files[0], "middle")

        return None

    def _load_from_dir(self, output_dir: Path) -> MinerUResult:
        """从目录加载 MinerU 结果"""
        # 优先 V2
        v2_files = list(output_dir.glob("**/*_content_list_v2.json"))
        if v2_files:
            return self._load_single_format(v2_files[0], "v2")

        # V1
        v1_files = list(output_dir.glob("**/*_content_list.json"))
        if v1_files:
            return self._load_single_format(v1_files[0], "v1")

        # Middle
        middle_files = list(output_dir.glob("**/*_middle.json"))
        if middle_files:
            return self._load_single_format(middle_files[0], "middle")

        logger.warning("MinerU 解析完成但未找到结果文件: %s", output_dir)
        return MinerUResult()

    def _load_single_format(
        self,
        json_path: Path,
        format_name: str,
    ) -> MinerUResult:
        """加载单个格式的结果"""
        logger.info("加载 %s 格式: %s", format_name, json_path)

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                import json
                raw_data = json.load(f)

            blocks = self._flatten_blocks(raw_data, format_name)

            return MinerUResult(
                engine="MinerU (PDF-Extract-Kit)",
                route="mineru_pipeline_auto",
                source_json=json_path,
                blocks=blocks,
                format_used=format_name,
                raw_data=raw_data,
            )
        except Exception as e:
            logger.error("加载 %s 格式失败: %s", format_name, e)
            return MinerUResult()

    def _flatten_blocks(self, data: Any, format_name: str) -> List[Dict[str, Any]]:
        """根据格式类型扁平化块数据"""
        if format_name == "v2":
            return self._flatten_v2(data)
        elif format_name == "v1":
            return self._flatten_v1(data)
        elif format_name == "middle":
            return self._flatten_middle(data)
        else:
            return []

    def _flatten_v2(self, payload: Any) -> List[Dict[str, Any]]:
        """扁平化 V2 格式数据"""
        items: List[Dict[str, Any]] = []

        if not isinstance(payload, list):
            return items

        # V2 可能是二维数组
        for part in payload:
            if isinstance(part, list):
                for x in part:
                    if isinstance(x, dict):
                        items.append(self._normalize_v2_item(x))
            elif isinstance(part, dict):
                items.append(self._normalize_v2_item(part))

        return items

    def _normalize_v2_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """标准化 V2 单个条目"""
        item_type = item.get("type", "")
        content = item.get("content", {})

        # 提取文本内容
        text = ""
        if item_type == "title":
            chunks = content.get("title_content", [])
            parts = []
            for c in chunks:
                if isinstance(c, dict):
                    t = c.get("content")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            text = "".join(parts)
        elif item_type == "paragraph":
            chunks = content.get("paragraph_content", [])
            parts = []
            for c in chunks:
                if isinstance(c, dict):
                    t = c.get("content")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            text = "".join(parts)
        else:
            # 其他类型，尝试直接提取
            for key in ("text", "content"):
                v = content.get(key) if isinstance(content, dict) else content
                if isinstance(v, str) and v.strip():
                    text = v.strip()
                    break

        return {
            "type": item_type,
            "text": text,
            "bbox": item.get("bbox"),
            "level": content.get("level") if isinstance(content, dict) else None,
            "page_idx": item.get("page_idx", 0),
        }

    def _flatten_v1(self, payload: Any) -> List[Dict[str, Any]]:
        """扁平化 V1 格式数据"""
        items: List[Dict[str, Any]] = []

        if not isinstance(payload, list):
            return items

        for item in payload:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type", "")
            text = ""

            # 提取文本
            for key in ("text", "content", "value", "raw_text"):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    text = v.strip()
                    break

            items.append({
                "type": item_type,
                "text": text,
                "bbox": item.get("bbox"),
                "text_level": item.get("text_level"),
                "page_idx": item.get("page_idx", 0),
            })

        return items

    def _flatten_middle(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """扁平化 Middle 格式数据"""
        items: List[Dict[str, Any]] = []

        if not isinstance(payload, dict):
            return items

        for page_info in payload.get("pdf_info", []):
            page_idx = page_info.get("page_idx", 0)

            for pb in page_info.get("preproc_blocks", []):
                if pb.get("type") not in ("title", "text"):
                    continue

                # 提取文本
                text_parts = []
                for line in pb.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("type") == "text":
                            c = span.get("content", "")
                            if isinstance(c, str) and c.strip():
                                text_parts.append(c.strip())

                text = " ".join(text_parts).strip()
                if not text:
                    continue

                items.append({
                    "type": pb.get("type"),
                    "text": text,
                    "bbox": pb.get("bbox"),
                    "score": pb.get("score", 0.0),
                    "level": pb.get("level"),
                    "page_idx": page_idx,
                })

        return items

    def load_existing(self, output_dir: Path) -> MinerUResult:
        """
        加载已有的解析结果目录

        Args:
            output_dir: MinerU 输出目录

        Returns:
            MinerUResult: 解析结果
        """
        return self._find_existing_results(output_dir)

    def get_all_formats(self, output_dir: Path) -> Dict[str, MinerUResult]:
        """
        获取所有三种格式的结果

        Returns:
            Dict[str, MinerUResult]: 格式名到结果的映射
        """
        results = {}

        # V2
        v2_files = list(output_dir.glob("**/*_content_list_v2.json"))
        if v2_files:
            results["v2"] = self._load_single_format(v2_files[0], "v2")

        # V1
        v1_files = list(output_dir.glob("**/*_content_list.json"))
        if v1_files:
            results["v1"] = self._load_single_format(v1_files[0], "v1")

        # Middle
        middle_files = list(output_dir.glob("**/*_middle.json"))
        if middle_files:
            results["middle"] = self._load_single_format(middle_files[0], "middle")

        return results


def demo():
    """演示如何使用 MinerUCaller"""
    import json

    # 示例：加载已有的解析结果
    caller = MinerUCaller()

    existing_dir = Path(__file__).resolve().parents[2] / "data" / "mineru_output" / "\u7b80\u5386" / "auto"

    if existing_dir.exists():
        print(f"加载已有解析结果: {existing_dir}")

        # 获取所有格式
        all_formats = caller.get_all_formats(existing_dir)

        print(f"\n发现 {len(all_formats)} 种格式:")
        for fmt, result in all_formats.items():
            print(f"\n=== {fmt.upper()} 格式 ===")
            print(f"  源文件: {result.source_json}")
            print(f"  块数量: {len(result.blocks)}")
            print(f"  前3个块:")
            for i, block in enumerate(result.blocks[:3]):
                text = block.get("text", "")[:50]
                print(f"    {i+1}. [{block.get('type')}] {text}...")

        # 对比 V1 和 V2 的差异
        if "v1" in all_formats and "v2" in all_formats:
            v1_blocks = all_formats["v1"].blocks
            v2_blocks = all_formats["v2"].blocks

            print(f"\n=== V1 vs V2 对比 ===")
            print(f"V1 块数: {len(v1_blocks)}")
            print(f"V2 块数: {len(v2_blocks)}")
            print(f"差异: {len(v1_blocks) - len(v2_blocks)} 个块")

    else:
        print(f"目录不存在: {existing_dir}")
        print("尝试解析新 PDF...")

        # 解析新 PDF
        pdf_path = Path(__file__).parent.parent.parent / "docs" / "简历样本_10.pdf"
        if pdf_path.exists():
            result = caller.parse(pdf_path)
            print(f"\n解析结果:")
            print(f"  格式: {result.format_used}")
            print(f"  块数: {len(result.blocks)}")
        else:
            print(f"PDF 文件不存在: {pdf_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo()