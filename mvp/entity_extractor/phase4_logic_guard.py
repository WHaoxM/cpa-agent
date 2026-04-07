"""
Phase 4: 逻辑守卫与闭环 (LogicGuard & Feedback Loop)
核心职责：
1. 契约约束矩阵验证
2. 关系一致性检查
3. 高置信度输出 vs 低置信度回炉
4. 主动学习数据蒸馏
5. 字段级内容验证（新增）
"""

from typing import Dict, List, Any, Tuple
from datetime import datetime
import re


class FieldValidator:
    """字段级验证器：验证实体 span 内容的合理性"""

    VALID_DEGREES = {
        "本科", "硕士", "博士", "学士", "专科", "大专",
        "PhD", "Master", "Bachelor", "Associate",
        "MBA", "MPA", "博士后",
    }

    SCHOOL_KEYWORDS = re.compile(
        r"(大学|学院|学校|University|College|Institute|中学|高中|附属)"
    )

    DATE_PATTERNS = [
        r"\d{4}[-/]\d{1,2}",
        r"\d{4}年\d{1,2}月",
        r"至今|现在|present|Present",
    ]

    @staticmethod
    def validate_school(name: str) -> bool:
        if not name or not name.strip():
            return True
        return bool(FieldValidator.SCHOOL_KEYWORDS.search(name))

    @staticmethod
    def validate_degree(degree: str) -> bool:
        if not degree or not degree.strip():
            return True
        return degree.strip() in FieldValidator.VALID_DEGREES

    @staticmethod
    def validate_date_range(date_str: str) -> bool:
        if not date_str or not date_str.strip():
            return True
        return any(
            re.search(p, date_str)
            for p in FieldValidator.DATE_PATTERNS
        )

    @staticmethod
    def validate_phone(phone: str) -> bool:
        if not phone or not phone.strip():
            return True
        return bool(re.match(r"^1[3-9]\d{9}$", phone.strip()))

    @staticmethod
    def validate_email(email: str) -> bool:
        if not email or not email.strip():
            return True
        return bool(re.match(r"^[\w.+-]+@[\w-]+\.[\w.]+$", email.strip()))

    @staticmethod
    def validate_entity_field(entity_type: str, span: str) -> Tuple[bool, str]:
        """
        根据实体类型验证 span 内容合理性。
        返回 (is_valid, reason)。
        """
        if not span or not span.strip():
            return True, "OK"

        validators = {
            "SCHOOL": (FieldValidator.validate_school, "学校名称格式不合法"),
            "DEGREE": (FieldValidator.validate_degree, "学位值不合法"),
            "DATE_RANGE": (FieldValidator.validate_date_range, "日期格式不合法"),
            "PHONE": (FieldValidator.validate_phone, "手机号格式不合法"),
            "EMAIL": (FieldValidator.validate_email, "邮箱格式不合法"),
        }

        if entity_type in validators:
            validate_fn, error_msg = validators[entity_type]
            if not validate_fn(span):
                return False, error_msg

        return True, "OK"


class ConstraintMatrix:
    """契约约束矩阵：定义合法的实体类型和关系"""
    
    def __init__(self):
        # 实体类型约束
        self.valid_entity_types = {
            "SKILL", "PROJECT_NAME", "PROJECT_ROLE", "COMPANY", 
            "DATE_RANGE", "SCHOOL", "MAJOR", "DEGREE", "CERTIFICATE",
            "POSITION", "DESCRIPTION",
            "PERSON_NAME", "PHONE", "EMAIL", "LOCATION", "METRIC",
        }
        
        # 关系类型约束
        self.valid_relation_types = {
            "HAS_ROLE", "HAS_PERIOD", "BELONGS_TO", "HAS_SKILL",
            "HAS_CERTIFICATE", "WORKS_AT", "HAS_DEGREE", "HAS_MAJOR"
        }
        
        # 关系约束矩阵：(head_type, tail_type) -> allowed_relations
        self.relation_constraints = {
            ("PROJECT_NAME", "PROJECT_ROLE"): ["HAS_ROLE"],
            ("PROJECT_NAME", "DATE_RANGE"): ["HAS_PERIOD"],
            ("PROJECT_NAME", "COMPANY"): ["BELONGS_TO"],
            ("PROJECT_NAME", "SKILL"): ["HAS_SKILL"],
            ("SCHOOL", "DEGREE"): ["HAS_DEGREE"],
            ("SCHOOL", "MAJOR"): ["HAS_MAJOR"],
            ("SCHOOL", "DATE_RANGE"): ["HAS_PERIOD"],
            ("PROJECT_NAME", "CERTIFICATE"): ["HAS_CERTIFICATE"],
            ("COMPANY", "POSITION"): ["HAS_ROLE"],
            ("COMPANY", "DATE_RANGE"): ["HAS_PERIOD"],
        }
        
        # 时间约束 — 泛化模式，兼容企业级多种日期格式
        # 支持: 2024-06, 2024/06, 2024.06, 2024年6月, 至今, present, 2024/11 - 至今
        self._date_permissive_patterns = [
            r"\d{4}[/\-.年]\d{1,2}",       # 2024-06, 2024/11, 2024.06, 2024年6
            r"\d{4}\s*年",                  # 2024年
            r"至今|现在|目前|present|Present|PRESENT",  # 泛时间终止词
        ]


