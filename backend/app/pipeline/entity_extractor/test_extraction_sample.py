#!/usr/bin/env python3
"""
实体/关系抽取测试脚本 - 使用冻结 Schema
"""
import json
from pathlib import Path

# ============== 冻结 Schema ==============
FROZEN_SCHEMA = {
    "schema_version": "extract_v1_1_relational",
    "mode": "TRAIN",
    "entities": [
        {
            "entity_id": "e001",
            "entity_type": "NAME",
            "span_text": "张伟",
            "block_id": "b1"
        },
        {
            "entity_id": "e002",
            "entity_type": "PHONE",
            "span_text": "13800138000",
            "block_id": "b1"
        },
        {
            "entity_id": "e003",
            "entity_type": "EMAIL",
            "span_text": "zhangwei@example.com",
            "block_id": "b1"
        },
        {
            "entity_id": "e004",
            "entity_type": "COMPANY",
            "span_text": "字节跳动",
            "block_id": "b3"
        },
        {
            "entity_id": "e005",
            "entity_type": "JOB_TITLE",
            "span_text": "高级算法工程师",
            "block_id": "b3",
            "parent_entity_id": "e004",
            "role_in_parent": "employment_title"
        },
        {
            "entity_id": "e006",
            "entity_type": "DATE_RANGE",
            "span_text": "2021/06 - 至今",
            "block_id": "b3",
            "parent_entity_id": "e004",
            "role_in_parent": "employment_period",
            "normalized": {
                "start_date": "2021-06",
                "end_date": None,
                "is_current": True
            }
        },
        {
            "entity_id": "e007",
            "entity_type": "SCHOOL",
            "span_text": "清华大学",
            "block_id": "b4"
        },
        {
            "entity_id": "e008",
            "entity_type": "MAJOR",
            "span_text": "计算机科学与技术",
            "block_id": "b4",
            "parent_entity_id": "e007",
            "role_in_parent": "school_degree"
        },
        {
            "entity_id": "e009",
            "entity_type": "DEGREE",
            "span_text": "硕士",
            "block_id": "b4",
            "parent_entity_id": "e007",
            "role_in_parent": "school_degree"
        },
        {
            "entity_id": "e010",
            "entity_type": "PROJECT_NAME",
            "span_text": "智能推荐系统重构",
            "block_id": "b5"
        },
        {
            "entity_id": "e011",
            "entity_type": "PROJECT_ROLE",
            "span_text": "技术负责人",
            "block_id": "b5",
            "parent_entity_id": "e010",
            "role_in_parent": "project_role"
        },
        {
            "entity_id": "e012",
            "entity_type": "SKILL",
            "span_text": "Python",
            "block_id": "b2"
        },
        {
            "entity_id": "e013",
            "entity_type": "SKILL",
            "span_text": "TensorFlow",
            "block_id": "b2"
        },
        {
            "entity_id": "e014",
            "entity_type": "CERTIFICATE",
            "span_text": "PMP项目管理认证",
            "block_id": "b2"
        }
    ],
    "relations": [
        {"relation_id": "r001", "head_entity_id": "e004", "relation_type": "HAS_TITLE", "tail_entity_id": "e005"},
        {"relation_id": "r002", "head_entity_id": "e004", "relation_type": "HAS_PERIOD", "tail_entity_id": "e006"},
        {"relation_id": "r003", "head_entity_id": "e007", "relation_type": "HAS_TITLE", "tail_entity_id": "e008"},
        {"relation_id": "r004", "head_entity_id": "e007", "relation_type": "HAS_TITLE", "tail_entity_id": "e009"},
        {"relation_id": "r005", "head_entity_id": "e010", "relation_type": "HAS_ROLE", "tail_entity_id": "e011"},
        {"relation_id": "r006", "head_entity_id": "e001", "relation_type": "HAS_SKILL", "tail_entity_id": "e012"},
        {"relation_id": "r007", "head_entity_id": "e001", "relation_type": "HAS_SKILL", "tail_entity_id": "e013"},
        {"relation_id": "r008", "head_entity_id": "e001", "relation_type": "HAS_CERTIFICATE", "tail_entity_id": "e014"}
    ]
}

