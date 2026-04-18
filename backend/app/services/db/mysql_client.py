"""
MySQL 数据库客户端
提供连接池管理和 SQL 查询接口
"""

from typing import Optional, Dict, Any, List

from sqlalchemy import create_engine, text, Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.db.mysql')


class MySQLClient:
    """MySQL 客户端 - 连接池管理 + SQL 查询"""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 3600,
    ):
        self.host = host or Config.MYSQL_HOST
        self.port = port or Config.MYSQL_PORT
        self.user = user or Config.MYSQL_USER
        self.password = password or Config.MYSQL_PASSWORD
        self.database = database or Config.MYSQL_DATABASE

        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_timeout = pool_timeout
        self.pool_recycle = pool_recycle

        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None

    def _get_engine(self) -> Engine:
        """获取或创建引擎"""
        if self._engine is None:
            connection_string = (
                f"mysql+pymysql://{self.user}:{self.password}@"
                f"{self.host}:{self.port}/{self.database}"
                f"?charset=utf8mb4"
            )
            logger.info(f"创建 MySQL 引擎: {self.host}:{self.port}/{self.database}")
            self._engine = create_engine(
                connection_string,
                poolclass=QueuePool,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_timeout=self.pool_timeout,
                pool_recycle=self.pool_recycle,
                echo=False,
            )
        return self._engine

    def _get_session_factory(self) -> sessionmaker:
        """获取会话工厂"""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self._get_engine(),
                autocommit=False,
                autoflush=False,
            )
        return self._session_factory

    def close(self):
        """关闭引擎"""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("MySQL 引擎已关闭")

    @property
    def engine(self) -> Engine:
        """获取引擎"""
        return self._get_engine()

    def test_connection(self) -> bool:
        """测试连接"""
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(text("SELECT 1 AS test"))
                result.scalar()
            logger.info("MySQL 连接测试成功")
            return True
        except Exception as e:
            logger.error(f"MySQL 连接测试失败: {e}")
            return False

    def execute(
        self,
        sql: str,
        parameters: Optional[Dict[str, Any]] = None,
        commit: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        执行 SQL 查询

        Args:
            sql: SQL 语句
            parameters: 参数
            commit: 是否提交

        Returns:
            查询结果列表
        """
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(text(sql), parameters or {})
                if commit:
                    conn.commit()

                if result.returns_rows:
                    keys = result.keys()
                    records = [dict(zip(keys, row)) for row in result.fetchall()]
                    logger.debug(f"执行查询: {sql[:50]}..., 返回 {len(records)} 条")
                    return records
                else:
                    conn.commit()
                    logger.debug(f"执行语句: {sql[:50]}..., 影响行数: {result.rowcount}")
                    return []
        except SQLAlchemyError as e:
            logger.error(f"SQL 执行失败: {e}, query: {sql}")
            raise

    def execute_single(
        self,
        sql: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """执行查询并返回单条结果"""
        results = self.execute(sql, parameters)
        return results[0] if results else None

    def execute_many(
        self,
        sql: str,
        parameters: List[Dict[str, Any]],
        commit: bool = True,
    ) -> int:
        """
        批量执行

        Args:
            sql: SQL 语句
            parameters: 参数列表
            commit: 是否提交

        Returns:
            影响的行数
        """
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(text(sql), parameters)
                if commit:
                    conn.commit()
                logger.debug(f"批量执行: {sql[:50]}..., 影响行数: {result.rowcount}")
                return result.rowcount
        except SQLAlchemyError as e:
            logger.error(f"批量执行失败: {e}")
            raise

    def get_tables(self) -> List[str]:
        """获取所有表名"""
        sql = "SHOW TABLES"
        results = self.execute(sql)
        table_key = f"Tables_in_{self.database}"
        return [row[table_key] for row in results]

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表结构"""
        sql = f"DESCRIBE `{table_name}`"
        return self.execute(sql)

    def create_database_if_not_exists(self):
        """创建数据库(如果不存在)"""
        temp_engine = create_engine(
            f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}?charset=utf8mb4"
        )
        try:
            with temp_engine.connect() as conn:
                conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{self.database}`"))
                conn.commit()
            logger.info(f"数据库 {self.database} 已就绪")
        finally:
            temp_engine.dispose()


_mysql_client: Optional[MySQLClient] = None


def get_mysql_client() -> MySQLClient:
    """获取全局 MySQL 客户端单例"""
    global _mysql_client
    if _mysql_client is None:
        _mysql_client = MySQLClient()
    return _mysql_client
