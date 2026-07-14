"""
Text-to-Cypher 安全模块框架
CPA-Agent 职业能力图谱问答模块
依赖现有后端服务: LLMClient, Neo4jClient
"""

import re
import json
import time
from typing import Optional, Literal, TypedDict, Any, List
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from app.utils.llm_client import get_llm_client
from app.services.db.neo4j_client import Neo4jClient
from app.config import Config
from app.utils.logger import get_logger
from config.prompt_loader import load_prompt

logger = get_logger('cpa-agent.text_cyber')


class CypherSafetyLevel(str, Enum):
    SAFE = "safe"
    SYNTAX_ERROR = "syntax_error"
    WRITE_BLOCKED = "write_blocked"
    INJECTION_RISK = "injection_risk"
    UNKNOWN_TEMPLATE = "unknown_template"


class IntentType(str, Enum):
    NODE_SEARCH = "node_search"
    RELATIONSHIP_QUERY = "relationship_query"
    PATH_FINDING = "path_finding"
    AGGREGATION = "aggregation"
    SCHEMA_INSPECTION = "schema_inspection"
    SIMILARITY_SEARCH = "similarity_search"
    CUSTOM = "custom"


@dataclass
class CypherTemplate:
    name: str
    intent: IntentType
    description: str
    template: str
    required_params: list[str]
    example_queries: list[str]

    def fill(self, params: dict) -> str:
        safe_params = {k: self._escape(v) for k, v in params.items()
                      if k in self.required_params}
        return self.template.format(**safe_params)

    @staticmethod
    def _escape(value: Any) -> str:
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            return f"'{escaped}'"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, list):
            return "[" + ", ".join(CypherTemplate._escape(v) for v in value) + "]"
        return str(value)


class CypherResult(BaseModel):
    original_query: str = Field(description="原始自然语言查询")
    generated_cypher: str = Field(description="生成的Cypher语句")
    safety_level: CypherSafetyLevel = Field(description="安全等级")
    execution_result: Optional[list[dict]] = Field(default=None, description="查询结果")
    natural_response: str = Field(description="自然语言回复")
    execution_time_ms: Optional[float] = Field(default=None)


