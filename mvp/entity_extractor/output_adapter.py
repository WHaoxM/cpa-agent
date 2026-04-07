"""
OutputAdapter: Phase 3 → Phase 4 格式适配器

Phase 3 (ReasoningCore) 输出 task-specific 扁平 JSON:
  - basic_info  → {"name": "...", "phone": "...", ...}
  - education   → {"school": "...", "degree": "...", ...}
  - experience  → {"company": "...", "title": "...", ...}
  - skills      → {"skills": [...]}

Phase 4 (LogicGuard) 期望:
  {"entities": [{"type": "...", "span": "...", ...}],
   "relations": [{"head_entity_id": ..., ...}]}

本模块负责这个格式转换，使 LogicGuard 只做验证不做转换。
"""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# task_type → 实体映射规则
# ---------------------------------------------------------------------------

BASIC_INFO_FIELDS = {
    "name":     "PERSON_NAME",
    "phone":    "PHONE",
    "email":    "EMAIL",
    "position": "POSITION",
    "location": "LOCATION",
    "address":  "LOCATION",
    "company":  "COMPANY",
    "project":  "PROJECT_NAME",
}

EDUCATION_FIELDS = {
    "school":     "SCHOOL",
    "degree":     "DEGREE",
    "major":      "MAJOR",
    "start_date": "DATE_RANGE",
    "end_date":   "DATE_RANGE",
    "gpa":        "METRIC",
}

EXPERIENCE_FIELDS = {
    "company":      "COMPANY",
    "title":        "POSITION",
    "position":     "POSITION",
    "role":         "PROJECT_ROLE",
    "name":         "PROJECT_NAME",
    "project_name": "PROJECT_NAME",
    "project":      "PROJECT_NAME",
    "start_date":   "DATE_RANGE",
    "end_date":     "DATE_RANGE",
    "description":  "DESCRIPTION",
}

SKILL_TYPE = "SKILL"


# ---------------------------------------------------------------------------
# 关系推导规则
# ---------------------------------------------------------------------------

RELATION_RULES = {
    "education": [
        ("SCHOOL", "DEGREE",      "HAS_DEGREE"),
        ("SCHOOL", "MAJOR",       "HAS_MAJOR"),
        ("SCHOOL", "DATE_RANGE",  "HAS_PERIOD"),
    ],
    "experience": [
        ("COMPANY",      "POSITION",     "HAS_ROLE"),
        ("COMPANY",      "DATE_RANGE",   "HAS_PERIOD"),
        ("PROJECT_NAME", "PROJECT_ROLE", "HAS_ROLE"),
        ("PROJECT_NAME", "SKILL",       "HAS_SKILL"),
        ("PROJECT_NAME", "DATE_RANGE",  "HAS_PERIOD"),
    ],
}


