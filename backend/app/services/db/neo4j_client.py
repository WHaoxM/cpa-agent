"""
Neo4j 图数据库客户端
提供连接池管理和 Cypher 查询接口
"""

import threading
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, AuthError

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.db.neo4j')


class Neo4jClient:
    """Neo4j 客户端 - 连接池管理 + Cypher 查询"""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        max_connection_pool_size: int = 50,
        connection_timeout: float = 30.0,
    ):
        self.uri = uri or Config.NEO4J_URI
        self.user = user or Config.NEO4J_USER
        self.password = password or Config.NEO4J_PASSWORD
        self.max_connection_pool_size = max_connection_pool_size
        self.connection_timeout = connection_timeout
        self._driver: Optional[Driver] = None

    def _get_driver(self) -> Driver:
        """获取或创建驱动"""
        if self._driver is None:
            logger.info(f"创建 Neo4j 驱动: {self.uri}")
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                max_connection_pool_size=self.max_connection_pool_size,
                connection_timeout=self.connection_timeout,
            )
        return self._driver

    def close(self):
        """关闭驱动"""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j 驱动已关闭")

    @contextmanager
    def session(self, **kwargs):
        """会话上下文管理器"""
        driver = self._get_driver()
        session = driver.session(**kwargs)
        try:
            yield session
        finally:
            session.close()

    def test_connection(self) -> bool:
        """测试连接"""
        try:
            with self.session() as session:
                result = session.run("RETURN 1 AS test")
                result.single()
            logger.info("Neo4j 连接测试成功")
            return True
        except (ServiceUnavailable, AuthError) as e:
            logger.error(f"Neo4j 连接测试失败: {e}")
            return False
        except Exception as e:
            logger.error(f"Neo4j 连接异常: {e}")
            return False

    def execute_query(
        self,
        cypher: str,
        parameters: Optional[Dict[str, Any]] = None,
        read_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        执行 Cypher 查询

        Args:
            cypher: Cypher 语句
            parameters: 参数
            read_only: 是否只读查询

        Returns:
            查询结果列表
        """
        try:
            with self.session(default_access_mode="READ" if read_only else "WRITE") as session:
                result = session.run(cypher, parameters or {})
                records = [dict(record) for record in result]
                logger.debug(f"执行查询: {cypher[:50]}..., 返回 {len(records)} 条")
                return records
        except Exception as e:
            logger.error(f"Cypher 执行失败: {e}, query: {cypher}")
            raise

    def execute_single(self, cypher: str, parameters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """执行查询并返回单条结果"""
        results = self.execute_query(cypher, parameters)
        return results[0] if results else None

    def get_schema(self) -> Dict[str, Any]:
        """获取图谱 schema 信息"""
        schema = {
            "node_labels": [],
            "relationship_types": [],
            "node_counts": {},
        }

        try:
            with self.session() as session:
                nodes = session.run("CALL db.labels() YIELD label RETURN label")
                schema["node_labels"] = [record["label"] for record in nodes]

                rels = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
                schema["relationship_types"] = [record["relationshipType"] for record in rels]

                for label in schema["node_labels"]:
                    count = session.run(f"MATCH (n:`{label}`) RETURN count(n) AS count")
                    schema["node_counts"][label] = count.single()["count"]

            logger.info(f"获取图谱 schema: {len(schema['node_labels'])} 种节点, {len(schema['relationship_types'])} 种关系")
        except Exception as e:
            logger.error(f"获取 schema 失败: {e}")

        return schema

    def get_node_count(self) -> int:
        """获取节点总数"""
        try:
            with self.session() as session:
                result = session.run("MATCH (n) RETURN count(n) AS count")
                return result.single()["count"]
        except Exception as e:
            logger.error(f"获取节点数失败: {e}")
            return 0

    def get_edge_count(self) -> int:
        """获取边总数"""
        try:
            with self.session() as session:
                result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
                return result.single()["count"]
        except Exception as e:
            logger.error(f"获取边数失败: {e}")
            return 0


_neo4j_client: Optional[Neo4jClient] = None
_neo4j_client_lock = threading.Lock()


def get_neo4j_client() -> Neo4jClient:
    """获取全局 Neo4j 客户端单例（线程安全）"""
    global _neo4j_client
    if _neo4j_client is None:
        with _neo4j_client_lock:
            if _neo4j_client is None:
                _neo4j_client = Neo4jClient()
    return _neo4j_client
