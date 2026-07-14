"""
日志中间件
请求/响应日志记录
"""

import time
from flask import Flask, request, g
from functools import wraps

from ..utils.logger import get_logger

logger = get_logger('cpa-agent.middleware.logging')


class LoggingMiddleware:
    """日志中间件"""

    def __init__(self, app: Flask = None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app: Flask):
        """初始化中间件"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)

    def before_request(self):
        """请求前处理"""
        g.start_time = time.time()
        logger.debug(f"请求开始: {request.method} {request.path}")

    def after_request(self, response):
        """请求后处理"""
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time

            log_data = {
                'method': request.method,
                'path': request.path,
                'status': response.status_code,
                'elapsed_ms': round(elapsed * 1000, 2)
            }

            if elapsed > 1.0:
                logger.warning(f"慢请求: {log_data}")
            else:
                logger.debug(f"请求完成: {log_data}")

        return response


def log_function_call(func):
    """函数调用日志装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug(f"调用函数: {func.__name__}")
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.debug(f"函数完成: {func.__name__}, 耗时: {elapsed*1000:.2f}ms")
        return result
    return wrapper
