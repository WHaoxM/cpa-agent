"""
Redis 客户端
提供缓存和会话管理接口
"""

import json
import threading
from typing import Optional, Any, List

import redis
from redis import Redis, ConnectionPool

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.db.redis')


class RedisClient:
    """Redis 客户端 - 连接池管理 + 缓存/会话操作"""

    def __init__(
        self,
        url: Optional[str] = None,
        decode_responses: bool = True,
        max_connections: int = 50,
    ):
        self.url = url or Config.REDIS_URL
        self.decode_responses = decode_responses
        self.max_connections = max_connections
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None

    def _get_pool(self) -> ConnectionPool:
        """获取连接池"""
        if self._pool is None:
            logger.info(f"创建 Redis 连接池: {self.url}")
            self._pool = ConnectionPool.from_url(
                self.url,
                decode_responses=self.decode_responses,
                max_connections=self.max_connections,
            )
        return self._pool

    def _get_client(self) -> Redis:
        """获取客户端"""
        if self._client is None:
            self._client = Redis(connection_pool=self._get_pool())
        return self._client

    def close(self):
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None
        if self._pool:
            self._pool.disconnect()
            self._pool = None
        logger.info("Redis 连接已关闭")

    @property
    def client(self) -> Redis:
        """获取 Redis 客户端"""
        return self._get_client()

    def test_connection(self) -> bool:
        """测试连接"""
        try:
            result = self._get_client().ping()
            logger.info("Redis 连接测试成功")
            return result
        except Exception as e:
            logger.error(f"Redis 连接测试失败: {e}")
            return False

    def set(
        self,
        key: str,
        value: Any,
        ex: Optional[int] = None,
        px: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        """
        设置键值

        Args:
            key: 键
            value: 值 (会自动 JSON 序列化)
            ex: 过期秒数
            px: 过期毫秒数
        """
        try:
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            return self._get_client().set(key, value, ex=ex, px=px, nx=nx, xx=xx)
        except Exception as e:
            logger.error(f"Redis SET 失败: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """获取值"""
        try:
            value = self._get_client().get(key)
            if value is None:
                return default
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"Redis GET 失败: {e}")
            return default

    def get_strict(self, key: str) -> Any:
        """Read authoritative state without conflating an outage with a miss."""
        value = self._get_client().get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def set_strict(self, key: str, value: Any, *, ex: Optional[int] = None) -> None:
        """Persist authoritative state or raise; never report a swallowed SET as success."""
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if not self._get_client().set(key, encoded, ex=ex):
            raise RuntimeError("Redis SET did not persist session state")

    def delete(self, *keys: str) -> int:
        """删除键"""
        try:
            return self._get_client().delete(*keys)
        except Exception as e:
            logger.error(f"Redis DELETE 失败: {e}")
            return 0

    def exists(self, *keys: str) -> int:
        """检查键是否存在"""
        try:
            return self._get_client().exists(*keys)
        except Exception as e:
            logger.error(f"Redis EXISTS 失败: {e}")
            return 0

    def expire(self, key: str, seconds: int) -> bool:
        """设置过期时间"""
        try:
            return self._get_client().expire(key, seconds)
        except Exception as e:
            logger.error(f"Redis EXPIRE 失败: {e}")
            return False

    def ttl(self, key: str) -> int:
        """获取剩余过期时间"""
        try:
            return self._get_client().ttl(key)
        except Exception as e:
            logger.error(f"Redis TTL 失败: {e}")
            return -2

    def hset(self, name: str, key: str = None, value: Any = None, mapping: dict = None) -> int:
        """哈希设置"""
        try:
            client = self._get_client()
            if mapping:
                encoded_mapping = {}
                for k, v in mapping.items():
                    encoded_mapping[k] = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                return client.hset(name, mapping=encoded_mapping)
            else:
                value = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
                return client.hset(name, key, value)
        except Exception as e:
            logger.error(f"Redis HSET 失败: {e}")
            return 0

    def hget(self, name: str, key: str, default: Any = None) -> Any:
        """哈希获取"""
        try:
            value = self._get_client().hget(name, key)
            if value is None:
                return default
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"Redis HGET 失败: {e}")
            return default

    def hgetall(self, name: str) -> dict:
        """获取哈希所有键值"""
        try:
            result = self._get_client().hgetall(name)
            decoded = {}
            for k, v in result.items():
                try:
                    decoded[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    decoded[k] = v
            return decoded
        except Exception as e:
            logger.error(f"Redis HGETALL 失败: {e}")
            return {}

    def hscan(self, name: str, cursor: int = 0, match: Optional[str] = None, count: Optional[int] = None) -> tuple:
        """增量迭代哈希键值"""
        try:
            cursor, result = self._get_client().hscan(name, cursor=cursor, match=match, count=count)
            decoded = {}
            for k, v in result.items():
                try:
                    decoded[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    decoded[k] = v
            return cursor, decoded
        except Exception as e:
            logger.error(f"Redis HSCAN 失败: {e}")
            return 0, {}

    def hdel(self, name: str, *keys: str) -> int:
        """删除哈希键"""
        try:
            return self._get_client().hdel(name, *keys)
        except Exception as e:
            logger.error(f"Redis HDEL 失败: {e}")
            return 0

    def lpush(self, name: str, *values: Any) -> int:
        """列表左推入"""
        try:
            encoded_values = []
            for v in values:
                if not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                encoded_values.append(v)
            return self._get_client().lpush(name, *encoded_values)
        except Exception as e:
            logger.error(f"Redis LPUSH 失败: {e}")
            return 0

    def rpop(self, name: str) -> Any:
        """列表右弹出"""
        try:
            value = self._get_client().rpop(name)
            if value is None:
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"Redis RPOP 失败: {e}")
            return None

    def lrange(self, name: str, start: int = 0, end: int = -1) -> List[Any]:
        """获取列表范围"""
        try:
            result = self._get_client().lrange(name, start, end)
            decoded = []
            for v in result:
                try:
                    decoded.append(json.loads(v))
                except (json.JSONDecodeError, TypeError):
                    decoded.append(v)
            return decoded
        except Exception as e:
            logger.error(f"Redis LRANGE 失败: {e}")
            return []

    def incr(self, key: str, amount: int = 1) -> int:
        """递增"""
        try:
            return self._get_client().incr(key, amount)
        except Exception as e:
            logger.error(f"Redis INCR 失败: {e}")
            return 0

    def keys(self, pattern: str = "*") -> List[str]:
        """获取匹配的键"""
        try:
            return self._get_client().keys(pattern)
        except Exception as e:
            logger.error(f"Redis KEYS 失败: {e}")
            return []


_redis_client: Optional[RedisClient] = None
_redis_client_lock = threading.Lock()


def get_redis_client() -> RedisClient:
    """获取全局 Redis 客户端单例（线程安全）"""
    global _redis_client
    if _redis_client is None:
        with _redis_client_lock:
            if _redis_client is None:
                _redis_client = RedisClient()
    return _redis_client