class OutputAdapter:
    """将 ReasoningCore 的 task-specific JSON 转为契约 entities/relations 格式。"""

    @staticmethod
    def adapt(
        task_output: Dict[str, Any],
        task_type: str,
        block_type: str = "",
    ) -> Dict[str, Any]:
        """
        Parameters
        ----------
        task_output : dict
            ReasoningCore.infer() 返回的 output 字段
        task_type : str
            推理任务类型 (basic_info / education / experience / skills)
        block_type : str
            原始块类型 (SKILLS_CERTS / EDUCATION / ...)

        Returns
        -------
        dict  {"entities": [...], "relations": [...]}
        """
        if not task_output or not isinstance(task_output, dict):
            return {"entities": [], "relations": []}

        entities: List[Dict[str, Any]] = []
        relations: List[Dict[str, Any]] = []

        if task_type == "skills":
            entities = OutputAdapter._adapt_skills(task_output)
        elif task_type == "basic_info":
            entities = OutputAdapter._adapt_kv(task_output, BASIC_INFO_FIELDS)
        elif task_type == "education":
            entities = OutputAdapter._adapt_kv(task_output, EDUCATION_FIELDS)
            # 处理嵌套 education 列表
            edu_items = task_output.get("education", [])
            if isinstance(edu_items, list):
                for item in edu_items:
                    if isinstance(item, dict):
                        entities.extend(OutputAdapter._adapt_kv(item, EDUCATION_FIELDS))
            relations = OutputAdapter._infer_relations(entities, "education")
        elif task_type == "experience":
            entities = OutputAdapter._adapt_kv(task_output, EXPERIENCE_FIELDS)
            # 新 prompt 格式: experience 输出可能包含 skills 列表
            inline_skills = task_output.get("skills", [])
            if isinstance(inline_skills, list):
                for s in inline_skills:
                    if isinstance(s, str) and s.strip():
                        entities.append({"type": SKILL_TYPE, "span": s.strip()})
            # 处理嵌套 work_experience / projects 列表
            for list_key in ("work_experience", "projects"):
                items = task_output.get(list_key, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            entities.extend(OutputAdapter._adapt_kv(item, EXPERIENCE_FIELDS))
                            for s in item.get("skills", item.get("tech_stack", [])):
                                if isinstance(s, str) and s.strip():
                                    entities.append({"type": SKILL_TYPE, "span": s.strip()})
            relations = OutputAdapter._infer_relations(entities, "experience")
        else:
            # 未知 task_type，尝试通用 KV 适配
            entities = OutputAdapter._adapt_generic(task_output, block_type)

        return {"entities": entities, "relations": relations}

    # ------------------------------------------------------------------
    # 技能列表适配
    # ------------------------------------------------------------------

    @staticmethod
    def _adapt_skills(output: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities = []
        skills = output.get("skills", [])
        if isinstance(skills, list):
            for s in skills:
                if isinstance(s, str) and s.strip():
                    entities.append({"type": SKILL_TYPE, "span": s.strip()})
        # 技能等级 / 类别等额外字段
        for key in ("skill_categories", "categories"):
            cats = output.get(key, {})
            if isinstance(cats, dict):
                for cat_name, cat_skills in cats.items():
                    if isinstance(cat_skills, list):
                        for s in cat_skills:
                            if isinstance(s, str) and s.strip():
                                ent = {"type": SKILL_TYPE, "span": s.strip(), "category": cat_name}
                                if ent not in entities:
                                    entities.append(ent)
        return entities

    # ------------------------------------------------------------------
    # KV 字段适配 (basic_info / education / experience)
    # ------------------------------------------------------------------

    @staticmethod
    def _adapt_kv(
        output: Dict[str, Any],
        field_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        entities = []
        for field_name, entity_type in field_map.items():
            value = output.get(field_name)
            if value is None:
                continue
            # line-range pointer [start, end]
            if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
                entities.append({
                    "type": entity_type,
                    "span": f"[line:{value[0]}-{value[1]}]",
                    "line_range": value,
                })
            elif isinstance(value, str) and value.strip():
                entities.append({"type": entity_type, "span": value.strip()})
            elif isinstance(value, (int, float)):
                entities.append({"type": entity_type, "span": str(value)})
        # 处理嵌套列表 (如 education 可能是 list of dicts)
        for key in ("items", "entries", "list"):
            items = output.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        entities.extend(OutputAdapter._adapt_kv(item, field_map))
        return entities

    # ------------------------------------------------------------------
    # 关系自动推导
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_relations(
        entities: List[Dict[str, Any]],
        domain: str,
    ) -> List[Dict[str, Any]]:
        rules = RELATION_RULES.get(domain, [])
        if not rules or not entities:
            return []

        # 按 type 建索引
        type_to_ids: Dict[str, List[int]] = {}
        for i, e in enumerate(entities):
            t = e.get("type", "")
            type_to_ids.setdefault(t, []).append(i)

        relations = []
        for head_type, tail_type, rel_type in rules:
            for h_id in type_to_ids.get(head_type, []):
                for t_id in type_to_ids.get(tail_type, []):
                    if h_id != t_id:
                        relations.append({
                            "head_entity_id": h_id,
                            "tail_entity_id": t_id,
                            "relation_type": rel_type,
                        })
        return relations

    # ------------------------------------------------------------------
    # 通用 fallback：尝试把所有非空字段转为实体
    # ------------------------------------------------------------------

    @staticmethod
    def _adapt_generic(
        output: Dict[str, Any],
        block_type: str,
    ) -> List[Dict[str, Any]]:
        entities = []
        for k, v in output.items():
            if isinstance(v, str) and v.strip():
                entities.append({"type": k.upper(), "span": v.strip()})
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        entities.append({"type": k.upper(), "span": item.strip()})
        return entities
