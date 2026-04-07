"""
日志工具
参考 MiroFish 日志模式, 支持控制台 + 文件输出
"""

import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, log_dir: str = None) -> logging.Logger:
    """
    初始化日志器

    Args:
        name: 日志器名称
        log_dir: 日志文件目录, 默认 backend/logs/

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        '[%(asctime)s] %(levelname)-7s %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if log_dir is None:
        log_dir = os.path.join(os.path.dirname(__file__), '../../logs')
    os.makedirs(log_dir, exist_ok=True)

    fh = RotatingFileHandler(
        os.path.join(log_dir, f'{name}.log'),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取已存在的日志器或创建新的"""
    return logging.getLogger(name)
