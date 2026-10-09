"""测试语义分块功能"""

import pytest
from app.utils.chunking import (
    semantic_chunk_text,
    detect_section_boundaries,
    split_at_safe_points,
    fixed_size_chunk,
    get_optimal_chunk_size,
)


class TestSemanticChunkText:
    """测试语义分块"""

    def test_empty_text(self):
        """测试空文本"""
        chunks = semantic_chunk_text("")
        assert len(chunks) == 0

    def test_short_text(self):
        """测试短文本（不需要分块）"""
        text = "这是测试文本，长度小于块大小。"
        chunks = semantic_chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0][1] == text

    def test_text_with_sections(self):
        """测试带章节的文本"""
        text = """
教育经历
清华大学 计算机科学 2018-2022

工作经历
腾讯 后端开发 2022-至今

技能
Java, Python, Go
"""
        chunks = semantic_chunk_text(text)
        assert len(chunks) > 0
        # 检查是否按章节分割
        all_text = ''.join(chunk[1] for chunk in chunks)
        assert '教育经历' in all_text
        assert '工作经历' in all_text

    def test_long_text_without_sections(self):
        """测试无章节的长文本"""
        text = "这是测试文本。" * 200  # 约1000字符
        chunks = semantic_chunk_text(text)
        assert len(chunks) > 1


class TestDetectSectionBoundaries:
    """测试章节边界检测"""

    def test_detect_education_section(self):
        """测试检测教育经历章节"""
        text = """
教育经历
清华大学 计算机科学

工作经历
腾讯 后端开发
"""
        sections = detect_section_boundaries(text, [r'(?:教育|学历)(?:背景|经历)?'])
        assert len(sections) >= 2

    def test_detect_multiple_sections(self):
        """测试检测多个章节"""
        text = """
教育经历
清华大学

工作经历
腾讯

技能
Java
"""
        sections = detect_section_boundaries(text, [
            r'(?:教育|学历)(?:背景|经历)?',
            r'(?:工作|实习)(?:经历|经验)?',
            r'(?:技能|能力)',
        ])
        assert len(sections) >= 3

    def test_empty_text(self):
        """测试空文本"""
        sections = detect_section_boundaries("", [])
        assert len(sections) == 0


class TestSplitAtSafePoints:
    """测试安全点分割"""

    def test_short_text(self):
        """测试短文本（不需要分割）"""
        text = "这是测试文本"
        chunks = split_at_safe_points(text, [], 1000)
        assert len(chunks) == 1
        assert chunks[0][1] == text

    def test_text_with_split_points(self):
        """测试带分割点的文本"""
        text = """
教育经历
清华大学

工作经历
腾讯
"""
        chunks = split_at_safe_points(
            text,
            [r'(?=\n(?:教育|学历)(?:背景|经历)?)', r'(?=\n(?:工作|实习)(?:经历|经验)?)'],
            1000
        )
        assert len(chunks) >= 2

    def test_empty_patterns(self):
        """测试空模式"""
        text = "这是测试文本" * 100
        chunks = split_at_safe_points(text, [], 100)
        assert len(chunks) > 1


class TestFixedSizeChunk:
    """测试固定大小分块"""

    def test_short_text(self):
        """测试短文本"""
        chunks = fixed_size_chunk("测试", 100, 10)
        assert len(chunks) == 1

    def test_long_text(self):
        """测试长文本"""
        text = "测试" * 100  # 200字符
        chunks = fixed_size_chunk(text, 50, 10)
        assert len(chunks) == 4

    def test_max_chunks_limit(self):
        """测试最大块数限制"""
        text = "测试" * 1000
        chunks = fixed_size_chunk(text, 50, 5)
        assert len(chunks) <= 5


class TestGetOptimalChunkSize:
    """测试最优块大小获取"""

    def test_short_text(self):
        """测试短文本"""
        size = get_optimal_chunk_size(500)
        assert size <= 500

    def test_long_text(self):
        """测试长文本"""
        size = get_optimal_chunk_size(10000)
        assert size >= 1200

    def test_with_file_ext(self):
        """测试带文件扩展名"""
        size_pdf = get_optimal_chunk_size(5000, "pdf")
        size_txt = get_optimal_chunk_size(5000, "txt")
        # PDF应该有更大的块大小
        assert size_pdf >= size_txt