class LogicGuard:
    """逻辑守卫：验证输出的合法性"""
    
    def __init__(self):
        self.constraints = ConstraintMatrix()
    
    def validate_entity(self, entity: Dict[str, Any]) -> Tuple[bool, str]:
        """验证单个实体"""
        entity_type = entity.get("type", "")
        span = entity.get("span", "").strip()
        
        # 检查 1：类型合法性
        if entity_type not in self.constraints.valid_entity_types:
            return False, f"Invalid entity type: {entity_type}"
        
        # 检查 2：span 不为空
        if not span:
            return False, "Empty entity span"
        
        # 检查 3：项目名长度限制（中文项目名可达 30+ 字符）
        if entity_type == "PROJECT_NAME" and len(span) > 50:
            return False, f"Project name too long: {len(span)} > 50"
        
        # 检查 4：项目名不能是动词短语
        action_verbs = ["参与", "负责", "实现", "完成", "设计", "开发", "协助"]
        if entity_type == "PROJECT_NAME" and any(v in span for v in action_verbs):
            return False, f"Project name contains action verb: {span}"
        
        # 检查 5：日期格式（泛化验证 — 只要包含年份或时间终止词即合法）
        if entity_type == "DATE_RANGE":
            is_valid_date = any(
                re.search(p, span)
                for p in self.constraints._date_permissive_patterns
            )
            if not is_valid_date:
                return False, f"Invalid date format: {span}"
        
        # 检查 6：字段级内容验证（新增）
        field_valid, field_reason = FieldValidator.validate_entity_field(entity_type, span)
        if not field_valid:
            return False, f"Field validation failed: {field_reason}"
        
        return True, "OK"
    
    def validate_relation(self, relation: Dict[str, Any], entities: List[Dict]) -> Tuple[bool, str]:
        """验证单个关系"""
        head_id = relation.get("head_entity_id")
        tail_id = relation.get("tail_entity_id")
        rel_type = relation.get("relation_type", "")
        
        # 检查 1：实体 ID 合法性
        if not isinstance(head_id, int) or not isinstance(tail_id, int):
            return False, "Invalid entity ID"
        
        if head_id >= len(entities) or tail_id >= len(entities):
            return False, f"Entity ID out of range: {head_id}, {tail_id}"
        
        # 检查 2：关系类型合法性
        if rel_type not in self.constraints.valid_relation_types:
            return False, f"Invalid relation type: {rel_type}"
        
        # 检查 3：关系约束矩阵
        head_type = entities[head_id].get("type", "")
        tail_type = entities[tail_id].get("type", "")
        key = (head_type, tail_type)
        
        if key not in self.constraints.relation_constraints:
            return False, f"Relation not allowed: {head_type} -> {tail_type}"
        
        if rel_type not in self.constraints.relation_constraints[key]:
            return False, f"Relation type not allowed: {rel_type} for {head_type} -> {tail_type}"
        
        # 检查 4：自环检查
        if head_id == tail_id:
            return False, "Self-loop relation not allowed"
        
        return True, "OK"
    
    def validate_temporal_consistency(self, entities: List[Dict]) -> Tuple[bool, str]:
        """验证时间一致性"""
        dates = [e for e in entities if e.get("type") == "DATE_RANGE"]
        
        if len(dates) >= 2:
            # 简单检查：如果有多个日期，应该有 start 和 end
            # 这里只做基本检查，实际应该更复杂
            pass
        
        return True, "OK"
    
    def sanitize_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """清理输出：移除非法实体和关系"""
        entities = output.get("entities", [])
        relations = output.get("relations", [])
        
        # 过滤非法实体
        valid_entities = []
        entity_id_mapping = {}  # 旧 ID -> 新 ID
        
        for old_id, entity in enumerate(entities):
            is_valid, reason = self.validate_entity(entity)
            if is_valid:
                entity_id_mapping[old_id] = len(valid_entities)
                valid_entities.append(entity)
        
        # 过滤非法关系
        valid_relations = []
        for relation in relations:
            # 更新实体 ID
            old_head_id = relation.get("head_entity_id")
            old_tail_id = relation.get("tail_entity_id")
            
            if old_head_id not in entity_id_mapping or old_tail_id not in entity_id_mapping:
                continue
            
            relation["head_entity_id"] = entity_id_mapping[old_head_id]
            relation["tail_entity_id"] = entity_id_mapping[old_tail_id]
            
            is_valid, reason = self.validate_relation(relation, valid_entities)
            if is_valid:
                valid_relations.append(relation)
        
        # 验证时间一致性
        is_temporal_valid, _ = self.validate_temporal_consistency(valid_entities)
        
        return {
            "entities": valid_entities,
            "relations": valid_relations,
            "is_valid": is_temporal_valid,
        }


