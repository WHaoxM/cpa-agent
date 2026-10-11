"""
Session Coordinator — per-session 锁管理器。

解决并发问题：两个 Flask 线程同时处理同一个 session_id 的请求，
会并发写黑板导致数据损坏。

对齐 OpenCode SessionRunCoordinator 设计。
"""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ...utils.logger import get_logger


logger = get_logger("cpa-agent.agent_runtime.session_coordinator")


class SessionCoordinationUnavailable(RuntimeError):
    """A required cross-process turn lease could not be acquired."""


class SessionCoordinationLost(RuntimeError):
    """The Redis lease expired or could not be renewed during a turn."""


@dataclass
class TurnLease:
    """Small common handle for local and Redis-backed session turn leases."""

    assert_held: Callable[[], None]
    require_authoritative_session: bool = False


class _RedisLease:
    """Redis-py's token-checked Lock with a watchdog that extends its lease.

    redis-py owns the Lua compare-and-delete/release behaviour.  We only add a
    bounded watchdog so an ordinary LLM/tool turn does not outlive its initial
    TTL.  This is intentionally a lease, not an exactly-once workflow claim:
    if renewal is lost, settlement is stopped and the caller receives a clear
    coordination failure rather than silently continuing cross-worker.
    """

    def __init__(self, raw_lock: Any, *, lease_seconds: float):
        self._raw_lock = raw_lock
        self._lease_seconds = lease_seconds
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._watchdog: Optional[threading.Thread] = None

    def acquire(self) -> None:
        try:
            acquired = self._raw_lock.acquire(blocking=True)
        except Exception as exc:
            raise SessionCoordinationUnavailable("会话串行租约不可用") from exc
        if not acquired:
            raise SessionCoordinationUnavailable("会话正由另一实例处理，请稍后重试")

        interval = max(0.25, self._lease_seconds / 3.0)

        def renew() -> None:
            while not self._stop.wait(interval):
                try:
                    # redis-py Lock checks the ownership token in Lua.  Use
                    # replace_ttl so this remains a fixed-size sliding lease.
                    if self._raw_lock.extend(self._lease_seconds, replace_ttl=True) is False:
                        raise SessionCoordinationLost("会话串行租约续租失败")
                except Exception as exc:
                    logger.error("session Redis lease renewal lost: %s", exc)
                    self._lost.set()
                    return

        self._watchdog = threading.Thread(
            target=renew,
            name="cpa-session-lease-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def assert_held(self) -> None:
        if self._lost.is_set():
            raise SessionCoordinationLost("会话串行租约在本轮执行期间丢失")
        # A paused watchdog can miss the TTL boundary until after this turn
        # settles. Check the server-side token at the settlement boundary.
        try:
            owned = self._raw_lock.owned()
        except Exception as exc:
            self._lost.set()
            raise SessionCoordinationLost("无法核验会话串行租约") from exc
        if not owned:
            self._lost.set()
            raise SessionCoordinationLost("会话串行租约在本轮执行期间丢失")

    def release(self) -> None:
        self._stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=max(0.5, self._lease_seconds / 3.0 + 0.1))
        try:
            self._raw_lock.release()
        except Exception as exc:
            # The lease may already have expired after a fatal Redis outage.
            # Do not hide a known lost lease behind a release-only exception.
            if not self._lost.is_set():
                raise SessionCoordinationLost("会话串行租约无法安全释放") from exc


