"""
L0 简历解析模块 (Resume Parsing)
整合 MinerU + YOLOv10 + Qwen3-0.6B SFT 模型
支持 DeepSeek API 作为备选
"""

import io
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Union
from dataclasses import dataclass

from app.utils.logger import get_logger

logger = get_logger('cpa-agent.l0_parser')


@dataclass
class ParseResult:
    success: bool
    text: str
    blocks: List[Dict]
    metadata: Dict
    error: Optional[str] = None


class L0ResumeParser:
    """
    L0 简历解析器

    支持格式:
    - PDF 文件
    - DOCX 文件
    - TXT 文件
    - 纯文本字符串
    """

    def __init__(self, use_local_model: bool = False):
        """
        Args:
            use_local_model: 是否使用本地 Qwen3-0.6B 模型 (需要 GPU)
        """
        self.use_local_model = use_local_model
        self._router = None
        self._llm_client = None

    def parse_file(self, file_path: Union[str, Path]) -> ParseResult:
        """解析简历文件"""
        file_path = Path(file_path)

        if not file_path.exists():
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error=f"文件不存在: {file_path}"
            )

        suffix = file_path.suffix.lower()

        try:
            if suffix == ".pdf":
                return self._parse_pdf(file_path)
            elif suffix in [".docx", ".doc"]:
                return self._parse_docx(file_path)
            elif suffix == ".txt":
                return self._parse_txt(file_path)
            else:
                return ParseResult(
                    success=False,
                    text="",
                    blocks=[],
                    metadata={},
                    error=f"不支持的文件格式: {suffix}"
                )
        except Exception as e:
            logger.error(f"解析失败: {e}")
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error=str(e)
            )

    def parse_text(self, text: str, doc_type: str = "resume") -> ParseResult:
        """
        解析纯文本

        Args:
            text: 文本内容
            doc_type: resume 或 jd
        """
        if not text or not text.strip():
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error="文本为空"
            )

        blocks = self._extract_blocks_from_text(text, doc_type)

        return ParseResult(
            success=True,
            text=text,
            blocks=blocks,
            metadata={
                "length": len(text),
                "doc_type": doc_type,
                "block_count": len(blocks)
            }
        )

    def _parse_pdf(self, file_path: Path) -> ParseResult:
        """解析 PDF 文件"""
        try:
            import pymupdf

            doc = pymupdf.open(str(file_path))
            text_parts = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                text_parts.append(page.get_text())

            full_text = "\n".join(text_parts)
            doc.close()

            blocks = self._extract_blocks_from_text(full_text, "resume")

            return ParseResult(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={
                    "file_name": file_path.name,
                    "page_count": len(doc),
                    "length": len(full_text)
                }
            )

        except ImportError:
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error="需要安装 pymupdf: pip install pymupdf"
            )

    def _parse_docx(self, file_path: Path) -> ParseResult:
        """解析 DOCX 文件"""
        try:
            from docx import Document

            doc = Document(str(file_path))
            paragraphs = [p.text for p in doc.paragraphs]
            full_text = "\n".join(paragraphs)

            blocks = self._extract_blocks_from_text(full_text, "resume")

            return ParseResult(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={
                    "file_name": file_path.name,
                    "paragraph_count": len(paragraphs),
                    "length": len(full_text)
                }
            )

        except ImportError:
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error="需要安装 python-docx: pip install python-docx"
            )

    def _parse_txt(self, file_path: Path) -> ParseResult:
        """解析 TXT 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_text = f.read()

            blocks = self._extract_blocks_from_text(full_text, "resume")

            return ParseResult(
                success=True,
                text=full_text,
                blocks=blocks,
                metadata={
                    "file_name": file_path.name,
                    "length": len(full_text)
                }
            )

        except Exception as e:
            return ParseResult(
                success=False,
                text="",
                blocks=[],
                metadata={},
                error=str(e)
            )

    def _extract_blocks_from_text(self, text: str, doc_type: str) -> List[Dict]:
        """
        从文本中提取 blocks (简化版，实际可用 YOLOv10+MinerU)
        """
        blocks = []
        lines = text.split("\n")

        current_block = None
        current_lines = []

        section_patterns = {
            "resume": {
                r"技能|技术|专长": "SKILLS_CERTS",
                r"教育|学历|毕业": "EDUCATION",
                r"工作|经历|经验": "WORK_EXPERIENCE",
                r"项目": "PROJECT_EXPERIENCE",
                r"个人信息|基本信息": "PERSONAL_INFO",
            },
            "jd": {
                r"岗位|职位": "JOB_TITLE",
                r"要求|任职": "JOB_REQUIREMENTS",
                r"职责|工作内容": "JOB_DESCRIPTION",
            }
        }

        patterns = section_patterns.get(doc_type, section_patterns["resume"])

        for line in lines:
            line = line.strip()
            if not line:
                continue

            section_type = None
            for pattern, block_type in patterns.items():
                if re.search(pattern, line, re.IGNORECASE):
                    section_type = block_type
                    break

            if section_type:
                if current_block and current_lines:
                    blocks.append({
                        "block_type": current_block,
                        "text": "\n".join(current_lines),
                        "length": len("\n".join(current_lines))
                    })

                current_block = section_type
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_block and current_lines:
            blocks.append({
                "block_type": current_block,
                "text": "\n".join(current_lines),
                "length": len("\n".join(current_lines))
            })

        if not blocks:
            blocks.append({
                "block_type": "OTHER",
                "text": text,
                "length": len(text)
            })

        return blocks


def parse_resume_text(text: str) -> ParseResult:
    """便捷函数: 解析简历文本"""
    parser = L0ResumeParser()
    return parser.parse_text(text, doc_type="resume")


def parse_jd_text(text: str) -> ParseResult:
    """便捷函数: 解析 JD 文本"""
    parser = L0ResumeParser()
    return parser.parse_text(text, doc_type="jd")


if __name__ == "__main__":
    test_resume = """
    张三 | Python后端开发 | 3年经验

    个人信息
    - 手机: 138xxxx8888
    - 邮箱: zhangsan@email.com

    教育背景
    - 2015-2019 XX大学 计算机科学与技术 本科

    技能特长
    - 熟练掌握 Python、Django、Flask
    - 精通 MySQL、Redis 数据库
    - 熟悉 Docker、Kubernetes
    - 了解 Vue.js、TensorFlow

    工作经历
    - 2021-至今 某互联网公司 Python后端开发
    - 2019-2021 某科技公司 实习

    项目经验
    - 电商平台后端系统
    """

    print("=" * 50)
    print("L0 简历解析测试")
    print("=" * 50)

    result = parse_resume_text(test_resume)

    print(f"\n成功: {result.success}")
    print(f"文本长度: {result.metadata.get('length', 0)}")
    print(f"Block 数量: {result.metadata.get('block_count', 0)}")

    print("\nBlocks:")
    for i, block in enumerate(result.blocks):
        print(f"  [{i+1}] {block['block_type']}: {block['text'][:50]}...")
