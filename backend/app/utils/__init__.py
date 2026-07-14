"""
工具库模块
"""

from .logger import setup_logger, get_logger
from .retry import retry_with_backoff

__all__ = [
    'setup_logger', 'get_logger',
    'retry_with_backoff',
    'LLMClient',
]


def __getattr__(name):
    if name == 'LLMClient':
        from .llm_client import LLMClient
        return LLMClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
