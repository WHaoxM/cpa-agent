"""
逻辑约束矩阵 — LogicGuard Phase 4 使用

定义实体验证规则和关系约束，确保抽取结果的逻辑一致性。
与 phase4_logic_guard.py 中的 ConstraintMatrix 配合使用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

# ---------------------------------------------------------------------------
# 实体验证规则
# ---------------------------------------------------------------------------

ENTITY_VALIDATION_RULES: Dict[str, Dict[str, Any]] = {
    "PERSON": {
        "required_fields": ["name"],
        "optional_fields": ["phone", "email", "position"],
        "field_constraints": {
            "name": {"min_len": 2, "max_len": 30},
            "phone": {"pattern": r"1[3-9]\d{9}"},
            "email": {"pattern": r".+@.+\..+"},
        },
    },
    "EDUCATION": {
        "required_fields": ["school", "degree"],
        "optional_fields": ["major", "start_date", "end_date", "gpa"],
        "field_constraints": {
            "school": {"min_len": 2, "max_len": 50},
            "degree": {"enum": ["博士", "硕士", "本科", "学士", "专科", "大专",
                                "PhD", "Master", "Bachelor", "Associate"]},
            "gpa": {"min_val": 0.0, "max_val": 5.0},
        },
    },
    "WORK_EXPERIENCE": {
        "required_fields": ["company"],
        "optional_fields": ["position", "start_date", "end_date", "description"],
        "field_constraints": {
            "company": {"min_len": 2, "max_len": 100},
        },
    },
    "PROJECT": {
        "required_fields": ["name"],
        "optional_fields": ["description", "start_date", "end_date", "role", "tech_stack"],
        "field_constraints": {
            "name": {"min_len": 2, "max_len": 100},
        },
    },
    "SKILL": {
        "required_fields": ["name"],
        "optional_fields": ["category", "proficiency"],
        "field_constraints": {
            "name": {"min_len": 1, "max_len": 50},
            "proficiency": {"enum": ["精通", "熟练", "熟悉", "了解",
                                     "expert", "proficient", "familiar", "basic"]},
        },
    },
}

# ---------------------------------------------------------------------------
# 关系约束矩阵 — (subject_type, object_type) → allowed_relations
# ---------------------------------------------------------------------------

RELATION_CONSTRAINT_MATRIX: Dict[tuple, Set[str]] = {
    ("PERSON", "EDUCATION"): {"studied_at"},
    ("PERSON", "WORK_EXPERIENCE"): {"worked_at"},
    ("PERSON", "PROJECT"): {"participated_in"},
    ("PERSON", "SKILL"): {"has_skill"},
    ("PROJECT", "SKILL"): {"uses_tech"},
    ("WORK_EXPERIENCE", "PROJECT"): {"includes_project"},
    ("EDUCATION", "PROJECT"): {"includes_project"},
}

# ---------------------------------------------------------------------------
# 时间一致性规则
# ---------------------------------------------------------------------------

TEMPORAL_RULES: List[Dict[str, Any]] = [
    {
        "rule_id": "edu_date_order",
        "entity_type": "EDUCATION",
        "constraint": "start_date <= end_date",
        "description": "教育经历开始日期不晚于结束日期",
    },
    {
        "rule_id": "work_date_order",
        "entity_type": "WORK_EXPERIENCE",
        "constraint": "start_date <= end_date",
        "description": "工作经历开始日期不晚于结束日期",
    },
    {
        "rule_id": "proj_date_order",
        "entity_type": "PROJECT",
        "constraint": "start_date <= end_date",
        "description": "项目经历开始日期不晚于结束日期",
    },
]


def validate_relation(subject_type: str, object_type: str, relation: str) -> bool:
    """检查关系是否在约束矩阵中"""
    key = (subject_type, object_type)
    allowed = RELATION_CONSTRAINT_MATRIX.get(key, set())
    return relation in allowed


def get_required_fields(entity_type: str) -> List[str]:
    """获取实体类型的必填字段"""
    rule = ENTITY_VALIDATION_RULES.get(entity_type, {})
    return rule.get("required_fields", [])
