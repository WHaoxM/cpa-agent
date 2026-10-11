"""
会话管理器
提供会话的创建、获取、删除、消息管理
"""

import json
import uuid
from copy import deepcopy
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


def derive_session_title(messages: List[Dict[str, Any]], *, fallback: str = "新对话") -> str:
    """Title from first user utterance — Claude/Qoder style conversation label."""
    for msg in messages or []:
        if (msg.get("role") or "") != "user":
            continue
        text = str(msg.get("content") or "").strip().replace("\n", " ")
        if not text:
            continue
        return text[:36] + ("…" if len(text) > 36 else "")
    return fallback


@dataclass
class SessionMessage:
    """会话消息"""
    role: str
    content: str
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMessage":
        return cls(
            message_id=data.get("message_id") or f"msg_{uuid.uuid4().hex[:12]}",
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
    # Server-generated lifetime identity.  ``session_id`` is caller-visible
    # and can be deleted/recreated; durable request admission must distinguish
    # those incarnations without exposing this token to clients.
    incarnation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    conversation_summary: str = ""
    summary_message_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "student_id": self.student_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "incarnation_id": self.incarnation_id,
            "messages": self.messages,
            "context": self.context,
            "conversation_summary": self.conversation_summary,
            "summary_message_count": self.summary_message_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        created_at = data.get("created_at", datetime.now().isoformat())
        incarnation_id = data.get("incarnation_id")
        if not incarnation_id:
            # Stable compatibility identity for sessions serialized before
            # incarnation_id existed.  It remains constant across workers and
            # changes when a deleted caller-visible ID is recreated.
            incarnation_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                "cpa-agent-session:"
                f"{data.get('student_id') or ''}:"
                f"{data.get('session_id') or ''}:{created_at}",
            ).hex
        return cls(
            session_id=data.get("session_id", ""),
            student_id=data.get("student_id"),
            status=SessionStatus(data.get("status", "created")),
            created_at=created_at,
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            incarnation_id=str(incarnation_id),
            messages=data.get("messages", []),
            context=data.get("context", {}),
            conversation_summary=data.get("conversation_summary", "") or "",
            summary_message_count=int(data.get("summary_message_count") or 0),
        )


