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
    get_failover_order,
    get_provider_config as _yaml_provider_config,
    get_react_agent_config,
    get_provider_presets as _yaml_provider_presets,
    resolve_provider_api_key,
)

project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    load_dotenv(override=True)


def _is_milvus_file_uri(uri: str) -> bool:
    """本地 Lite 文件路径（.db / 相对路径），不是 http(s) 服务地址。"""
    u = (uri or "").strip()
    if not u:
        return False
    if u.startswith(("http://", "https://", "tcp://", "unix:")):
        return False
    return u.endswith(".db") or "/" in u or "\\" in u


def _resolve_milvus_uri() -> str:
    """解析 Milvus 地址，并避免与 pymilvus 的环境变量 MILVUS_URI 撞名。

    pymilvus 在 import 时会读取进程环境 MILVUS_URI，且只接受 http(s) 形式。
    本项目若把 Lite 路径写进 MILVUS_URI，会导致「一 import 就炸」。
    约定：
    - 优先 CPA_MILVUS_URI（推荐）
    - 兼容旧 MILVUS_URI
    - 若解析结果是本地 .db 路径，则从 os.environ 移除 MILVUS_URI，只保留 Config 属性
    """
    uri = (
        os.environ.get("CPA_MILVUS_URI")
        or os.environ.get("MILVUS_URI")
        or "localhost:19530"
    ).strip()
    if _is_milvus_file_uri(uri):
        # 规范化到绝对路径，便于 Lite 落盘
        if not os.path.isabs(uri):
            uri = str((Path(__file__).resolve().parents[1] / uri).resolve())
        # 根治：进程环境里不能再留文件型 MILVUS_URI
        os.environ.pop("MILVUS_URI", None)
        os.environ["CPA_MILVUS_URI"] = uri
    return uri


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
    DEMO_STUDENT_ID = os.environ.get('CPA_DEMO_STUDENT_ID', 'student_001')
    DEMO_PRINCIPAL_ID = os.environ.get('CPA_DEMO_PRINCIPAL_ID', f'demo:{DEMO_STUDENT_ID}')
    # Cookie account auth defaults to HTTPS-only. Explicitly disable only for local HTTP.
    CPA_AUTH_COOKIE_SECURE = os.environ.get('CPA_AUTH_COOKIE_SECURE', 'true').lower() not in ('0', 'false', 'no', 'off')
    ENABLE_LEGACY_AGENT_ENGINES = (
        os.environ.get('CPA_ENABLE_LEGACY_AGENT_ENGINES', 'false').lower()
        in ('1', 'true', 'yes', 'on')
    )

    # CORS 允许的 origin 列表 (逗号分隔, 空值表示允许全部 — 仅开发环境)
    # 生产环境应通过 .env 设置具体域名: CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    _cors_origins_str = os.environ.get('CORS_ALLOWED_ORIGINS', '').strip()
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_str.split(',') if o.strip()] if _cors_origins_str else ['*']

    # LLM 配置 — .env 优先，未设置时从 config/integrations/llm.yaml 读取
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', get_default_provider()).lower()

    # Provider presets 从 YAML 读取
    PROVIDER_PRESETS = _yaml_provider_presets()

    @classmethod
    def get_provider_config(cls, provider: str | None = None):
        return _yaml_provider_config(provider or cls.LLM_PROVIDER)

    @classmethod
    def get_failover_order(cls):
        return get_failover_order()

    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    LLM_TIMEOUT = int(os.environ.get('LLM_TIMEOUT', str(get_default_timeout())))
    LLM_FAILOVER_ORDER = os.environ.get('LLM_FAILOVER_ORDER', '')

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
    # Session turn admission: Redis provides cross-worker serialization when
    # selected.  Development may deliberately remain local-only; production
    # compose sets redis_required so an outage fails closed instead of claiming
    # a guarantee an in-process mutex cannot provide.
    SESSION_TURN_LOCK_MODE = os.environ.get('CPA_SESSION_TURN_LOCK_MODE', 'redis_preferred')
    SESSION_TURN_LOCK_LEASE_SECONDS = float(os.environ.get('CPA_SESSION_TURN_LOCK_LEASE_SECONDS', '180'))
    SESSION_TURN_LOCK_WAIT_SECONDS = float(os.environ.get('CPA_SESSION_TURN_LOCK_WAIT_SECONDS', '30'))
    # Explicit Idempotency-Key requests use a durable MySQL admission ledger.
    # Stale executions become unknown and are never automatically replayed;
    # completed response bodies are retained for a bounded replay window.
    AGENT_TURN_IDEMPOTENCY_STALE_SECONDS = int(
        os.environ.get('CPA_AGENT_TURN_IDEMPOTENCY_STALE_SECONDS', '7200')
    )
    AGENT_TURN_IDEMPOTENCY_RETENTION_DAYS = int(
        os.environ.get('CPA_AGENT_TURN_IDEMPOTENCY_RETENTION_DAYS', '7')
    )

    # Milvus：用 CPA_MILVUS_URI；勿把本地 .db 写进环境变量 MILVUS_URI（与 pymilvus 冲突）
    MILVUS_URI = _resolve_milvus_uri()

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
        has_key = bool(cls.LLM_API_KEY) or any(
            resolve_provider_api_key(p) for p in get_failover_order()
        )
        if not has_key:
            errors.append("LLM_API_KEY 未配置")
        if not cls.NEO4J_PASSWORD:
            errors.append("NEO4J_PASSWORD 未配置 (可设为空字符串)")
        return errors
