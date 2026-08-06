"""
Tool 1: graph_query — Neo4j 知识图谱查询
智能体根据用户意图动态生成 Cypher 查询, 驱动知识图谱可视化

安全说明:
- LLM 生成的 Cypher 必须经过 _validate_cypher() 验证
- 禁止的关键字: DETACH/DELETE/DROP/CREATE/SET/REMOVE/MERGE
"""

import json
import re
from typing import Dict, Any, List, Optional

from config.prompt_loader import load_prompt

from .base import BaseTool
from ...services.db import get_neo4j_client, Neo4jClient
from ...utils.logger import get_logger
from ...utils.llm_client import get_llm_client

logger = get_logger('cpa-agent.tool.graph_query')

FORBIDDEN_KEYWORDS = frozenset({
    'DETACH', 'DELETE', 'DROP', 'CREATE', 'SET', 'REMOVE', 'MERGE',
    'CALL', 'LOAD', 'FOREACH', 'UNWIND', 'BEGIN', 'COMMIT', 'ROLLBACK'
})

SAFE_PATTERNS = frozenset({
    'MATCH', 'OPTIONAL MATCH', 'WHERE', 'RETURN', 'WITH', 'ORDER BY',
    'SKIP', 'LIMIT', 'COUNT', 'COLLECT', 'DISTINCT', 'UNWIND'
})


class GraphQueryTool(BaseTool):
    """Neo4j 图谱查询工具 (Text-to-Cypher)"""

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None, llm_client=None):
        self._neo4j = neo4j_client
        self._llm = llm_client

    @property
    def neo4j(self) -> Neo4jClient:
        if self._neo4j is None:
            self._neo4j = get_neo4j_client()
        return self._neo4j

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm_client()
        return self._llm

    @property
    def name(self) -> str:
        return "graph_query"

    @property
    def description(self) -> str:
        return """\
【知识图谱查询 — Neo4j Text-to-Cypher】
根据你的自然语言问题, 自动生成 Cypher 查询并执行, 返回图谱中的节点和关系。

【数据范围】
- 技能节点 (~500个): 6大类 → 24子类, 含 embedding 和别名
- 岗位画像 (≥10个): 聚合画像 + 四维需求
- 职业路径: 晋升边 (≥3链) + 换岗边 (≥5×2)
- 实体关系: 19种实体 + 24种关系 (D1-D4 四维映射)

【使用场景】
- "Java后端的晋升路径是什么"
- "前端转AI算法需要补什么技能"
- "哪些岗位需要Python"
- "React和Vue有什么关联技能"

【返回内容】
- 匹配的节点列表 (名称/类型/属性)
- 关系列表 (起点→关系→终点)
- 路径信息 (如晋升链/换岗路径)"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "query": "自然语言查询问题",
            "query_type": "查询类型提示 (可选: path/neighbors/statistics)"
        }

    def _get_schema_prompt(self) -> str:
        """获取图谱 Schema 提示"""
        try:
            schema = self.neo4j.get_schema()
            node_info = []
            for label in schema.get("node_labels", [])[:20]:
                count = schema.get("node_counts", {}).get(label, "N/A")
                node_info.append(f"- {label}: {count} 个节点")

            rel_info = []
            for rel_type in schema.get("relationship_types", [])[:20]:
                rel_info.append(f"- {rel_type}")

            return f"""\
图谱 Schema:
节点类型 ({len(schema.get('node_labels', []))} 种):
{chr(10).join(node_info)}