class SessionCoordinator:
    """Per-session turn admission for one process, optionally one Redis scope.

    OpenCode persists and promotes one admitted session input at a time in
    ``packages/core/src/session/input.ts``.  CPA-Agent does not yet have that
    durable input queue, so this narrow adapter uses the already-required
    Redis service only to serialize the existing request-bound turn lane.
    """

    def __init__(
        self,
        *,
        redis_lock_factory: Optional[Callable[[str], Any]] = None,
        redis_required: bool = False,
        lease_seconds: float = 180.0,
    ):
        self._locks: Dict[str, threading.Lock] = {}
        self._ref_counts: Dict[str, int] = {}
        self._global_lock = threading.Lock()
        self._redis_lock_factory = redis_lock_factory
        self._redis_required = redis_required
        self._lease_seconds = max(5.0, float(lease_seconds))

    def acquire(self, session_id: str) -> threading.Lock:
        """获取 session 级锁。同一 session_id 串行执行。"""
        with self._global_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            self._ref_counts[session_id] = self._ref_counts.get(session_id, 0) + 1
            return self._locks[session_id]

    def release(self, session_id: str):
        """释放引用计数，清理空闲锁。"""
        with self._global_lock:
            count = self._ref_counts.get(session_id, 1) - 1
            self._ref_counts[session_id] = count
            if count <= 0:
                self._locks.pop(session_id, None)
                self._ref_counts.pop(session_id, None)

    @contextmanager
    def lock(self, session_id: str):
        """Acquire local then optional cross-process lease for one session.

        A Redis outage is fail-closed only when deployment explicitly selected
        ``redis_required``.  The development fallback stays process-local and
        logs that it offers no cross-worker guarantee.
        """
        local_lock = self.acquire(session_id)
        local_lock.acquire()
        redis_lease: Optional[_RedisLease] = None
        try:
            if self._redis_lock_factory is not None:
                try:
                    redis_lease = _RedisLease(
                        self._redis_lock_factory(session_id),
                        lease_seconds=self._lease_seconds,
                    )
                    redis_lease.acquire()
                except SessionCoordinationUnavailable:
                    if self._redis_required:
                        raise
                    logger.warning(
                        "session=%s uses local-only turn lock; cross-worker ordering is unavailable",
                        session_id,
                    )
                    redis_lease = None
                except Exception as exc:
                    if self._redis_required:
                        raise SessionCoordinationUnavailable("会话串行租约不可用") from exc
                    logger.warning(
                        "session=%s uses local-only turn lock after Redis setup failure; "
                        "cross-worker ordering is unavailable: %s",
                        session_id,
                        type(exc).__name__,
                    )
                    redis_lease = None
            yield TurnLease(
                assert_held=redis_lease.assert_held if redis_lease else lambda: None,
                require_authoritative_session=self._redis_required,
            )
        finally:
            # A Redis release failure must still release the process-local
            # mutex/refcount; otherwise one failed remote lease deadlocks this
            # worker forever even though the caller receives an error.
            try:
                if redis_lease is not None:
                    redis_lease.release()
            finally:
                local_lock.release()
                self.release(session_id)


_coordinator: Optional[SessionCoordinator] = None
_coordinator_lock = threading.Lock()


def _redis_lock_factory(session_id: str):
    """Build a token-checked redis-py lock without exposing raw session IDs."""
    from ...config import Config
    from ..db.redis_client import get_redis_client

    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return get_redis_client().client.lock(
        f"cpa:agent:turn-lock:{digest}",
        timeout=float(Config.SESSION_TURN_LOCK_LEASE_SECONDS),
        blocking_timeout=float(Config.SESSION_TURN_LOCK_WAIT_SECONDS),
        # Watchdog renews from another thread, so token storage cannot be
        # thread-local (redis-py otherwise rejects extend/release there).
        thread_local=False,
    )


def get_session_coordinator() -> SessionCoordinator:
    """获取全局 SessionCoordinator 单例（线程安全）。"""
    global _coordinator
    if _coordinator is None:
        with _coordinator_lock:
            if _coordinator is None:
                from ...config import Config

                mode = str(Config.SESSION_TURN_LOCK_MODE or "redis_preferred").lower()
                if mode not in {"local", "redis_preferred", "redis_required"}:
                    logger.warning("unknown CPA_SESSION_TURN_LOCK_MODE=%s; using redis_preferred", mode)
                    mode = "redis_preferred"
                _coordinator = SessionCoordinator(
                    redis_lock_factory=_redis_lock_factory if mode != "local" else None,
                    redis_required=mode == "redis_required",
                    lease_seconds=float(Config.SESSION_TURN_LOCK_LEASE_SECONDS),
                )
    return _coordinator