class SessionManager:
    """
    会话管理器
    内存缓存 + Redis 持久化
    """

    SESSION_PREFIX = "cpa:session:"
    SESSION_LIST_KEY = "cpa:sessions"

    MAX_CACHE_SIZE = 200

    def __init__(self, redis_client: Optional[RedisClient] = None):
        self._redis = redis_client or get_redis_client()
        self._cache: Dict[str, SessionState] = {}

    def _get_redis_key(self, session_id: str) -> str:
        return f"{self.SESSION_PREFIX}{session_id}"

    def _save_to_redis(self, state: SessionState, *, strict: bool = False):
        """保存到 Redis"""
        key = self._get_redis_key(state.session_id)
        encoded = json.dumps(state.to_dict(), ensure_ascii=False)
        if strict:
            self._redis.set_strict(key, encoded, ex=SESSION_EXPIRE_SECONDS)
        else:
            self._redis.set(key, encoded, ex=SESSION_EXPIRE_SECONDS)
        self._redis.hset(self.SESSION_LIST_KEY, state.session_id, json.dumps({
            "student_id": state.student_id,
            "updated_at": state.updated_at,
        }))

    def _load_from_redis(self, session_id: str, *, strict: bool = False) -> Optional[SessionState]:
        """从 Redis 加载"""
        key = self._get_redis_key(session_id)
        data = self._redis.get_strict(key) if strict else self._redis.get(key)
        if data:
            try:
                return SessionState.from_dict(data)
            except Exception as e:
                if strict:
                    raise ValueError("权威会话状态无法解析") from e
                logger.error(f"加载会话失败: {e}")
                return None
        return None

    def create_session(self, student_id: Optional[str] = None, session_id: Optional[str] = None) -> SessionState:
        """创建新会话（幂等：相同 session_id 返回已有会话）"""
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing

        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
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
        self._save_session_to_mysql(state)

        logger.info(f"创建会话: {session_id}, student_id={student_id}")
        return state

    def _save_session_to_mysql(self, state: SessionState):
        """将会话保存到 MySQL"""
        try:
            from .db import get_mysql_client
            mysql = get_mysql_client()

            mysql.execute(
                """
                INSERT INTO conversation (session_id, student_id, title, status, message_count, created_at, updated_at)
                VALUES (:session_id, :student_id, :title, :status, :message_count, :created_at, :updated_at)
                ON DUPLICATE KEY UPDATE
                    student_id = VALUES(student_id),
                    status = VALUES(status),
                    updated_at = VALUES(updated_at)
                """,
                {
                    "session_id": state.session_id,
                    "student_id": state.student_id,
                    "title": None,
                    "status": state.status.value,
                    "message_count": len(state.messages),
                    "created_at": state.created_at,
                    "updated_at": state.updated_at,
                },
                commit=True,
            )
        except Exception as e:
            logger.warning(f"保存会话到 MySQL 失败: {e}")

    def _save_message_to_mysql(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        message_id: Optional[str] = None,
    ):
        """将消息保存到 MySQL"""
        try:
            from .db import get_mysql_client
            mysql = get_mysql_client()

            message_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
            now = datetime.now().isoformat()

            tool_calls_json = None
            thinking_json = None
            elapsed_seconds = None
            iterations = None

            if metadata:
                if 'tool_calls' in metadata:
                    tool_calls_json = json.dumps(metadata['tool_calls'], ensure_ascii=False)
                thinking_payload: Dict[str, Any] = {}
                for key in (
                    "thinking_steps",
                    "parts_v1",
                    "turn_id",
                    "interaction_mode",
                    "upgrade_hint",
                    "artifacts",
                    "orchestration",
                ):
                    if key in metadata:
                        thinking_payload[key] = metadata[key]
                if thinking_payload:
                    thinking_json = json.dumps(thinking_payload, ensure_ascii=False)
                if 'iterations' in metadata:
                    iterations = metadata['iterations']
                if 'elapsed_seconds' in metadata:
                    elapsed_seconds = metadata['elapsed_seconds']

            mysql.execute(
                """
                INSERT INTO conversation_message 
                (session_id, message_id, role, content, tool_calls_json, thinking_json, iterations, elapsed_seconds, created_at)
                VALUES (:session_id, :message_id, :role, :content, :tool_calls_json, :thinking_json, :iterations, :elapsed_seconds, :created_at)
                """,
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "role": role,
                    "content": content,
                    "tool_calls_json": tool_calls_json,
                    "thinking_json": thinking_json,
                    "iterations": iterations,
                    "elapsed_seconds": elapsed_seconds,
                    "created_at": now,
                },
                commit=True,
            )

            mysql.execute(
                """
                UPDATE conversation 
                SET message_count = message_count + 1, updated_at = :updated_at
                WHERE session_id = :session_id
                """,
                {
                    "updated_at": now,
                    "session_id": session_id,
                },
                commit=True,
            )
        except Exception as e:
            logger.warning(f"保存消息到 MySQL 失败: {e}")

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """获取会话"""
        if session_id in self._cache:
            return self._cache[session_id]

        state = self._load_from_redis(session_id)
        if state:
            self._cache[session_id] = state
            # 淘汰最旧的缓存项
            if len(self._cache) > self.MAX_CACHE_SIZE:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].updated_at)
                del self._cache[oldest_key]

        return state

    def refresh_session(self, session_id: str, *, require_authoritative: bool = False) -> Optional[SessionState]:
        """Reload the authoritative Redis session after cross-worker admission.

        A worker-local cache is useful for ordinary reads, but another worker
        may have settled a turn while this worker waited on the Redis turn
        lease.  Do not let that stale cache become the next model history or
        context write.  If Redis is unavailable in deliberate local-only
        development, preserve the existing local state rather than deleting it.
        """
        state = self._load_from_redis(session_id, strict=require_authoritative)
        if state is not None:
            self._cache[session_id] = state
            return state
        if require_authoritative:
            self._cache.pop(session_id, None)
            return None
        return self._cache.get(session_id)

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
        *,
        require_authoritative: bool = False,
        context_update: Optional[Dict[str, Any]] = None,
    ) -> Optional[SessionState]:
        """添加消息"""
        state = self.get_session(session_id)
        if not state:
            logger.warning(f"会话不存在: {session_id}")
            return None
        if require_authoritative:
            # Avoid poisoning this worker's cached object if the authoritative
            # Redis SET fails after an in-memory append.
            state = SessionState.from_dict(deepcopy(state.to_dict()))
        if context_update is not None:
            state.context = deepcopy(context_update)

        message = SessionMessage(
            role=role,
            content=content,
            metadata=metadata,
        )
        state.messages.append(message.to_dict())

        if role == "user":
            state.status = SessionStatus.ACTIVE
            # First user turn stamps conversation title (MySQL + context)
            user_turns = sum(1 for m in state.messages if m.get("role") == "user")
            if user_turns == 1:
                title = derive_session_title(state.messages)
                ctx = dict(state.context) if isinstance(state.context, dict) else {}
                ctx["conversation_title"] = title
                state.context = ctx
                # MySQL title is a projection; update after session persistence.
        state.updated_at = datetime.now().isoformat()

        if require_authoritative:
            try:
                self._save_to_redis(state, strict=True)
            except Exception as exc:
                from .agent_runtime.session_coordinator import SessionCoordinationUnavailable
                raise SessionCoordinationUnavailable("权威会话状态写入失败") from exc
        else:
            self._save_to_redis(state)
        self._cache[session_id] = state
        if role == "user" and user_turns == 1:
            self._update_mysql_title(session_id, title)
        self._save_message_to_mysql(
            session_id,
            role,
            content,
            metadata,
            message_id=message.message_id,
        )

        return state

    def _update_mysql_title(self, session_id: str, title: str) -> None:
        try:
            from .db import get_mysql_client

            mysql = get_mysql_client()
            mysql.execute(
                """
                UPDATE conversation
                SET title = :title, updated_at = :updated_at
                WHERE session_id = :session_id
                """,
                {
                    "title": title,
                    "updated_at": datetime.now().isoformat(),
                    "session_id": session_id,
                },
                commit=True,
            )
        except Exception as e:
            logger.warning(f"更新会话标题失败: {e}")

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
        max_messages: int = 16,
    ) -> List[Dict[str, str]]:
        """
        获取适合传给 Agent 的历史消息格式（单通道 STM）。
        默认 max_messages=16 ≈ K=8 轮。不把同一批消息再塞进 structured context。
        """
        messages = self.get_messages(session_id, limit=max_messages)

        if len(messages) < max_messages:
            mysql_messages = self._get_messages_from_mysql(
                session_id,
                limit=max_messages - len(messages),
                offset=0
            )
            if mysql_messages:
                mysql_messages.reverse()
                messages = mysql_messages + messages

        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages[-max_messages:]
        ]

    def get_conversation_summary(self, session_id: str) -> str:
        state = self.get_session(session_id)
        if not state:
            return ""
        return state.conversation_summary or ""

    def set_conversation_summary(self, session_id: str, summary: str) -> Optional[SessionState]:
        state = self.get_session(session_id)
        if not state:
            return None
        state.conversation_summary = summary or ""
        state.updated_at = datetime.now().isoformat()
        self._cache[session_id] = state
        self._save_to_redis(state)
        return state

    def maybe_rollup_summary(self, session_id: str, keep_messages: int = 16) -> Optional[str]:
        """
        When message count exceeds keep_messages, compress older turns into conversation_summary.
        Best-effort; skips if LLM unavailable.
        """
        state = self.get_session(session_id)
        if not state or len(state.messages) <= keep_messages:
            return state.conversation_summary if state else None

        cutoff = max(0, len(state.messages) - keep_messages)
        cursor = max(0, min(int(getattr(state, "summary_message_count", 0) or 0), cutoff))
        if cutoff <= cursor:
            return state.conversation_summary

        older = state.messages[cursor:cutoff]
        if not older:
            return state.conversation_summary

        blob = "\n".join(
            f"{m.get('role')}: {str(m.get('content') or '')[:300]}" for m in older[-20:]
        )
        prev = state.conversation_summary or ""
        try:
            from ..utils.llm_client import get_llm_client

            llm = get_llm_client()
            prompt = (
                "将以下对话压缩为不超过 800 字的中文摘要，保留用户目标、已确认约束与未决问题。\n"
                f"已有摘要:\n{prev}\n\n新增对话:\n{blob}"
            )
            summary = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=600,
            )
            if summary:
                state.conversation_summary = summary.strip()
                state.summary_message_count = cutoff
                state.updated_at = datetime.now().isoformat()
                self._cache[session_id] = state
                self._save_to_redis(state)
                return summary.strip()
        except Exception as e:
            logger.warning(f"滚动摘要失败: {e}")
            from .agent_runtime.budget import current_usage_ledger
            if (current_usage_ledger() or {}).get("stop_reason"):
                # Keep the source cursor intact; a later turn can retry these
                # messages. Never label a budget fallback as a full summary.
                raise
            # deterministic fallback without LLM
            fallback = (prev + "\n" + blob)[-1500:]
            state.conversation_summary = fallback
            state.summary_message_count = cutoff
            state.updated_at = datetime.now().isoformat()
            self._cache[session_id] = state
            self._save_to_redis(state)
            return fallback
        return state.conversation_summary

    def persist_blackboard(self, session_id: str, blackboard: Dict[str, Any]) -> Optional[SessionState]:
        """Store nested blackboard under session.context['blackboard']."""
        state = self.get_session(session_id)
        if not state:
            return None
        if not isinstance(state.context, dict):
            state.context = {}
        state.context["blackboard"] = blackboard
        state.updated_at = datetime.now().isoformat()
        self._cache[session_id] = state
        self._save_to_redis(state)
        return state

    def build_structured_context(
        self,
        session_id: str,
        student_id: Optional[str] = None,
        recent_limit: int = 6,
        include_recent_messages: bool = False,
    ) -> Dict[str, Any]:
        """
        构建给 Agent / Supervisor 的结构化上下文。

        默认不再附带 conversation.recent_messages（避免与 history 双注入）。
        legacy 模式可传 include_recent_messages=True。
        """
        state = self.get_session(session_id)
        if not state:
            return {
                "session": {"session_id": session_id, "exists": False},
                "student": {"student_id": student_id},
                "conversation": {
                    "summary": "",
                },
                "business": {},
                "blackboard": {},
            }

        effective_student_id = student_id or state.student_id
        context_payload = state.context if isinstance(state.context, dict) else {}
        blackboard = context_payload.get("blackboard")
        if not isinstance(blackboard, dict):
            blackboard = {}

        conversation: Dict[str, Any] = {
            "summary": state.conversation_summary or "",
            "message_count": len(state.messages),
        }
        if include_recent_messages:
            recent_msgs = self.get_messages(session_id, limit=recent_limit)
            last_user = next((m for m in reversed(recent_msgs) if m.get("role") == "user"), None)
            last_assistant = next(
                (m for m in reversed(recent_msgs) if m.get("role") == "assistant"), None
            )
            conversation["recent_messages"] = [
                {"role": m.get("role"), "content": m.get("content", "")[:500]}
                for m in recent_msgs
            ]
            conversation["last_user_message"] = (last_user or {}).get("content", "")
            conversation["last_assistant_message"] = (last_assistant or {}).get("content", "")

        return {
            "session": {
                "session_id": state.session_id,
                "status": state.status.value,
                "created_at": state.created_at,
                "updated_at": state.updated_at,
                "message_count": len(state.messages),
                "exists": True,
            },
            "student": {
                "student_id": effective_student_id,
            },
            "conversation": conversation,
            "business": {
                k: v for k, v in context_payload.items() if k != "blackboard"
            },
            "blackboard": blackboard,
        }

    def _get_messages_from_mysql(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """从 MySQL 分页获取消息"""
        try:
            from .db import get_mysql_client
            mysql = get_mysql_client()

            rows = mysql.fetch_all(
                """
                SELECT message_id, role, content, tool_calls_json, thinking_json, created_at
                FROM conversation_message
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                LIMIT :limit OFFSET :offset
                """,
                {"session_id": session_id, "limit": limit, "offset": offset}
            )

            return [
                self._mysql_row_to_message(row)
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"从 MySQL 获取消息失败: {e}")
            return []

    def _mysql_row_to_message(self, row: Dict[str, Any]) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        tool_calls = _loads_json(row.get("tool_calls_json"), None)
        thinking_payload = _loads_json(row.get("thinking_json"), None)
        if isinstance(tool_calls, list):
            metadata["tool_calls"] = tool_calls
        if isinstance(thinking_payload, list):
            metadata["thinking_steps"] = thinking_payload
        elif isinstance(thinking_payload, dict):
            metadata.update(thinking_payload)
        return {
            "message_id": row.get("message_id") or f"msg_{uuid.uuid4().hex[:12]}",
            "role": row.get("role", "user"),
            "content": row.get("content", ""),
            "timestamp": row.get("created_at", ""),
            "metadata": metadata or None,
        }

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
        status: Optional[SessionStatus | str] = None,
        limit: int = 50,
    ) -> List[SessionState]:
        """列出会话（使用 hscan 优化）"""
        status_filter: Optional[SessionStatus] = None
        if isinstance(status, SessionStatus):
            status_filter = status
        elif isinstance(status, str) and status.strip():
            try:
                status_filter = SessionStatus(status.strip())
            except ValueError:
                status_filter = None

        cursor = 0
        collected = 0
        session_ids = []

        while collected < limit:
            cursor, batch = self._redis.hscan(self.SESSION_LIST_KEY, cursor=cursor, count=min(limit - collected, 100))
            session_ids.extend(batch.keys())
            collected = len(session_ids)
            if cursor == 0:
                break

        session_ids = session_ids[:limit]

        sessions = []
        for sid in session_ids:
            state = self.get_session(sid)
            if state:
                if student_id and state.student_id != student_id:
                    continue
                if status_filter and state.status != status_filter:
                    continue
                sessions.append(state)

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def archive_session(self, session_id: str) -> bool:
        """归档会话，并触发 Memory Store Reflection（有 student_id 时）"""
        state = self.get_session(session_id)
        if not state:
            return False

        state.status = SessionStatus.ARCHIVED
        state.updated_at = datetime.now().isoformat()

        self._cache[session_id] = state
        self._save_to_redis(state)

        if state.student_id:
            try:
                from .agent_runtime.fork_jobs import enqueue as fork_enqueue

                summary = state.conversation_summary or ""
                if not summary and state.messages:
                    summary = " | ".join(
                        str(m.get("content") or "")[:80]
                        for m in state.messages[-6:]
                    )
                # Qoder-style fork: do not block archive on Reflection LLM/DB
                fork_enqueue(
                    session_id,
                    "reflect_session",
                    {
                        "student_id": state.student_id,
                        "summary": summary,
                        "promote": True,
                    },
                )
            except Exception as e:
                logger.warning(f"Reflection fork 入队失败 session={session_id}: {e}")

        logger.info(f"归档会话: {session_id}")
        return True


_session_manager: Optional[SessionManager] = None
_session_manager_lock = __import__('threading').Lock()


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例 (线程安全)"""
    global _session_manager
    if _session_manager is None:
        with _session_manager_lock:
            if _session_manager is None:
                _session_manager = SessionManager()
    return _session_manager


def _loads_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback
