"""
Stage 6 D1 共享 fixture
- 真实 workspace (default) 跑正向
- tmp_path 造临时 workspace 跑负向（ADR 契约）
- 单测间用 reload() 清缓存，避免状态污染
"""
import os
import sys
from pathlib import Path
import pytest

# 把 backend 加进 sys.path（绕开 app/__init__.py 触发 psutil 等依赖）
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 确保使用真实 workspace/default（不要受外部 .env 干扰）
os.environ.setdefault("WORKSPACE_ID", "default")
# 清掉可能干扰的 WORKSPACE_PATH
os.environ.pop("WORKSPACE_PATH", None)


@pytest.fixture(autouse=True)
def _reload_loaders():
    """每个用例前后清空所有 loader 缓存，避免单测间状态污染"""
    from config import (
        reload_dictionaries,
        reload_integrations,
    )
    from config.schema_loader import reload as reload_schemas
    from config.loader import init_rules_config
    from config.prompt_loader import init_prompt_registry

    # 启动时强制 reload
    reload_dictionaries()
    reload_integrations()
    reload_schemas()
    init_rules_config(force=True)
    init_prompt_registry(force=True)
    yield
    # 测后也清，给下一个用例一个干净起点
    reload_dictionaries()
    reload_integrations()
    reload_schemas()


@pytest.fixture
def repo_root() -> Path:
    """仓库根 (含 workspace/ 目录)"""
    return BACKEND_ROOT.parent


@pytest.fixture
def real_workspace_dir(repo_root) -> Path:
    """workspace/default 路径（已有合法内容）"""
    ws = repo_root / "workspace" / "default"
    assert ws.is_dir(), f"workspace/default 不存在: {ws}"
    return ws


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch) -> Path:
    """
    造一个临时 workspace 目录并切换 WORKSPACE_PATH 到它。
    测完自动恢复。适合负向契约 + workspace 行为验证。
    """
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    # 同时清掉 WORKSPACE_ID 避免冲突
    monkeypatch.delenv("WORKSPACE_ID", raising=False)
    # 强制 reload 让新路径生效
    from config.loader import init_rules_config
    from config.prompt_loader import init_prompt_registry
    init_rules_config(force=True)
    init_prompt_registry(force=True)
    return tmp_path
