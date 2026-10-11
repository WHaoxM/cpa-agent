"""测试grep搜索功能"""

import pytest
from app.services.agent_runtime.media.document_access import (
    grep_full_text,
    format_grep_results,
)


class TestGrepFullText:
    """测试全文grep搜索"""

    def test_simple_text_search(self):
        """测试简单文本搜索"""
        text = """
教育经历
清华大学 计算机科学 2018-2022

工作经历
腾讯 后端开发 2022-至今
"""
        results = grep_full_text(
            text, "教育",
            use_regex=False,
            case_sensitive=False,
            context_lines=1,
            max_results=5,
        )
        assert len(results) > 0
        assert '教育经历' in results[0]['line']

    def test_case_insensitive_search(self):
        """测试大小写不敏感搜索"""
        text = """
Education Background
清华大学 计算机科学 2018-2022
"""
        results = grep_full_text(
            text, "education",
            use_regex=False,
            case_sensitive=False,
            context_lines=0,
            max_results=5,
        )
        assert len(results) > 0
        assert 'Education' in results[0]['line']

    def test_regex_search(self):
        """测试正则表达式搜索"""
        text = """
电话：13800138000
邮箱：zhangsan@example.com
"""
        results = grep_full_text(
            text, r'\d{11}',
            use_regex=True,
            case_sensitive=False,
            context_lines=0,
            max_results=5,
        )
        assert len(results) > 0
        assert '13800138000' in results[0]['line']

    def test_email_regex_search(self):
        """测试邮箱正则搜索"""
        text = """
联系人：张三
邮箱：zhangsan@example.com
电话：13800138000
"""
        results = grep_full_text(
            text, r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
            use_regex=True,
            case_sensitive=False,
            context_lines=1,
            max_results=5,
        )
        assert len(results) > 0
        assert 'zhangsan@example.com' in results[0]['line']

    def test_context_lines(self):
        """测试上下文行数"""
        text = """
第一行
第二行
教育经历
第四行
第五行
"""
        results = grep_full_text(
            text, "教育",
            use_regex=False,
            case_sensitive=False,
            context_lines=2,
            max_results=5,
        )
        assert len(results) > 0
        # 检查上下文包含前后行
        context = results[0]['context']
        assert '第二行' in context
        assert '第四行' in context

    def test_max_results_limit(self):
        """测试最大结果数限制"""
        text = """
教育经历1
教育经历2
教育经历3
教育经历4
教育经历5
"""
        results = grep_full_text(
            text, "教育",
            use_regex=False,
            case_sensitive=False,
            context_lines=0,
            max_results=3,
        )
        assert len(results) <= 3

    def test_empty_pattern(self):
        """测试空模式"""
        text = "测试文本"
        results = grep_full_text(text, "", use_regex=False)
        assert len(results) == 0

    def test_empty_text(self):
        """测试空文本"""
        results = grep_full_text("", "测试", use_regex=False)
        assert len(results) == 0

    def test_no_match(self):
        """测试无匹配"""
        text = "这是测试文本"
        results = grep_full_text(
            text, "教育",
            use_regex=False,
            case_sensitive=False,
            context_lines=0,
            max_results=5,
        )
        assert len(results) == 0


class TestFormatGrepResults:
    """测试grep结果格式化"""

    def test_format_empty_results(self):
        """测试空结果格式化"""
        result = format_grep_results([], query="教育")
        assert '无匹配结果' in result

    def test_format_with_results(self):
        """测试有结果格式化"""
        hits = [
            {
                "line_number": 5,
                "line": "教育经历",
                "context": "教育经历",
                "source": "keyword_match",
            }
        ]
        result = format_grep_results(hits, query="教育")
        assert '教育经历' in result
        assert 'L5' in result

    def test_format_semantic_match(self):
        """测试语义匹配结果格式化"""
        hits = [
            {
                "line_number": 0,
                "line": "学术背景",
                "context": "学术背景",
                "source": "semantic_match",
                "relevance_score": 0.95,
                "semantic_reason": "语义相似",
            }
        ]
        result = format_grep_results(hits, query="教育")
        assert '学术背景' in result
        assert 'semantic_match' in result
        assert '0.95' in result
