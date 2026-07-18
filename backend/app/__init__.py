"""
CPA-Agent Backend - Flask 应用工厂
"""

import os
import warnings

warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask

from .config import Config
from .utils.logger import setup_logger, get_logger
from .middleware import LoggingMiddleware, ErrorHandler, CorsMiddleware


def create_app(config_class=Config):
    """Flask 应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    if hasattr(app, 'json'):
        if hasattr(app.json, 'ensure_ascii'):
            app.json.ensure_ascii = False
        # Flask 3 JSON provider：显式 charset，避免 Windows 客户端按 CP936 误解码
        if hasattr(app.json, 'mimetype'):
            app.json.mimetype = 'application/json; charset=utf-8'
    app.config.setdefault('JSONIFY_MIMETYPE', 'application/json; charset=utf-8')

    logger = setup_logger('cpa-agent')

    is_reloader = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log = not debug_mode or is_reloader

    if should_log:
        logger.info("=" * 50)
        logger.info("CPA-Agent Backend 启动中...")
        logger.info("=" * 50)

    LoggingMiddleware(app)
    ErrorHandler(app)
    CorsMiddleware(app, allowed_origins=config_class.CORS_ALLOWED_ORIGINS)

    try:
        from config.loader import init_rules_config
        init_rules_config()
    except Exception as e:
        logger.error(f"业务规则配置加载失败: {e}")
        raise

    try:
        from config.prompt_loader import init_prompt_registry
        init_prompt_registry()
    except Exception as e:
        logger.error(f"Prompt 注册表加载失败: {e}")
        raise

    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'CPA-Agent Backend'}

    @app.route('/api/admin/python-info')
    def python_info():
        """检查 Flask 使用的 Python 信息"""
        import sys
        return {
            'python_executable': sys.executable,
            'python_version': sys.version,
            'python_path': sys.path[:5],
        }

    @app.route('/metrics')
    def metrics():
        """性能监控指标"""
        try:
            import psutil
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

    from .api import graph_bp, profile_bp, match_bp, report_bp, agent_bp, pipeline_bp, learning_bp, admin_bp, favorites_bp, career_bp, messages_bp, ability_bp, ai_bp, pipeline_ext_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(profile_bp, url_prefix='/api/profile')
    app.register_blueprint(match_bp, url_prefix='/api/match')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(agent_bp, url_prefix='/api/agent')
    app.register_blueprint(pipeline_bp, url_prefix='/api/pipeline')
    app.register_blueprint(pipeline_ext_bp, url_prefix='/api/pipeline/ext')
    app.register_blueprint(learning_bp, url_prefix='/api/learning')
    app.register_blueprint(career_bp, url_prefix='/api/career')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(favorites_bp, url_prefix='/api')
    app.register_blueprint(messages_bp, url_prefix='/api')
    app.register_blueprint(ability_bp, url_prefix='/api')
    app.register_blueprint(ai_bp, url_prefix='/api/ai')

    if should_log:
        logger.info("CPA-Agent Backend 启动完成")

    return app