class TemplateLibrary:
    TEMPLATES = [
        CypherTemplate(
            name="search_skills_by_category",
            intent=IntentType.NODE_SEARCH,
            description="按分类搜索技能节点",
            template="MATCH (s:Skill {category: {category}}) RETURN s.name AS skill_name, s.category AS category LIMIT {limit}",
            required_params=["category", "limit"],
            example_queries=[
                "查找所有前端技能",
                "搜索后端相关的技能",
                "查询数据库技能有哪些"
            ]
        ),
        CypherTemplate(
            name="search_skills_by_name",
            intent=IntentType.NODE_SEARCH,
            description="按名称搜索技能节点",
            template="MATCH (s:Skill) WHERE s.name CONTAINS {keyword} RETURN s.name AS skill_name, s.category AS category LIMIT {limit}",
            required_params=["keyword", "limit"],
            example_queries=[
                "查找包含Vue的技能",
                "搜索Python相关技能",
                "查询JavaScript有哪些"
            ]
        ),
        CypherTemplate(
            name="get_job_portrait_skills",
            intent=IntentType.RELATIONSHIP_QUERY,
            description="获取岗位画像的必备技能",
            template="MATCH (j:JobPortrait {position_name: {position_name}})-[r:REQUIRES]->(s:Skill) RETURN s.name AS skill_name, r.level AS required_level LIMIT {limit}",
            required_params=["position_name", "limit"],
            example_queries=[
                "前端开发工程师需要哪些技能",
                "查找Java工程师的技能要求",
                "产品经理需要什么技能"
            ]
        ),
        CypherTemplate(
            name="get_skill_prerequisites",
            intent=IntentType.RELATIONSHIP_QUERY,
            description="获取技能的前置技能",
            template="MATCH (s:Skill {name: {skill_name}})<-[r:PREREQUISITE]-(pre:Skill) RETURN pre.name AS prerequisite_skill, r.confidence AS confidence LIMIT {limit}",
            required_params=["skill_name", "limit"],
            example_queries=[
                "Vue 3的前置技能是什么",
                "学习React需要什么基础",
                "掌握某技能前需要学会什么"
            ]
        ),
        CypherTemplate(
            name="get_career_path",
            intent=IntentType.PATH_FINDING,
            description="查询职业晋升路径",
            template="""
            MATCH path = (start:CareerNode {position_name: {start_position}})-[:PROMOTION*1..3]->(end:CareerNode {position_name: {end_position}})
            RETURN path, length(path) AS steps
            LIMIT {limit}
            """,
            required_params=["start_position", "end_position", "limit"],
            example_queries=[
                "从前端开发到技术经理怎么发展",
                "初级工程师如何晋升到高级工程师",
                "查询职业发展路径"
            ]
        ),
        CypherTemplate(
            name="get_similar_skills",
            intent=IntentType.SIMILARITY_SEARCH,
            description="查询相似技能",
            template="""
            MATCH (target:Skill {name: {skill_name}})-[:REQUIRES|PREFERS|BONUS]->(intermediate)<-|[:REQUIRES|PREFERS|BONUS]-(similar:Skill)
            WHERE target <> similar
            RETURN similar.name AS skill_name, count(intermediate) AS common_count
            ORDER BY common_count DESC
            LIMIT {limit}
            """,
            required_params=["skill_name", "limit"],
            example_queries=[
                "和Vue类似的框架有哪些",
                "Python还能做什么",
                "有哪些技能和React相关"
            ]
        ),
        CypherTemplate(
            name="get_skill_category_hierarchy",
            intent=IntentType.SCHEMA_INSPECTION,
            description="获取技能分类层级",
            template="MATCH (c:SkillCategory) RETURN c.name AS category, c.level AS level, c.parent_name AS parent LIMIT {limit}",
            required_params=["limit"],
            example_queries=[
                "技能分类有哪些",
                "查看技能体系结构",
                "技能是怎么分类的"
            ]
        ),
        CypherTemplate(
            name="count_skills_by_category",
            intent=IntentType.AGGREGATION,
            description="统计各类别技能数量",
            template="MATCH (s:Skill) RETURN s.category AS category, count(s) AS skill_count ORDER BY skill_count DESC LIMIT {limit}",
            required_params=["limit"],
            example_queries=[
                "每类技能有多少个",
                "前端技能占比多少",
                "各类技能分布如何"
            ]
        ),
        CypherTemplate(
            name="get_job_portrait_requirements",
            intent=IntentType.NODE_SEARCH,
            description="获取岗位画像详细信息",
            template="MATCH (j:JobPortrait {position_name: {position_name}}) RETURN j.portrait_id AS portrait_id, j.position_name AS position_name, j.required_degree AS required_degree, j.required_skill_cnt AS skill_count LIMIT {limit}",
            required_params=["position_name", "limit"],
            example_queries=[
                "前端开发工程师的岗位要求",
                "查看后端开发的具体要求",
                "产品经理岗位画像是什么"
            ]
        ),
        CypherTemplate(
            name="get_learning_sequence",
            intent=IntentType.PATH_FINDING,
            description="获取技能学习顺序",
            template="""
            MATCH path = (s:Skill {name: {skill_name}})<-[:PREREQUISITE*1..3]-(pre:Skill)
            RETURN pre.name AS prerequisite, length(path) AS distance
            ORDER BY distance
            LIMIT {limit}
            """,
            required_params=["skill_name", "limit"],
            example_queries=[
                "学习React的最佳顺序是什么",
                "Vue应该怎么学",
                "按什么顺序掌握技能最好"
            ]
        ),
    ]

    def __init__(self):
        self.template_map = {t.name: t for t in self.TEMPLATES}
        self.intent_index: dict[IntentType, list[CypherTemplate]] = {}
        for t in self.TEMPLATES:
            self.intent_index.setdefault(t.intent, []).append(t)

    def get_template(self, name: str) -> Optional[CypherTemplate]:
        return self.template_map.get(name)

    def get_by_intent(self, intent: IntentType) -> list[CypherTemplate]:
        return self.intent_index.get(intent, [])

    def list_all(self) -> list[CypherTemplate]:
        return self.TEMPLATES


