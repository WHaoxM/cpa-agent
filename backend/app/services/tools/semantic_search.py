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
        return (
            "语义检索暂不可用：现有技能/JD/简历/对话集合缺少可信公共来源或 owner 隔离。"
            "不得以空结果推断没有匹配；公共技能证据改用 graph_query。"
        )

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

        if not query:
            return json.dumps({"success": False, "status": "failed", "message": "[semantic_search] 错误: query 参数不能为空"}, ensure_ascii=False)

        # None of the current collections has a trusted publication/owner
        # contract. Even skill/jd vectors are writable from runtime upload APIs.
        # Do not call embedding/Milvus, silently remove a filter on errors, or
        # mistake an IT-position flag for public provenance. Unknown names are
        # also denied rather than passed through to arbitrary collections.
        message = (
            "[semantic_search] 不可用: 当前技能/JD/简历/会话向量尚未完成公共来源与 Principal 归属隔离。"
            "已停止跨集合检索；不能据此判断没有匹配内容。公共技能证据请用 graph_query；"
            "个人检索需完成 owner 字段迁移与可信作用域下沉后恢复。"
        )
        return json.dumps({"success": False, "status": "unavailable", "message": message}, ensure_ascii=False)
