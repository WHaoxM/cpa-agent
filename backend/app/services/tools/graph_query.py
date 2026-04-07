"""
Tool 1: graph_query — Neo4j 知识图谱查询
智能体根据用户意图动态生成 Cypher 查询, 驱动知识图谱可视化
"""

from typing import Dict

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.graph_query')


class GraphQueryTool(BaseTool):
    """Neo4j 图谱查询工具 (Text-to-Cypher)"""

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

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        query_type = kwargs.get("query_type", "")

        logger.info(f"graph_query 执行: query={query}, type={query_type}")

        # TODO: 实现 Text-to-Cypher 流程
        # 1. 将 Neo4j Schema (节点类型/关系类型/属性) 注入 Prompt
        # 2. LLM 生成 Cypher (只读 MATCH 语句)
        # 3. Neo4j driver 执行查询
        # 4. 格式化结果为文本

        return f"[graph_query] 查询: {query}\n暂未实现, 待接入 Neo4j 驱动"
