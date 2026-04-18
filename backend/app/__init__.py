"""
CPA-Agent Backend - Flask 应用工厂
参考 MiroFish 架构，适配职业规划智能体场景
"""

import os
import time
import warnings
import psutil

warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, g
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask 应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    logger = setup_logger('cpa-agent')

    is_reloader = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log = not debug_mode or is_reloader

    if should_log:
        logger.info("=" * 50)
        logger.info("CPA-Agent Backend 启动中...")
        logger.info("=" * 50)

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.before_request
    def log_request():
        g.start_time = time.time()
        req_logger = get_logger('cpa-agent.request')
        req_logger.debug(f"请求: {request.method} {request.path}")

    @app.after_request
    def log_response(response):
        req_logger = get_logger('cpa-agent.request')
        
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            if elapsed > 1.0:
                req_logger.warning(f"慢请求: {request.method} {request.path} - {elapsed:.2f}s")
            else:
                req_logger.debug(f"响应: {response.status_code} - {elapsed:.3f}s")
        
        return response

    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'CPA-Agent Backend'}

    @app.route('/metrics')
    def metrics():
        """性能监控指标"""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            
            return {
                'status': 'ok',
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used_mb': memory.used / (1024 * 1024),
                'memory_available_mb': memory.available / (1024 * 1024),
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    from .api import graph_bp, profile_bp, match_bp, report_bp, agent_bp, pipeline_bp, learning_bp, admin_bp, favorites_bp, pipeline_ext
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(profile_bp, url_prefix='/api/profile')
    app.register_blueprint(match_bp, url_prefix='/api/match')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(agent_bp, url_prefix='/api/agent')
    app.register_blueprint(pipeline_bp, url_prefix='/api/pipeline')
    app.register_blueprint(pipeline_ext.pipeline_ext_bp, url_prefix='/api/pipeline/ext')
    app.register_blueprint(learning_bp, url_prefix='/api/learning')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(favorites_bp, url_prefix='/api')

    if should_log:
        logger.info("CPA-Agent Backend 启动完成")

    return app
