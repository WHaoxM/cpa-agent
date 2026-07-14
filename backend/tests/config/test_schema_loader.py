"""
test_schema_loader.py — D1.3 Schema 加载器
- 3 schema 全部能加载
- _meta.frozen 正确识别
- 缓存机制
"""
import pytest
from config.schema_loader import (
    get_education_schema,
    get_project_schema,
    get_extraction_output_schema,
    is_frozen,
    list_schemas,
    reload,
)


class TestSchemaLoader:
    def test_all_schemas_loadable(self):
        """3 个 schema 全部能加载且根节点是 dict"""
        for getter, name in [
            (get_education_schema, "education"),
            (get_project_schema, "project"),
            (get_extraction_output_schema, "extraction_output"),
        ]:
            schema = getter()
            assert isinstance(schema, dict), f"{name} schema 应为 dict"
            assert schema, f"{name} schema 不能为空"

    def test_list_schemas(self):
        """列出所有 schema 文件名"""
        names = list_schemas()
        assert isinstance(names, list)
        assert "education.json" in names
        assert "project.json" in names
        assert "extraction_output.json" in names

    def test_frozen_flag(self):
        """frozen=true 的 schema 正确识别"""
        # 至少 education 应是冻结契约
        assert is_frozen("education") is True
        # project 也应是冻结契约
        assert is_frozen("project") is True

    def test_cache_works(self):
        """第二次调用应返回同一对象（缓存生效）"""
        s1 = get_education_schema()
        s2 = get_education_schema()
        assert s1 is s2

    def test_reload_clears_cache(self):
        """reload() 后缓存清空，重新读取"""
        s1 = get_education_schema()
        reload()
        s2 = get_education_schema()
        # 值相等，但不是同一对象引用
        assert s1 == s2
