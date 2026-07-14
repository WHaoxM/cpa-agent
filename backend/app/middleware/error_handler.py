"""
错误处理中间件
统一异常处理和错误响应
"""

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from ..exceptions import APIException, ErrorCode
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.middleware.error')


class ErrorHandler:
    """错误处理中间件"""

    def __init__(self, app: Flask = None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app: Flask):
        """初始化中间件"""
        app.register_error_handler(APIException, self.handle_api_exception)
        app.register_error_handler(HTTPException, self.handle_http_exception)
        app.register_error_handler(Exception, self.handle_generic_exception)

    @staticmethod
    def handle_api_exception(error: APIException):
        """处理 API 异常"""
        logger.warning(f"API异常: {error.error_code.value} - {error.message}")
        return jsonify(error.to_dict()), error.status_code

    @staticmethod
    def handle_http_exception(error: HTTPException):
        """处理 HTTP 异常"""
        logger.warning(f"HTTP异常: {error.code} - {error.description}")
        body = {
            'success': False,
            'error': error.description,
            'code': ErrorCode.INTERNAL_ERROR.value,
        }
        return jsonify(body), error.code

    @staticmethod
    def handle_generic_exception(error: Exception):
        """处理通用异常"""
        logger.error(f"未处理异常: {type(error).__name__} - {str(error)}")
        body = {
            'success': False,
            'error': '服务器内部错误',
            'code': ErrorCode.INTERNAL_ERROR.value,
        }
        return jsonify(body), 500
