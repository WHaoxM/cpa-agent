"""文件处理工具

借鉴OpenCode的文件处理系统，为CPA-Agent提供：
1. 二进制文件检测
2. 图片处理和base64编码
3. 文件类型识别
4. 路径安全校验
"""

from __future__ import annotations

import os
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from enum import Enum


class FileType(Enum):
    """文件类型"""
    TEXT = "text"
    BINARY = "binary"
    IMAGE = "image"
    PDF = "pdf"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    UNKNOWN = "unknown"


# 图片MIME类型
IMAGE_MIMES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
    '.tiff': 'image/tiff',
    '.tif': 'image/tiff',
}

# 文档MIME类型
DOCUMENT_MIMES = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

# 音频MIME类型
AUDIO_MIMES = {
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.webm': 'audio/webm',
    '.ogg': 'audio/ogg',
}

# 文本文件扩展名
TEXT_EXTENSIONS = {
    '.txt', '.md', '.markdown', '.json', '.csv', '.log',
    '.yml', '.yaml', '.xml', '.html', '.css', '.js',
    '.ts', '.py', '.java', '.go', '.rs', '.cpp', '.c', '.h',
}


def detect_file_type(file_path: str) -> FileType:
    """
    检测文件类型
    
    Args:
        file_path: 文件路径
    
    Returns:
        FileType枚举
    """
    ext = Path(file_path).suffix.lower()
    
    if ext in IMAGE_MIMES:
        return FileType.IMAGE
    elif ext in DOCUMENT_MIMES:
        if ext == '.pdf':
            return FileType.PDF
        return FileType.DOCUMENT
    elif ext in AUDIO_MIMES:
        return FileType.AUDIO
    elif ext in TEXT_EXTENSIONS:
        return FileType.TEXT
    elif ext == '':
        # 无扩展名，尝试读取文件头
        if _is_binary_file(file_path):
            return FileType.BINARY
        return FileType.TEXT
    else:
        # 尝试读取文件头判断
        if _is_binary_file(file_path):
            return FileType.BINARY
        return FileType.UNKNOWN


def _is_binary_file(file_path: str, chunk_size: int = 8192) -> bool:
    """
    检测文件是否为二进制文件
    
    Args:
        file_path: 文件路径
        chunk_size: 读取块大小
    
    Returns:
        是否为二进制文件
    """
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(chunk_size)
            # 检查是否有空字节
            if b'\x00' in chunk:
                return True
            # 检查非文本字符比例
            text_chars = sum(1 for b in chunk if b < 128 or b > 159)
            if len(chunk) > 0 and text_chars / len(chunk) < 0.7:
                return True
        return False
    except Exception:
        return False


def get_mime_type(file_path: str) -> str:
    """
    获取文件MIME类型
    
    Args:
        file_path: 文件路径
    
    Returns:
        MIME类型字符串
    """
    ext = Path(file_path).suffix.lower()
    
    # 优先从预定义映射中获取
    if ext in IMAGE_MIMES:
        return IMAGE_MIMES[ext]
    if ext in DOCUMENT_MIMES:
        return DOCUMENT_MIMES[ext]
    if ext in AUDIO_MIMES:
        return AUDIO_MIMES[ext]
    
    # 使用mimetypes模块
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'


def file_to_base64(file_path: str, max_size_mb: int = 20) -> Optional[str]:
    """
    将文件转换为base64编码
    
    Args:
        file_path: 文件路径
        max_size_mb: 最大文件大小（MB）
    
    Returns:
        base64编码字符串，如果文件太大或读取失败则返回None
    """
    import base64
    
    try:
        file_size = os.path.getsize(file_path)
        if file_size > max_size_mb * 1024 * 1024:
            return None
        
        with open(file_path, 'rb') as f:
            content = f.read()
            return base64.b64encode(content).decode('utf-8')
    except Exception:
        return None


def image_to_base64(file_path: str, max_size_mb: int = 20) -> Optional[Dict[str, str]]:
    """
    将图片转换为base64编码（带MIME类型）
    
    Args:
        file_path: 图片路径
        max_size_mb: 最大文件大小（MB）
    
    Returns:
        包含data和mime的字典，如果失败则返回None
    """
    file_type = detect_file_type(file_path)
    if file_type != FileType.IMAGE:
        return None
    
    base64_data = file_to_base64(file_path, max_size_mb)
    if not base64_data:
        return None
    
    mime_type = get_mime_type(file_path)
    return {
        'data': base64_data,
        'mime': mime_type,
        'name': Path(file_path).name,
    }


def safe_path_join(base_dir: str, relative_path: str) -> Optional[str]:
    """
    安全的路径拼接（防止路径逃逸）
    
    Args:
        base_dir: 基础目录
        relative_path: 相对路径
    
    Returns:
        拼接后的绝对路径，如果路径不安全则返回None
    """
    try:
        base = Path(base_dir).resolve()
        target = (base / relative_path).resolve()
        
        # 检查目标是否在基础目录内
        if not str(target).startswith(str(base)):
            return None
        
        return str(target)
    except Exception:
        return None


def read_file_content(
    file_path: str,
    max_lines: int = 2000,
    encoding: str = 'utf-8',
) -> Optional[str]:
    """
    读取文件内容
    
    Args:
        file_path: 文件路径
        max_lines: 最大行数
        encoding: 文件编码
    
    Returns:
        文件内容，如果读取失败则返回None
    """
    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
            return ''.join(lines)
    except Exception:
        return None


def get_file_info(file_path: str) -> Dict[str, Any]:
    """
    获取文件详细信息
    
    Args:
        file_path: 文件路径
    
    Returns:
        文件信息字典
    """
    path = Path(file_path)
    
    info = {
        'path': str(path.absolute()),
        'name': path.name,
        'extension': path.suffix.lower(),
        'type': detect_file_type(file_path).value,
        'mime_type': get_mime_type(file_path),
    }
    
    try:
        stat = path.stat()
        info['size'] = stat.st_size
        info['size_human'] = _human_readable_size(stat.st_size)
        info['modified'] = stat.st_mtime
        info['created'] = stat.st_ctime
    except Exception:
        info['size'] = 0
        info['size_human'] = '0 B'
    
    return info


def _human_readable_size(size: int) -> str:
    """将字节大小转换为可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
