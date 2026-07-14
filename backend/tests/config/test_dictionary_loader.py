"""
test_dictionary_loader.py — D1.4 Dictionary 加载器
- 5 个字典加载
- 关键 key 存在
"""
import pytest
from config.dictionary_loader import (
    get_skill_categories,
    get_degree_keywords,
    get_role_keywords,
    get_company_suffixes,
    get_ethics_sensitive_keywords,
    reload,
)


class TestDictionaryLoader:
    def test_skill_categories_structure(self):
        """技能分类: 7 大类 (英文字段名)"""
        cats = get_skill_categories()
        assert isinstance(cats, dict)
        assert len(cats) >= 6, f"应有至少 6 大类, 实际 {len(cats)}"
        # 7 大类英文字段（实际数据）
        for expected in ("programming_languages", "web_frameworks", "ai_ml",
                         "databases", "devops_tools", "concepts", "soft_skills"):
            assert expected in cats, f"缺分类: {expected}"
        # 每类至少 1 个技能
        for name, items in cats.items():
            assert isinstance(items, list)
            assert len(items) > 0, f"分类 {name} 为空"

    def test_degree_keywords(self):
        """学历关键词: 必须含本科/硕士/博士"""
        kws = get_degree_keywords()
        assert isinstance(kws, list)
        assert "本科" in kws
        assert "硕士" in kws
        assert "博士" in kws

    def test_role_keywords(self):
        """岗位关键词: 非空列表"""
        kws = get_role_keywords()
        assert isinstance(kws, list)
        assert len(kws) > 0

    def test_company_suffixes(self):
        """公司后缀: 非空列表"""
        suffixes = get_company_suffixes()
        assert isinstance(suffixes, list)
        assert len(suffixes) > 0
        # 必须含 有限公司
        assert any("有限" in s for s in suffixes)

    def test_ethics_sensitive_keywords(self):
        """伦理敏感词: dict 形式分类"""
        kws = get_ethics_sensitive_keywords()
        assert isinstance(kws, dict)
        assert kws, "伦理敏感词不能为空"

    def test_cache_reload(self):
        """缓存 + reload 机制"""
        s1 = get_skill_categories()
        s2 = get_skill_categories()
        assert s1 is s2
        reload()
        s3 = get_skill_categories()
        assert s1 == s3
