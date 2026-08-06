"""
Milvus 向量数据库客户端
提供向量检索接口，支持 BGE-M3 embedding
支持独立 Milvus 服务器与 Lite 嵌入式 (.db) 模式
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from ...config import Config, _is_milvus_file_uri
from ...utils.logger import get_logger

logger = get_logger("cpa-agent.db.milvus")

# Agent / 归一化默认依赖的集合；连接成功后按需创建空壳
DEFAULT_COLLECTIONS = (
    ("skill_vectors", 1024, "COSINE"),
    ("jd_vectors", 1024, "COSINE"),
    ("resume_vectors", 1024, "COSINE"),
    ("conversation_vectors", 1024, "COSINE"),
)


def _import_pymilvus_client():
    """安全 import pymilvus.MilvusClient。

    pymilvus 在 import 时会读环境变量 MILVUS_URI，只接受 http(s)。
    Config 加载阶段已剥离文件型 URI；此处再做一次防御。
    """
    poisoned = os.environ.get("MILVUS_URI", "")
    popped = None
    if _is_milvus_file_uri(poisoned):
        popped = os.environ.pop("MILVUS_URI", None)
    try:
        from pymilvus import MilvusClient as _Client
        return _Client
    finally:
        if popped is not None and not _is_milvus_file_uri(popped):
            os.environ["MILVUS_URI"] = popped


class MilvusClient:
    """Milvus 向量数据库客户端 - 向量检索"""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.uri = uri or Config.MILVUS_URI
        self.user = user
        self.password = password
        self.timeout = timeout
        self._connected = False
        self._client = None

    def connect(self, alias: str = "default") -> bool:
        """连接 Milvus"""
        try:
            if self._connected:
                logger.info("Milvus 已连接")
                return True

            logger.info(f"连接 Milvus: {self.uri}")
            Client = _import_pymilvus_client()

            if _is_milvus_file_uri(self.uri):
                milvus_uri = self.uri
                if not os.path.isabs(milvus_uri):
                    milvus_uri = os.path.abspath(milvus_uri)
                milvus_dir = os.path.dirname(milvus_uri)
                if milvus_dir:
                    os.makedirs(milvus_dir, exist_ok=True)
                self._client = Client(uri=milvus_uri)
                self._connected = True
                logger.info(f"Milvus Lite 嵌入式连接成功: {milvus_uri}")
            else:
                uri = self.uri
                if not uri.startswith("http://") and not uri.startswith("https://"):
                    uri = f"http://{uri}"
                self._client = Client(uri=uri)
                self._connected = True
                logger.info(f"Milvus 独立服务器连接成功: {uri}")

            self.ensure_default_collections()
            return True

        except Exception as e:
            logger.error(f"Milvus 连接失败: {e}")
            self._connected = False
            return False

    def ensure_default_collections(self) -> None:
        """确保 Agent 默认集合存在（空集合即可，避免 insert/search 直接 100）。"""
        if not self._connected or self._client is None:
            return
        for name, dim, metric in DEFAULT_COLLECTIONS:
            try:
                if self._client.has_collection(name):
                    continue
                self._client.create_collection(
                    collection_name=name,
                    dimension=dim,
                    metric_type=metric,
                    auto_id=True,
                    primary_field_name="id",
                    vector_field_name="embedding",
                )
                logger.info("已创建 Milvus collection: %s (dim=%s)", name, dim)
            except Exception as e:
                logger.warning("创建 Milvus collection 失败 %s: %s", name, e)

    def ensure_collection(self, collection_name: str, dimension: int = 1024) -> bool:
        if not self._connected and not self.connect():
            return False
        try:
            if self._client.has_collection(collection_name):
                return True
            self._client.create_collection(
                collection_name=collection_name,
                dimension=dimension,
                metric_type="COSINE",
                auto_id=True,
                primary_field_name="id",
                vector_field_name="embedding",
            )
            logger.info("已创建 Milvus collection: %s", collection_name)
            return True
        except Exception as e:
            logger.error("ensure_collection(%s) 失败: %s", collection_name, e)
            return False

    def disconnect(self, alias: str = "default"):
        """断开连接"""
        self._connected = False
        self._client = None
        logger.info("Milvus 已断开")

    def test_connection(self) -> bool:
        """测试连接"""
        if not self._connected:
            return self.connect()
        try:
            collections = self._client.list_collections()
            logger.info(f"Milvus 连接测试成功, 集合数: {len(collections)}")
            return True
        except Exception as e:
            logger.error(f"Milvus 连接测试失败: {e}")
            return False

    def create_collection(
        self,
        collection_name: str,
        dimension: int = 1024,
        description: str = "",
        primary_field_name: str = "id",
        vector_field_name: str = "embedding",
        id_type: str = "int",
        vector_type: str = "float_vector",
        metric_type: str = "IP",
        index_type: str = "AUTOINDEX",
    ) -> bool:
        """创建集合"""
        try:
            if not self._connected and not self.connect():
                return False
            if self._client.has_collection(collection_name):
                logger.info(f"集合 {collection_name} 已存在")
                return True

            self._client.create_collection(
                collection_name=collection_name,
                dimension=dimension,
                primary_field_name=primary_field_name,
                vector_field_name=vector_field_name,
                metric_type=metric_type,
            )

            logger.info(f"集合 {collection_name} 创建成功, dimension={dimension}")
            return True

        except Exception as e:
            logger.error(f"创建集合失败: {e}")
            return False

    def insert(
        self,
        collection_name: str,
        texts: List[str],
        vectors: List[List[float]],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> List[int]:
        """插入向量数据"""
        try:
            if not self._connected and not self.connect():
                raise RuntimeError("Milvus 未连接")
            dim = len(vectors[0]) if vectors and vectors[0] else 1024
            if not self.ensure_collection(collection_name, dimension=dim):
                raise RuntimeError(f"collection '{collection_name}' 不存在且创建失败")
            meta_strs = [
                json.dumps(m, ensure_ascii=False) if m else "{}"
                for m in (metadata or [{}] * len(texts))
            ]

            data = {
                "text": texts,
                "embedding": vectors,
                "metadata": meta_strs,
            }

            result = self._client.insert(collection_name=collection_name, data=data)

            logger.info(f"插入 {len(texts)} 条向量到 {collection_name}")
            return result.primary_keys

        except Exception as e:
            logger.error(f"插入向量失败: {e}")
            raise

    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 10,
        expr: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """向量检索"""
        try:
            if not self._connected and not self.connect():
                return []
            if not self._client.has_collection(collection_name):
                logger.warning(f"集合 {collection_name} 不存在（将尝试创建空集合）")
                dim = len(query_vector) if query_vector else 1024
                if not self.ensure_collection(collection_name, dimension=dim):
                    return []

            kwargs: Dict[str, Any] = {
                "collection_name": collection_name,
                "data": [query_vector],
                "limit": top_k,
                "output_fields": output_fields or ["text", "metadata"],
            }
            if expr:
                kwargs["filter"] = expr

            results = self._client.search(**kwargs)

            formatted_results = []
            for hits in results:
                for hit in hits:
                    entity = hit.get("entity") if isinstance(hit, dict) else None
                    if isinstance(entity, dict):
                        text = entity.get("text")
                        metadata = entity.get("metadata")
                    else:
                        text = hit.get("text") if isinstance(hit, dict) else None
                        metadata = hit.get("metadata") if isinstance(hit, dict) else None
                    formatted_results.append({
                        "id": hit.get("id") if isinstance(hit, dict) else getattr(hit, "id", None),
                        "distance": hit.get("distance") if isinstance(hit, dict) else getattr(hit, "distance", None),
                        "text": text,
                        "metadata": metadata,
                    })

            logger.debug(f"检索 {collection_name}, top_k={top_k}, 返回 {len(formatted_results)} 条")
            return formatted_results

        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def delete_collection(self, collection_name: str) -> bool:
        """删除集合"""
        try:
            if not self._connected and not self.connect():
                return False
            if self._client.has_collection(collection_name):
                self._client.drop_collection(collection_name)
                logger.info(f"集合 {collection_name} 已删除")
            return True
        except Exception as e:
            logger.error(f"删除集合失败: {e}")
            return False

    def list_collections(self) -> List[str]:
        """列出所有集合"""
        try:
            if not self._connected and not self.connect():
                return []
            return self._client.list_collections()
        except Exception as e:
            logger.error(f"列出集合失败: {e}")
            return []

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """获取集合统计信息"""
        try:
            if not self._connected and not self.connect():
                return {"exists": False}
            if not self._client.has_collection(collection_name):
                return {"exists": False}

            return {
                "exists": True,
                "name": collection_name,
                "num_entities": self._client.get_collection_stats(collection_name).get("row_count", 0),
            }
        except Exception as e:
            logger.error(f"获取集合统计失败: {e}")
            return {"exists": False, "error": str(e)}


_milvus_client: Optional[MilvusClient] = None
_milvus_client_lock = threading.Lock()


def get_milvus_client() -> MilvusClient:
    """获取全局 Milvus 客户端单例（线程安全）"""
    global _milvus_client
    if _milvus_client is None:
        with _milvus_client_lock:
            if _milvus_client is None:
                _milvus_client = MilvusClient()
    return _milvus_client
