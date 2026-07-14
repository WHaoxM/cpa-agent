"""
test_integration_loader.py — D1.5 Integration 加载器
- 4 集成配置加载（llm/embedding/agent/ethics）
- 业务默认 vs .env 隔离（provider 路径走 .env）
"""
import pytest
from config.integration_loader import (
    get_llm_provider_presets,
    get_provider_config,
    get_default_provider,
    get_embedding_config,
    get_embedding_model_name,
    get_embedding_dimension,
    get_react_agent_config,
    get_langgraph_agent_config,
    get_ethics_filter_config,
    reload,
)


class TestIntegrationLoader:
    def test_llm_provider_presets(self):
        """LLM presets: 至少 1 个 provider (openai/custom)"""
        presets = get_llm_provider_presets()
        assert isinstance(presets, dict)
        assert "openai" in presets or "custom" in presets
        # preset 应含 base_url
        for name, cfg in presets.items():
            assert "base_url" in cfg, f"provider {name} 缺少 base_url"
            assert "model" in cfg, f"provider {name} 缺少 model"

    def test_default_provider(self):
        """默认 provider: string 非空"""
        p = get_default_provider()
        assert isinstance(p, str) and p

    def test_provider_config_keys(self):
        """provider_config: 5 个键"""
        cfg = get_provider_config()
        assert "provider" in cfg
        assert "api_key" in cfg
        assert "base_url" in cfg
        assert "model" in cfg
        assert "timeout" in cfg

    def test_embedding_config(self):
        """embedding: BGE-M3, dim=1024"""
        cfg = get_embedding_config()
        assert isinstance(cfg, dict)
        assert get_embedding_model_name() == "BAAI/bge-m3"
        assert get_embedding_dimension() == 1024

    def test_react_agent_config(self):
        """ReAct agent config 存在"""
        cfg = get_react_agent_config()
        assert isinstance(cfg, dict)
        assert cfg, "react_agent 配置不能为空"

    def test_langgraph_agent_config(self):
        """LangGraph agent config 存在"""
        cfg = get_langgraph_agent_config()
        assert isinstance(cfg, dict)
        assert cfg, "langgraph_agent 配置不能为空"

    def test_ethics_filter_config(self):
        """伦理过滤器配置存在"""
        cfg = get_ethics_filter_config()
        assert isinstance(cfg, dict)
        assert cfg, "ethics_filter 配置不能为空"

    def test_reload(self):
        """reload() 清缓存"""
        c1 = get_embedding_config()
        reload()
        c2 = get_embedding_config()
        assert c1 == c2