class CypherValidator:
    WRITE_KEYWORDS = [
        'CREATE', 'DELETE', 'DETACH', 'REMOVE', 'SET', 'MERGE',
        'DROP', 'CALL', 'LOAD', 'apoc', 'gds', 'algo'
    ]

    DANGEROUS_PATTERNS = [
        r'apoc\.\w+',
        r'gds\.\w+',
        r'dbms\.\w+',
        r'CALL\s+.*\.',
        r'LOAD\s+CSV',
        r'LOAD\s+JSON',
        r'USING\s+PERIODIC',
        r'FOREACH\s*\(',
    ]

    SYNTAX_PATTERNS = {
        'match_clause': r'MATCH\s+',
        'return_clause': r'RETURN\s+',
        'balanced_parentheses': r'\(.*\)',
    }

    @classmethod
    def validate(cls, cypher: str) -> tuple[bool, CypherSafetyLevel, str]:
        if not cypher or not cypher.strip():
            return False, CypherSafetyLevel.SYNTAX_ERROR, "空查询"

        upper_cypher = cypher.upper().strip()

        for keyword in cls.WRITE_KEYWORDS:
            pattern = r'\b' + keyword + r'\b'
            if re.search(pattern, upper_cypher):
                return False, CypherSafetyLevel.WRITE_BLOCKED, \
                       f"检测到写操作关键字: {keyword}，仅允许READ查询"

        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, cypher, re.IGNORECASE):
                return False, CypherSafetyLevel.INJECTION_RISK, \
                       f"检测到危险模式: {pattern}"

        if not re.search(cls.SYNTAX_PATTERNS['match_clause'], upper_cypher):
            if not upper_cypher.startswith('CALL DB.'):
                return False, CypherSafetyLevel.SYNTAX_ERROR, \
                       "缺少MATCH子句或非法查询结构"

        open_count = cypher.count('(')
        close_count = cypher.count(')')
        if open_count != close_count:
            return False, CypherSafetyLevel.SYNTAX_ERROR, \
                   f"括号不匹配: 开{open_count} 闭{close_count}"

        single_quotes = cypher.count("'") - cypher.count("\\'")
        if single_quotes % 2 != 0:
            return False, CypherSafetyLevel.SYNTAX_ERROR, "单引号不匹配"

        return True, CypherSafetyLevel.SAFE, "校验通过"


class IntentUnderstandingLayer:
    def __init__(self, model: str = None):
        self.llm = get_llm_client()
        self.model = model or Config.LLM_MODEL_NAME or "gpt-4o-mini"
        self.template_lib = TemplateLibrary()

    def understand(self, query: str) -> dict:
        intent_prompt = load_prompt("cypher.intent.user", query=query)

        try:
            result = self.llm.chat_json(
                messages=[{"role": "user", "content": intent_prompt}],
                temperature=0
            )
            return result
        except Exception as e:
            logger.error(f"意图理解失败: {e}")
            return {
                "intent": "custom",
                "template_name": None,
                "params": {},
                "confidence": 0.0,
                "needs_custom_generation": True,
                "explanation": f"意图解析失败: {str(e)}"
            }

    def generate_custom_cypher(self, query: str) -> str:
        custom_prompt = load_prompt("cypher.generate.user", query=query)

        try:
            result = self.llm.chat(
                messages=[{"role": "user", "content": custom_prompt}],
                temperature=0
            )
            cleaned = result.strip()
            cleaned = re.sub(r'^```(?:cypher)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\n?```\s*$', '', cleaned)
            return cleaned.strip()
        except Exception as e:
            logger.error(f"Cypher生成失败: {e}")
            raise


class ResultRewriter:
    def __init__(self):
        self.llm = get_llm_client()

    def rewrite(self, original_query: str, result: Optional[list[dict]]) -> str:
        result_json = json.dumps(result, ensure_ascii=False, indent=2) if result else "[]"
        rewrite_prompt = load_prompt(
            "cypher.rewrite.user",
            original_query=original_query,
            result_json=result_json,
        )

        try:
            return self.llm.chat(
                messages=[{"role": "user", "content": rewrite_prompt}],
                temperature=0.7
            )
        except Exception as e:
            logger.error(f"结果改写失败: {e}")
            return f"查询完成，但结果转换失败: {str(e)}"


