"""
Milvus 向量数据库客户端
提供向量检索接口，支持 BGE-M3 embedding
使用 pymilvus Lite 嵌入式模式
"""

import os
import json
from typing import Optional, Dict, Any, List

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.db.milvus')

_MILVUS_LITE = None


def _get_milvus_lite():
    """获取 Milvus Lite 客户端"""
    global _MILVUS_LITE
    if _MILVUS_LITE is None:
        from pymilvus import MilvusLiteClient
        _MILVUS_LITE = MilvusLiteClient
    return _MILVUS_LITE


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
            
            MilvusLiteClient = _get_milvus_lite()
            
            if self.uri.endswith(".db"):
                milvus_uri = os.path.abspath(self.uri)
                os.makedirs(os.path.dirname(milvus_uri), exist_ok=True)
                self._client = MilvusLiteClient(uri=milvus_uri)
                self._connected = True
                logger.info(f"Milvus Lite 嵌入式连接成功: {milvus_uri}")
                return True
            elif self.uri.startswith("http://") or self.uri.startswith("https://"):
                self._client = MilvusLiteClient(uri=self.uri)
                self._connected = True
                logger.info("Milvus 连接成功")
                return True
            else:
                milvus_uri = os.path.abspath(self.uri)
                os.makedirs(os.path.dirname(milvus_uri), exist_ok=True)
                self._client = MilvusLiteClient(uri=milvus_uri)
                self._connected = True
                logger.info(f"Milvus 连接成功: {milvus_uri}")
                return True
            
        except Exception as e:
            logger.error(f"Milvus 连接失败: {e}")
            self._connected = False
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
            from pymilvus import DataType
            
            if self._client.has_collection(collection_name):
                logger.info(f"集合 {collection_name} 已存在")
                return True

            fields = [
                {"name": primary_field_name, "dtype": DataType.INT64, "is_primary": True, "auto_id": True},
                {"name": "text", "dtype": DataType.VARCHAR, "max_length": 65535},
                {"name": vector_field_name, "dtype": DataType.FLOAT_VECTOR, "dimension": dimension},
                {"name": "metadata", "dtype": DataType.VARCHAR, "max_length": 65535},
            ]

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
            meta_strs = [json.dumps(m, ensure_ascii=False) if m else "{}" for m in (metadata or [{}] * len(texts))]

            data = [
                texts,
                vectors,
                meta_strs,
            ]

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
            if not self._client.has_collection(collection_name):
                logger.warning(f"集合 {collection_name} 不存在")
                return []

            results = self._client.search(
                collection_name=collection_name,
                data=[query_vector],
                limit=top_k,
                output_fields=output_fields or ["text", "metadata"],
            )

            formatted_results = []
            for hits in results:
                for hit in hits:
                    formatted_results.append({
                        "id": hit.get("id"),
                        "distance": hit.get("distance"),
                        "text": hit.get("text"),
                        "metadata": hit.get("metadata"),
                    })

            logger.debug(f"检索 {collection_name}, top_k={top_k}, 返回 {len(formatted_results)} 条")
            return formatted_results

        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def delete_collection(self, collection_name: str) -> bool:
        """删除集合"""
        try:
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
            return self._client.list_collections()
        except Exception as e:
            logger.error(f"列出集合失败: {e}")
            return []

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """获取集合统计信息"""
        try:
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


def get_milvus_client() -> MilvusClient:
    """获取全局 Milvus 客户端单例"""
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient()
    return _milvus_client