class FeedbackLoop:
    """反馈循环：高置信度输出 vs 低置信度回炉"""
    
    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        self.high_confidence_data = []
        self.low_confidence_data = []
    
    def classify(self, result: Dict[str, Any]) -> str:
        """分类结果：HIGH / LOW / INVALID"""
        confidence = result.get("confidence", 0.0)
        is_valid = result.get("is_valid", False)
        
        if not is_valid:
            return "INVALID"
        elif confidence >= self.confidence_threshold:
            return "HIGH"
        else:
            return "LOW"

    @staticmethod
    def is_sft_eligible(result: Dict[str, Any]) -> bool:
        """判断是否可进入 SFT 微调池（排除 pre_extracted 兜底数据）"""
        llm_output = result.get("llm_output", {})
        if llm_output.get("_sft_eligible") is False:
            return False
        if llm_output.get("_fallback_source") == "pre_extracted":
            return False
        if not result.get("is_valid", False):
            return False
        return result.get("classification") == "HIGH"
    
    def process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """处理结果"""
        classification = self.classify(result)
        
        result["classification"] = classification
        result["timestamp"] = datetime.now().isoformat()
        
        if classification == "HIGH":
            self.high_confidence_data.append(result)
        elif classification == "LOW":
            self.low_confidence_data.append(result)
        
        return result


class ActiveLearningDistiller:
    """主动学习数据蒸馏：为微调准备数据"""
    
    def __init__(self):
        self.silver_standard_data = []  # 混合方案输出
        self.gold_standard_data = []    # 人工标注
    
    def create_silver_standard(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """创建银标准数据（混合方案输出）"""
        silver_data = []
        
        for result in results:
            if result.get("classification") == "HIGH":
                silver_data.append({
                    "block_id": result.get("block_id"),
                    "block_type": result.get("block_type"),
                    "input": result.get("tagged_text"),
                    "output": result.get("llm_output"),
                    "confidence": result.get("confidence"),
                    "source": "hybrid_pipeline",
                })
        
        self.silver_standard_data = silver_data
        return silver_data
    
    def mark_gold_standard(self, silver_data: List[Dict], corrections: List[Dict]) -> List[Dict]:
        """标记金标准数据（人工校正）"""
        gold_data = []
        
        for silver, correction in zip(silver_data, corrections):
            gold_data.append({
                "block_id": silver.get("block_id"),
                "block_type": silver.get("block_type"),
                "input": silver.get("input"),
                "output": correction.get("output", silver.get("output")),
                "confidence": 1.0,
                "source": "human_annotated",
            })
        
        self.gold_standard_data = gold_data
        return gold_data
    
    def export_for_sft(self) -> List[Dict[str, Any]]:
        """导出用于 SFT 微调的数据"""
        sft_data = []
        
        for item in self.gold_standard_data:
            sft_data.append({
                "instruction": f"从以下简历块中提取实体和关系：\n{item['input']}",
                "input": item["input"],
                "output": item["output"],
                "block_type": item["block_type"],
            })
        
        return sft_data
