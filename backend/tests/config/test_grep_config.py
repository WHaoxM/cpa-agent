"""测试grep/llm_extraction/chunking配置加载"""

import pytest
from config.loader import (
    get_grep_config,
    get_extraction_config,
    get_chunking_config,
    reload_all_configs,
)


@pytest.fixture(autouse=True)
def _reload():
    """每个测试前刷新配置缓存"""
    reload_all_configs()
    yield
    reload_all_configs()


class TestGrepConfig:
    """测试grep搜索配置"""

    def test_grep_config_loads(self):
        """测试grep配置能够正确加载"""
        config = get_grep_config()
        assert isinstance(config, dict)
        assert 'defaults' in config
        assert 'resume_keywords' in config
        assert 'semantic_matching' in config

    def test_grep_keywords_structure(self):
        """测试关键词库结构"""
        config = get_grep_config()
        keywords = config['resume_keywords']
        assert isinstance(keywords, dict)
        assert 'education' in keywords
        assert 'experience' in keywords
        assert 'projects' in keywords
        assert 'skills' in keywords

    def test_grep_keywords_are_lists(self):
        """测试关键词是列表格式"""
        config = get_grep_config()
        for category, keywords in config['resume_keywords'].items():
            assert isinstance(keywords, list), f"{category} should be a list"
            assert len(keywords) > 0, f"{category} should not be empty"

    def test_grep_defaults(self):
        """测试grep默认值"""
        config = get_grep_config()
        defaults = config['defaults']
        assert 'use_regex' in defaults
        assert 'case_sensitive' in defaults
        assert 'context_lines' in defaults
        assert 'max_results' in defaults

    def test_semantic_matching_config(self):
        """测试语义匹配配置"""
        config = get_grep_config()
        semantic = config['semantic_matching']
        assert semantic['enabled'] is True
        assert 'categories' in semantic
        assert 'education' in semantic['categories']

    def test_semantic_variants(self):
        """测试语义变体配置"""
        config = get_grep_config()
        categories = config['semantic_matching']['categories']
        for cat_name, cat_config in categories.items():
            assert 'description' in cat_config
            assert 'semantic_variants' in cat_config
            assert isinstance(cat_config['semantic_variants'], list)


class TestExtractionConfig:
    """测试LLM抽取配置"""

    def test_extraction_config_loads(self):
        """测试LLM抽取配置能够正确加载"""
        config = get_extraction_config()
        assert isinstance(config, dict)
        assert 'windows' in config
        assert 'normalization' in config

    def test_window_sizes(self):
        """测试窗口大小配置"""
        config = get_extraction_config()
        windows = config['windows']
        assert 'full_structure' in windows
        assert 'chunk' in windows
        assert 'education' in windows
        assert 'experience' in windows
        assert 'skills' in windows

    def test_normalization_rules(self):
        """测试归一化规则"""
        config = get_extraction_config()
        normalization = config['normalization']
        assert 'degree_mapping' in normalization
        assert 'date_format' in normalization
        assert 'gpa_format' in normalization

    def test_degree_mapping(self):
        """测试学位映射"""
        config = get_extraction_config()
        degree_map = config['normalization']['degree_mapping']
        assert '本科' in degree_map
        assert '硕士' in degree_map
        assert '博士' in degree_map


class TestChunkingConfig:
    """测试分块配置"""

    def test_chunking_config_loads(self):
        """测试分块配置能够正确加载"""
        config = get_chunking_config()
        assert isinstance(config, dict)
        assert 'defaults' in config
        assert 'semantic' in config

    def test_chunking_defaults(self):
        """测试分块默认值"""
        config = get_chunking_config()
        defaults = config['defaults']
        assert 'chunk_size' in defaults
        assert 'max_chunks' in defaults
        assert 'overlap' in defaults

    def test_semantic_chunking(self):
        """测试语义分块配置"""
        config = get_chunking_config()
        semantic = config['semantic']
        assert semantic['enabled'] is True
        assert 'section_patterns' in semantic
        assert 'safe_split_patterns' in semantic

    def test_file_type_config(self):
        """测试文件类型配置"""
        config = get_chunking_config()
        file_types = config['file_types']
        assert 'pdf' in file_types
        assert 'docx' in file_types
        assert 'txt' in file_types