class TextToCypherModule:
    def __init__(self, neo4j_client: Neo4jClient = None):
        self.neo4j = neo4j_client or Neo4jClient()
        self.intent_layer = IntentUnderstandingLayer()
        self.validator = CypherValidator()
        self.rewriter = ResultRewriter()
        self.template_lib = TemplateLibrary()

    def query(self, natural_language: str) -> CypherResult:
        intent_result = self.intent_layer.understand(natural_language)

        cypher = None
        safety = CypherSafetyLevel.SAFE
        safety_msg = ""

        if (intent_result.get("template_name")
            and not intent_result.get("needs_custom_generation", False)
            and intent_result.get("confidence", 0) > 0.7):

            template = self.template_lib.get_template(intent_result["template_name"])
            if template:
                try:
                    cypher = template.fill(intent_result.get("params", {}))
                except KeyError as e:
                    logger.warning(f"模板参数缺失，降级到LLM生成: {e}")
                    cypher = self.intent_layer.generate_custom_cypher(natural_language)
            else:
                cypher = self.intent_layer.generate_custom_cypher(natural_language)
        else:
            cypher = self.intent_layer.generate_custom_cypher(natural_language)

        is_valid, safety, safety_msg = self.validator.validate(cypher)

        if not is_valid:
            logger.warning(f"查询被拒绝: {safety_msg}, cypher: {cypher}")
            return CypherResult(
                original_query=natural_language,
                generated_cypher=cypher,
                safety_level=safety,
                execution_result=None,
                natural_response=f"查询被拒绝: {safety_msg}",
                execution_time_ms=None
            )

        execution_result = None
        execution_time = None
        try:
            start = time.time()
            execution_result = self.neo4j.execute_query(cypher, read_only=True)
            execution_time = (time.time() - start) * 1000
            logger.info(f"Cypher执行成功，耗时: {execution_time:.2f}ms")

        except Exception as e:
            logger.error(f"查询执行失败: {e}")
            return CypherResult(
                original_query=natural_language,
                generated_cypher=cypher,
                safety_level=CypherSafetyLevel.SYNTAX_ERROR,
                execution_result=None,
                natural_response=f"查询执行失败: {str(e)}",
                execution_time_ms=None
            )

        natural_response = self.rewriter.rewrite(natural_language, execution_result)

        return CypherResult(
            original_query=natural_language,
            generated_cypher=cypher,
            safety_level=safety,
            execution_result=execution_result,
            natural_response=natural_response,
            execution_time_ms=execution_time
        )

    def explain_query(self, result: CypherResult) -> str:
        return f"""
=== Text-to-Cypher 执行报告 ===
原始查询: {result.original_query}
生成Cypher: {result.generated_cypher}
安全等级: {result.safety_level.value}
执行耗时: {result.execution_time_ms:.2f}ms if result.execution_time_ms else 'N/A'
结果条数: {len(result.execution_result) if result.execution_result else 0}

自然语言回复:
{result.natural_response}
================================
"""


_text_to_cypher_module: Optional[TextToCypherModule] = None


def get_text_to_cypher_module() -> TextToCypherModule:
    global _text_to_cypher_module
    if _text_to_cypher_module is None:
        _text_to_cypher_module = TextToCypherModule()
    return _text_to_cypher_module


if __name__ == "__main__":
    module = TextToCypherModule()

    test_queries = [
        "查找所有前端技能",
        "Vue 3的前置技能是什么",
        "前端开发工程师需要哪些技能",
        "和React类似的框架有哪些",
        "技能分类有哪些",
        "学习React的最佳顺序是什么",
    ]

    for q in test_queries:
        print(f"\n{'='*60}")
        print(f"用户: {q}")
        result = module.query(q)
        print(f"安全等级: {result.safety_level.value}")
        print(f"Cypher: {result.generated_cypher}")
        print(f"回复: {result.natural_response}")
        if result.execution_result:
            print(f"数据: {json.dumps(result.execution_result, ensure_ascii=False)[:200]}...")