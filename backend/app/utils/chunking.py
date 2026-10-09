"""基于配置的语义分块

根据配置文件中的规则，将长文本分割成语义完整的块，
避免切断关键信息（如教育经历、工作经历等）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from config.loader import get_chunking_config


def semantic_chunk_text(
    text: str,
    config: Optional[Dict[str, Any]] = None,
) -> List[Tuple[int, str]]:
    """
    基于配置的语义分块

    Args:
        text: 待分块的文本
        config: 分块配置（可选，默认从配置文件加载）

    Returns:
        分块列表 [(offset, chunk_text), ...]
    """
    if not text:
        return []

    if config is None:
        config = get_chunking_config()

    semantic_config = config.get('semantic', {})
    defaults = config.get('defaults', {})

    chunk_size = defaults.get('chunk_size', 1200)
    max_chunks = defaults.get('max_chunks', 40)

    # 如果未启用语义分块，使用固定大小分块
    if not semantic_config.get('enabled', False):
        return fixed_size_chunk(text, chunk_size, max_chunks)

    # 1. 检测章节边界
    section_patterns = semantic_config.get('section_patterns', [])
    sections = detect_section_boundaries(text, section_patterns)

    # 2. 按章节分块，保护关键信息
    chunks: List[Tuple[int, str]] = []
    for section in sections:
        section_text = section['text']
        if len(section_text) <= chunk_size:
            chunks.append((section['offset'], section_text))
        else:
            # 在安全点分割
            safe_split_patterns = semantic_config.get('safe_split_patterns', [])
            sub_chunks = split_at_safe_points(section_text, safe_split_patterns, chunk_size)
            # 调整offset为全局位置
            for sub_offset, sub_text in sub_chunks:
                chunks.append((section['offset'] + sub_offset, sub_text))

    return chunks[:max_chunks]


def detect_section_boundaries(
    text: str,
    patterns: List[str],
) -> List[Dict[str, Any]]:
    """
    检测章节边界

    Args:
        text: 完整文本
        patterns: 章节标题正则模式列表

    Returns:
        章节列表 [{"offset": int, "title": str, "text": str}, ...]
    """
    if not text:
        return []

    sections: List[Dict[str, Any]] = []
    lines = text.split('\n')
    current_section: Dict[str, Any] = {'offset': 0, 'title': '文档开头', 'lines': []}

    for i, line in enumerate(lines):
        is_boundary = False
        line_stripped = line.strip()

        for pattern in patterns:
            try:
                if re.search(pattern, line_stripped, re.IGNORECASE):
                    is_boundary = True
                    break
            except re.error:
                # 正则语法错误，跳过
                continue

        if is_boundary and current_section['lines']:
            # 保存当前章节
            current_section['text'] = '\n'.join(current_section['lines'])
            sections.append(current_section)

            # 开始新章节
            offset = sum(len(l) + 1 for l in lines[:i])  # +1 for \n
            current_section = {
                'offset': offset,
                'title': line_stripped[:48],  # 限制标题长度
                'lines': [line],
            }
        else:
            current_section['lines'].append(line)

    # 保存最后一个章节
    if current_section['lines']:
        current_section['text'] = '\n'.join(current_section['lines'])
        sections.append(current_section)

    return sections


def split_at_safe_points(
    text: str,
    patterns: List[str],
    max_chunk_size: int,
) -> List[Tuple[int, str]]:
    """
    在安全点分割文本

    Args:
        text: 待分割的文本
        patterns: 安全分割点的正则模式列表
        max_chunk_size: 最大块大小

    Returns:
        分块列表 [(local_offset, chunk_text), ...]
    """
    # 找到所有安全分割点
    split_points = [0]
    for pattern in patterns:
        try:
            for match in re.finditer(pattern, text):
                pos = match.start()
                if pos > 0 and pos not in split_points:
                    split_points.append(pos)
        except re.error:
            # 正则语法错误，跳过
            continue

    split_points.append(len(text))
    split_points = sorted(set(split_points))

    # 按安全点分割
    chunks: List[Tuple[int, str]] = []
    for i in range(len(split_points) - 1):
        start = split_points[i]
        end = split_points[i + 1]

        chunk_text = text[start:end]

        # 如果块太大，递归分割
        if len(chunk_text) > max_chunk_size:
            sub_chunks = fixed_size_chunk(chunk_text, max_chunk_size, 10)
            for sub_offset, sub_text in sub_chunks:
                chunks.append((start + sub_offset, sub_text))
        else:
            chunks.append((start, chunk_text))

    return chunks


def fixed_size_chunk(
    text: str,
    chunk_size: int,
    max_chunks: int,
) -> List[Tuple[int, str]]:
    """
    固定大小分块

    Args:
        text: 待分块的文本
        chunk_size: 块大小
        max_chunks: 最大块数

    Returns:
        分块列表 [(offset, chunk_text), ...]
    """
    chunks: List[Tuple[int, str]] = []
    for i in range(0, len(text), chunk_size):
        if len(chunks) >= max_chunks:
            break
        chunks.append((i, text[i:i + chunk_size]))
    return chunks


def get_optimal_chunk_size(text_length: int, file_ext: str = "") -> int:
    """
    根据文本长度和文件类型获取最优块大小

    Args:
        text_length: 文本长度
        file_ext: 文件扩展名（如 "pdf", "docx"）

    Returns:
        推荐的块大小
    """
    config = get_chunking_config()
    defaults = config.get('defaults', {})
    file_types = config.get('file_types', {})

    # 获取文件类型对应的配置
    ext = (file_ext or "").lstrip(".").lower()
    file_config = file_types.get(ext, {})

    # 合并配置（文件类型配置 > 默认配置）
    chunk_size = file_config.get('chunk_size', defaults.get('chunk_size', 1200))

    # 根据文本长度调整
    if text_length < 2000:
        # 短文本使用更小的块
        return min(chunk_size, text_length // 2 if text_length > 100 else 500)
    elif text_length > 10000:
        # 长文本使用更大的块
        return int(chunk_size * 1.2)

    return chunk_size
