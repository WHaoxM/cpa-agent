"""
Tool 3: semantic_search — Milvus 向量语义检索
BGE-M3 Embedding 驱动的语义检索, 支持技能/JD/简历/对话 RAG 记忆
"""

import json
from typing import Dict, Any, List, Optional

from .base import BaseTool
from ...services.db import get_milvus_client, MilvusClient
from ...services.embedding_service import get_embedding_service
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.semantic_search')

DEFAULT_COLLECTIONS = {
    "skill": "skill_vectors",
    "jd": "jd_vectors",
    "resume": "resume_vectors",
    "conversation": "conversation_vectors",
}


class SemanticSearchTool(BaseTool):
    """Milvus 向量语义检索工具"""

    def __init__(self, milvus_client: Optional[MilvusClient] = None):
        self._milvus = milvus_client
        self._embedding = None

    @property
    def milvus(self) -> MilvusClient:
        if self._milvus is None:
            self._milvus = get_milvus_client()
            self._milvus.connect()
        return self._milvus

    @property
    def embedding(self):
        """获取 Embedding 服务"""
        if self._embedding is None:
            self._embedding = get_embedding_service()
        return self._embedding

    def _encode(self, texts: List[str]) -> List[List[float]]:
        """生成 embedding"""
        return self.embedding.encode(texts)

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

    def _get_collections(self, collection_hint: str) -> List[str]:
        """获取要检索的集合列表"""
        if not collection_hint or collection_hint == "all":
            return list(DEFAULT_COLLECTIONS.values())

        hints = [c.strip() for c in collection_hint.split(",")]
        collections = []
        for hint in hints:
            if hint in DEFAULT_COLLECTIONS:
                collections.append(DEFAULT_COLLECTIONS[hint])
            else:
                collections.append(hint)

        return collections if collections else list(DEFAULT_COLLECTIONS.values())

    def _format_results(
        self,
        results: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """格式化检索结果"""
        parts = []
        total = sum(len(v) for v in results.values())

        if total == 0:
            return "未找到相似内容"

        parts.append(f"找到 {total} 条相似结果:")

        for collection, hits in results.items():
            if not hits:
                continue

            collection_name = collection.replace("_vectors", "")
            parts.append(f"\n【{collection_name}】")

            for i, hit in enumerate(hits[:3]):
                text = hit.get("text", "")[:200]
                distance = hit.get("distance", 0)
                metadata = hit.get("metadata", {})

                parts.append(f"  {i+1}. 相似度: {distance:.4f}")
                parts.append(f"     内容: {text}...")
                if metadata:
                    parts.append(f"     元数据: {json.dumps(metadata, ensure_ascii=False)[:100]}")

        return "\n".join(parts)

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        collection = kwargs.get("collection", "all")
        top_k = kwargs.get("top_k", 5)

        if not query:
            return "[semantic_search] 错误: query 参数不能为空"

        try:
            top_k = int(top_k) if isinstance(top_k, str) else top_k
        except (ValueError, TypeError):
            logger.warning(f"top_k 参数解析失败: {top_k}, 使用默认值 5")
            top_k = 5

        logger.info(f"semantic_search 执行: query={query}, collection={collection}, top_k={top_k}")

        try:
            query_vector = self._encode([query])[0]

            collections = self._get_collections(collection)
            all_results = {}

            for coll in collections:
                try:
                    if not self.milvus.test_connection():
                        continue

                    expr = None
                    if coll == "jd_vectors":
                        expr = "is_it_position == 1"

                    results = self.milvus.search(
                        collection_name=coll,
                        query_vector=query_vector,
                        top_k=top_k,
                        expr=expr,
                    )
                    if results:
                        all_results[coll] = results
                except Exception as e:
                    logger.warning(f"集合 {coll} 检索失败: {e}")
                    continue

            return self._format_results(all_results)

        except Exception as e:
            logger.error(f"semantic_search 执行失败: {e}")
            return f"[semantic_search] 执行失败，请稍后重试"
