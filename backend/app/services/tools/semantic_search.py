"""
Tool 3: semantic_search — Milvus 向量语义检索
BGE-M3 Embedding 驱动的语义检索, 支持技能/JD/简历/对话 RAG 记忆
"""

from typing import Dict

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.semantic_search')


class SemanticSearchTool(BaseTool):
    """Milvus 向量语义检索工具"""

    @property
    def name(self) -> str:
        return "semantic_search"

    @property
    def description(self) -> str:
        return """\
【语义检索 — Milvus + BGE-M3】
基于向量相似度的语义检索, 从 Milvus 中查找与查询最相似的内容。

【向量集合】
- skill_vectors: 技能节点向量 (~500个, 1024dim)
- jd_vectors: JD 岗位描述向量
- resume_vectors: 简历内容向量
- conversation_vectors: 对话历史向量 (RAG 记忆)

【使用场景】
- "类似React的前端技能有哪些"
- "跟我简历最匹配的岗位"
- "之前我们聊过Python相关的内容吗" (跨会话 RAG)
- "机器学习相关的岗位要求"

【返回内容】
- Top-K 最相似的结果 (名称/相似度/来源)
- 相关上下文片段"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "query": "自然语言查询",
            "collection": "目标集合 (可选: skill/jd/resume/conversation, 默认全部)",
            "top_k": "返回结果数量 (可选, 默认5)"
        }

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        collection = kwargs.get("collection", "all")
        top_k = kwargs.get("top_k", 5)

        logger.info(f"semantic_search 执行: query={query}, collection={collection}, top_k={top_k}")

        # TODO: 实现 Milvus 语义检索
        # 1. BGE-M3 对 query 做 embedding
        # 2. Milvus search (collection, query_vector, top_k)
        # 3. 格式化结果: [{name, score, source, context}]

        return f"[semantic_search] 查询: {query}\n暂未实现, 待接入 Milvus + BGE-M3"
