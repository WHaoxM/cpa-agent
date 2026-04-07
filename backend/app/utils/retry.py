"""
API 调用重试机制
参考 MiroFish retry_with_backoff, 适用于 LLM / Neo4j / MySQL 等外部服务
"""

import time
import random
import functools
from typing import Callable, Any, Optional, Type, Tuple

from .logger import get_logger

logger = get_logger('cpa-agent.retry')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    带指数退避的重试装饰器

    Args:
        max_retries: 最大重试次数
        initial_delay: 初始延迟 (秒)
        max_delay: 最大延迟 (秒)
        backoff_factor: 退避因子
        jitter: 是否添加随机抖动
        exceptions: 需要重试的异常类型
        on_retry: 重试时的回调函数 (exception, retry_count)

    Usage:
        @retry_with_backoff(max_retries=3)
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"函数 {func.__name__} 在 {max_retries} 次重试后仍失败: {str(e)}"
                        )
                        raise

                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())

                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1} 次尝试失败: {str(e)}, "
                        f"{current_delay:.1f}秒后重试..."
                    )

                    if on_retry:
                        on_retry(e, attempt + 1)

                    time.sleep(current_delay)
                    delay *= backoff_factor

            raise last_exception

        return wrapper
    return decorator
