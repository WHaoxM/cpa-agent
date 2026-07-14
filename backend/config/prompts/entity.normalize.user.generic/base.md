请对以下{label}进行规范化分类。

名称: {name}
{ctx}

返回JSON格式:
{{
  "canonical_name": "规范名称(统一标准叫法)",
  "category": "分类(如: 编程语言/前端/后端框架/数据库/AI-ML/DevOps/工具/软技能 等)",
  "aliases": ["别名1", "别名2", "缩写"]
}}

注意:
- canonical_name 应该是最通用的叫法(如: Vue.js 而非 Vue3)
- aliases 包含常见的其他叫法、缩写、版本号变体
- category 必须从已有分类中选择, 不要发明新分类