关系类型 ({len(schema.get('relationship_types', []))} 种):
{chr(10).join(rel_info)}"""
        except Exception as e:
            logger.warning(f"获取 schema 失败: {e}")
            return "图谱 schema 暂不可用"

    def _generate_cypher(self, user_query: str, query_type: str = "") -> str:
        """使用 LLM 生成 Cypher 查询"""
        # 方法内导入：避免进程未热重载时模块级 NameError 残留
        from config.prompt_loader import load_prompt as _load_prompt

        schema_prompt = self._get_schema_prompt()

        system_prompt = _load_prompt(
            "tool.graph_query.system",
            schema=schema_prompt,
            query_type=query_type if query_type else "自动判断",
        )

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
                ],
                temperature=0.1,
                max_tokens=500,
            )
            if response:
                cypher = response.strip()
                if cypher.startswith("```cypher"):
                    cypher = cypher[9:]
                if cypher.startswith("```"):
                    cypher = cypher[3:]
                if cypher.endswith("```"):
                    cypher = cypher[:-3]
                return cypher.strip()
        except Exception as e:
            logger.error(f"Cypher 生成失败: {e}")

        return ""

    def _format_results(
        self,
        nodes: List[Dict],
        edges: List[Dict],
    ) -> str:
        """格式化查询结果"""
        parts = []

        if nodes:
            parts.append(f"找到 {len(nodes)} 个节点:")
            for i, node in enumerate(nodes[:10]):
                labels = list(node.keys())
                parts.append(f"  {i+1}. {node.get('n', node)}")
            if len(nodes) > 10:
                parts.append(f"  ... 还有 {len(nodes) - 10} 个节点")

        if edges:
            parts.append(f"\n找到 {len(edges)} 条关系:")
            for i, edge in enumerate(edges[:10]):
                parts.append(f"  {i+1}. {edge}")
            if len(edges) > 10:
                parts.append(f"  ... 还有 {len(edges) - 10} 条关系")

        if not nodes and not edges:
            parts.append("未找到匹配的数据")

        return "\n".join(parts)

    def _validate_cypher(self, cypher: str) -> tuple:
        """
        验证 Cypher 安全性

        Returns:
            (is_valid, error_message)
        """
        if not cypher or not cypher.strip():
            return False, "Cypher 为空"

        cypher_upper = cypher.upper()
        # SAFE_PATTERNS 为规范大小写；必须与 upper 后的语句比较（旧代码用 pattern.lower() 导致 MATCH 永远不通过）
        cypher_stripped = cypher_upper.lstrip()
        has_safe_start = any(
            cypher_stripped.startswith(pattern.upper()) for pattern in SAFE_PATTERNS
        )
        if not has_safe_start:
            return False, f"查询必须以 MATCH/RETURN 等开头，当前: {cypher[:50]}..."

        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(r'\b' + keyword + r'\b', cypher_upper):
                return False, f"查询包含禁止关键字: {keyword}"

        comment_pattern = r'(//.*?$|/\*.*?\*/|#.*?$)'
        clean_cypher = re.sub(comment_pattern, '', cypher_upper, flags=re.MULTILINE)
        for keyword in FORBIDDEN_KEYWORDS:
            if keyword in clean_cypher:
                return False, f"查询包含禁止关键字: {keyword}"

        return True, ""

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        query_type = kwargs.get("query_type", "")

        if not query:
            return "[graph_query] 错误: query 参数不能为空"

        logger.info(f"graph_query 执行: query={query}, type={query_type}")

        try:
            cypher = self._generate_cypher(query, query_type)
            if not cypher:
                return "[graph_query] 无法生成查询语句，请重述问题"

            is_valid, error_msg = self._validate_cypher(cypher)
            if not is_valid:
                logger.warning(f"Cypher 安全校验失败: {error_msg}")
                return f"[graph_query] 安全校验失败: {error_msg}"

            logger.debug(f"生成的 Cypher: {cypher}")

            results = self.neo4j.execute_query(cypher, read_only=True)

            nodes = []
            edges = []

            for record in results:
                for value in record.values():
                    if hasattr(value, 'labels'):
                        nodes.append({
                            "labels": list(value.labels()),
                            "properties": dict(value)
                        })
                    elif hasattr(value, 'type'):
                        edges.append({
                            "type": value.type(),
                            "start": dict(value.start_node),
                            "end": dict(value.end_node),
                            "properties": dict(value)
                        })
                    elif isinstance(value, list):
                        for item in value:
                            if hasattr(item, 'labels'):
                                nodes.append({
                                    "labels": list(item.labels()),
                                    "properties": dict(item)
                                })
                            elif hasattr(item, 'type'):
                                edges.append({
                                    "type": item.type(),
                                    "start": dict(item.start_node),
                                    "end": dict(item.end_node),
                                })

            return self._format_results(nodes, edges)

        except Exception as e:
            logger.error(f"graph_query 执行失败: {e}")
            return f"[graph_query] 执行失败，请稍后重试"
