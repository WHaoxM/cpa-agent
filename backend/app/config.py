"""
配置管理
统一从项目根目录的 .env 文件加载配置

阶段二迁移 (2B): provider presets 和 embedding 参数已迁至
config/defaults/integrations/，由 config.integration_loader 提供默认值。
"""

import os
import warnings
from pathlib import Path
from dotenv import load_dotenv

from config.integration_loader import (
    get_default_provider,
    get_default_timeout,
    get_embedding_dimension,
    get_embedding_model_name,
    get_provider_config as _yaml_provider_config,
    get_react_agent_config,
    get_provider_presets as _yaml_provider_presets,
)

project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    load_dotenv(override=True)


class Config:
    """Flask 配置类"""

    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        warnings.warn(
            "SECRET_KEY 环境变量未设置，使用不安全的默认值。生产环境必须设置！",
            RuntimeWarning
        )
        SECRET_KEY = 'cpa-agent-insecure-default-do-not-use-in-production'
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    JSON_AS_ASCII = False
    DEMO_MODE = os.environ.get('CPA_DEMO_MODE', 'true').lower() in ('1', 'true', 'yes', 'on')

    # CORS 允许的 origin 列表 (逗号分隔, 空值表示允许全部 — 仅开发环境)
    # 生产环境应通过 .env 设置具体域名: CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    _cors_origins_str = os.environ.get('CORS_ALLOWED_ORIGINS', '').strip()
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_str.split(',') if o.strip()] if _cors_origins_str else ['*']

    # LLM 配置 — .env 优先，未设置时从 config/integrations/llm.yaml 读取
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', get_default_provider()).lower()

    # Provider presets 从 YAML 读取
    PROVIDER_PRESETS = _yaml_provider_presets()

    @classmethod
    def get_provider_config(cls):
        return _yaml_provider_config(cls.LLM_PROVIDER)

    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    LLM_TIMEOUT = int(os.environ.get('LLM_TIMEOUT', str(get_default_timeout())))

    # Neo4j 配置
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', '')

    # MySQL 配置
    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', '3306'))
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'cpa_agent')

    # Redis 配置
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

    # Milvus 配置 (独立服务器模式)
    MILVUS_URI = os.environ.get('MILVUS_URI', 'localhost:19530')

    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'json'}

    # Agent 配置 — .env 优先，未设置时从 config/integrations/agent.yaml 读取
    _react_defaults = get_react_agent_config()

    AGENT_MAX_TOOL_CALLS = int(os.environ.get('AGENT_MAX_TOOL_CALLS', str(_react_defaults.get('max_tool_calls', 5))))
    AGENT_MAX_ITERATIONS = int(os.environ.get('AGENT_MAX_ITERATIONS', str(_react_defaults.get('max_iterations', 8))))
    AGENT_TEMPERATURE = float(os.environ.get('AGENT_TEMPERATURE', str(_react_defaults.get('temperature', 0.5))))

    # BGE-M3 Embedding 配置 — .env 优先，未设置时从 config/integrations/embedding.yaml 读取
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', get_embedding_model_name())
    EMBEDDING_DIM = int(os.environ.get('EMBEDDING_DIM', str(get_embedding_dimension())))

    # Workspace 配置（见 docs/adr/0001-config-and-workspace-layers.md）
    WORKSPACE_ID = os.environ.get('WORKSPACE_ID', 'default')
    WORKSPACE_PATH = os.environ.get('WORKSPACE_PATH', '').strip()

    @classmethod
    def validate(cls, *, strict: bool | None = None):
        """验证必要配置"""
        if strict is None:
            strict = not cls.DEMO_MODE
        if not strict:
            return []

        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        if not cls.NEO4J_PASSWORD:
            errors.append("NEO4J_PASSWORD 未配置 (可设为空字符串)")
        return errors
