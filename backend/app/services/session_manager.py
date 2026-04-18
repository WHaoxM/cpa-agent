"""
会话管理器
提供会话的创建、获取、删除、消息管理
"""

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

from .db.redis_client import get_redis_client, RedisClient
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.session')


class SessionStatus(str, Enum):
    """会话状态"""
    CREATED = "created"
    ACTIVE = "active"
    IDLE = "idle"
    COMPLETED = "completed"
    ARCHIVED = "archived"


SESSION_EXPIRE_SECONDS = 86400 * 7


@dataclass
class SessionMessage:
    """会话消息"""
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMessage":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            metadata=data.get("metadata"),
        )


@dataclass
class SessionState:
    """会话状态"""
    session_id: str
    student_id: Optional[str]
    status: SessionStatus
    created_at: str
    updated_at: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "student_id": self.student_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        return cls(
            session_id=data.get("session_id", ""),
            student_id=data.get("student_id"),
            status=SessionStatus(data.get("status", "created")),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            messages=data.get("messages", []),
            context=data.get("context", {}),
        )


class SessionManager:
    """
    会话管理器
    内存缓存 + Redis 持久化
    """

    SESSION_PREFIX = "cpa:session:"
    SESSION_LIST_KEY = "cpa:sessions"

    def __init__(self, redis_client: Optional[RedisClient] = None):
        self._redis = redis_client or get_redis_client()
        self._cache: Dict[str, SessionState] = {}

    def _get_redis_key(self, session_id: str) -> str:
        return f"{self.SESSION_PREFIX}{session_id}"

    def _save_to_redis(self, state: SessionState):
        """保存到 Redis"""
        key = self._get_redis_key(state.session_id)
        self._redis.set(
            key,
            json.dumps(state.to_dict(), ensure_ascii=False),
            ex=SESSION_EXPIRE_SECONDS,
        )
        self._redis.hset(self.SESSION_LIST_KEY, state.session_id, json.dumps({
            "student_id": state.student_id,
            "updated_at": state.updated_at,
        }))

    def _load_from_redis(self, session_id: str) -> Optional[SessionState]:
        """从 Redis 加载"""
        key = self._get_redis_key(session_id)
        data = self._redis.get(key)
        if data:
            try:
                return SessionState.from_dict(data)
            except Exception as e:
                logger.error(f"加载会话失败: {e}")
                return None
        return None

    def create_session(self, student_id: Optional[str] = None) -> SessionState:
        """创建新会话"""
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()

        state = SessionState(
            session_id=session_id,
            student_id=student_id,
            status=SessionStatus.CREATED,
            created_at=now,
            updated_at=now,
            messages=[],
            context={},
        )

        self._cache[session_id] = state
        self._save_to_redis(state)

        logger.info(f"创建会话: {session_id}, student_id={student_id}")
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """获取会话"""
        if session_id in self._cache:
            return self._cache[session_id]

        state = self._load_from_redis(session_id)
        if state:
            self._cache[session_id] = state

        return state

    def update_session(self, session_id: str, **kwargs) -> Optional[SessionState]:
        """更新会话"""
        state = self.get_session(session_id)
        if not state:
            return None

        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)

        state.updated_at = datetime.now().isoformat()
        self._cache[session_id] = state
        self._save_to_redis(state)

        return state

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        if session_id in self._cache:
            del self._cache[session_id]

        key = self._get_redis_key(session_id)
        self._redis.delete(key)
        self._redis.hdel(self.SESSION_LIST_KEY, session_id)

        logger.info(f"删除会话: {session_id}")
        return True

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[SessionState]:
        """添加消息"""
        state = self.get_session(session_id)
        if not state:
            logger.warning(f"会话不存在: {session_id}")
            return None

        message = SessionMessage(
            role=role,
            content=content,
            metadata=metadata,
        )
        state.messages.append(message.to_dict())

        if role == "user":
            state.status = SessionStatus.ACTIVE
        state.updated_at = datetime.now().isoformat()

        self._cache[session_id] = state
        self._save_to_redis(state)

        return state

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取消息历史"""
        state = self.get_session(session_id)
        if not state:
            return []

        messages = state.messages
        if limit:
            messages = messages[-limit:]

        return messages

    def get_history_for_agent(
        self,
        session_id: str,
        max_messages: int = 20,
    ) -> List[Dict[str, str]]:
        """
        获取适合传给 Agent 的历史消息格式
        [{"role": "user/assistant", "content": "..."}]
        """
        messages = self.get_messages(session_id, limit=max_messages)
        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages
        ]

    def set_context(
        self,
        session_id: str,
        key: str,
        value: Any,
    ) -> Optional[SessionState]:
        """设置会话上下文"""
        state = self.get_session(session_id)
        if not state:
            return None

        state.context[key] = value
        state.updated_at = datetime.now().isoformat()

        self._cache[session_id] = state
        self._save_to_redis(state)

        return state

    def get_context(
        self,
        session_id: str,
        key: str,
        default: Any = None,
    ) -> Any:
        """获取会话上下文"""
        state = self.get_session(session_id)
        if not state:
            return default
        return state.context.get(key, default)

    def list_sessions(
        self,
        student_id: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
    ) -> List[SessionState]:
        """列出会话"""
        session_ids = self._redis.hkeys(self.SESSION_LIST_KEY)[:limit]

        sessions = []
        for sid in session_ids:
            state = self.get_session(sid)
            if state:
                if student_id and state.student_id != student_id:
                    continue
                if status and state.status != status:
                    continue
                sessions.append(state)

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def archive_session(self, session_id: str) -> bool:
        """归档会话"""
        state = self.get_session(session_id)
        if not state:
            return False

        state.status = SessionStatus.ARCHIVED
        state.updated_at = datetime.now().isoformat()

        self._cache[session_id] = state
        self._save_to_redis(state)

        logger.info(f"归档会话: {session_id}")
        return True


_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
