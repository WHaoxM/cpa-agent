"""
CORS 中间件
跨域资源共享处理
"""

from flask import Flask, request

from ..utils.logger import get_logger

logger = get_logger('cpa-agent.middleware.cors')


class CorsMiddleware:
    """CORS 中间件"""

    def __init__(self, app: Flask = None, allowed_origins: list = None):
        self.allowed_origins = allowed_origins or ['*']
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app: Flask):
        """初始化中间件"""
        app.after_request(self.add_cors_headers)

    def add_cors_headers(self, response):
        """添加 CORS 响应头"""
        origin = request.headers.get('Origin')

        if '*' in self.allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = '*'
        elif origin and origin in self.allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin

        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        response.headers['Access-Control-Max-Age'] = '3600'

        if request.method == 'OPTIONS':
            response.headers['Access-Control-Allow-Credentials'] = 'true'

        return response