# ============== 简历文本示例 ==============
SAMPLE_RESUME_TEXT = """
【基本信息】
张伟
手机：13800138000
邮箱：zhangwei@example.com

【专业技能】
精通 Python、TensorFlow、PyTorch，熟悉分布式训练框架
持有 PMP 项目管理认证

【工作经历】
字节跳动 | 高级算法工程师 | 2021/06 - 至今
负责推荐算法优化，将 CTR 提升 15%
主导多模态内容理解项目

【教育经历】
清华大学 | 计算机科学与技术 | 硕士 | 2018/09 - 2021/06
研究方向：机器学习与数据挖掘

【项目经历】
智能推荐系统重构 | 技术负责人 | 2022/03 - 2022/12
带领 5 人团队完成推荐系统架构升级
引入深度模型替代传统协同过滤
"""

# ============== 按 block 切分 ==============
SAMPLE_BLOCKS = [
    {
        "block_id": "b1",
        "block_type": "PERSONAL_INFO",
        "text": "张伟\n手机：13800138000\n邮箱：zhangwei@example.com"
    },
    {
        "block_id": "b2",
        "block_type": "SKILLS_CERTS",
        "text": "精通 Python、TensorFlow、PyTorch，熟悉分布式训练框架\n持有 PMP 项目管理认证"
    },
    {
        "block_id": "b3",
        "block_type": "WORK_EXPERIENCE",
        "text": "字节跳动 | 高级算法工程师 | 2021/06 - 至今\n负责推荐算法优化，将 CTR 提升 15%\n主导多模态内容理解项目"
    },
    {
        "block_id": "b4",
        "block_type": "EDUCATION",
        "text": "清华大学 | 计算机科学与技术 | 硕士 | 2018/09 - 2021/06\n研究方向：机器学习与数据挖掘"
    },
    {
        "block_id": "b5",
        "block_type": "PROJECT_EXPERIENCE",
        "text": "智能推荐系统重构 | 技术负责人 | 2022/03 - 2022/12\n带领 5 人团队完成推荐系统架构升级\n引入深度模型替代传统协同过滤"
    }
]

def print_sample():
    """打印示例数据"""
    print("=" * 60)
    print("【冻结 Schema 示例】")
    print("=" * 60)
    print("\n📋 简历文本：")
    print(SAMPLE_RESUME_TEXT)
    print("\n" + "=" * 60)
    print("📦 Blocks 切分：")
    print("=" * 60)
    for block in SAMPLE_BLOCKS:
        print(f"\n{block['block_id']} [{block['block_type']}]:")
        print(f"  {block['text'][:50]}...")
    
    print("\n" + "=" * 60)
    print("🔍 实体抽取结果（按 Schema）：")
    print("=" * 60)
    print(f"\n共 {len(FROZEN_SCHEMA['entities'])} 个实体:")
    for e in FROZEN_SCHEMA['entities']:
        parent = f" → {e['parent_entity_id']}" if e.get('parent_entity_id') else ""
        print(f"  [{e['entity_id']}] {e['entity_type']:12s} | {e['span_text'][:20]:20s} | block:{e['block_id']}{parent}")
    
    print(f"\n\n共 {len(FROZEN_SCHEMA['relations'])} 个关系:")
    for r in FROZEN_SCHEMA['relations']:
        print(f"  [{r['relation_id']}] {r['head_entity_id']} --{r['relation_type']}--> {r['tail_entity_id']}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    print_sample()
    
    # 保存到文件
    output = {
        "schema": FROZEN_SCHEMA,
        "resume_text": SAMPLE_RESUME_TEXT,
        "blocks": SAMPLE_BLOCKS
    }
    out_path = Path(__file__).parent / "test_extraction_sample.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 已保存到: {out_path}")
