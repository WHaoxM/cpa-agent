"""KeyedMutex并发控制

提供按key序列化的并发控制，不同key可并行。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional


class KeyedMutex:
    """按key序列化的互斥锁"""
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._ref_counts: Dict[str, int] = {}
        self._wait_counts: Dict[str, int] = {}
    
    async def acquire(self, key: str):
        """获取锁"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
            self._ref_counts[key] = 0
            self._wait_counts[key] = 0
        
        self._wait_counts[key] += 1
        try:
            await self._locks[key].acquire()
            self._ref_counts[key] += 1
        finally:
            self._wait_counts[key] -= 1
    
    async def release(self, key: str):
        """释放锁"""
        if key in self._locks:
            self._locks[key].release()
            self._ref_counts[key] -= 1
            
            # 如果没有持有者且没有等待者，清理锁
            if self._ref_counts[key] == 0 and self._wait_counts[key] == 0:
                del self._locks[key]
                del self._ref_counts[key]
                del self._wait_counts[key]
    
    @asynccontextmanager
    async def lock(self, key: str):
        """上下文管理器"""
        await self.acquire(key)
        try:
            yield
        finally:
            await self.release(key)
    
    def is_locked(self, key: str) -> bool:
        """检查是否被锁定"""
        return key in self._locks and self._locks[key].locked()
    
    def get_wait_count(self, key: str) -> int:
        """获取等待数量"""
        return self._wait_counts.get(key, 0)
    
    def get_holders(self) -> Dict[str, int]:
        """获取所有持有者"""
        return {k: v for k, v in self._ref_counts.items() if v > 0}
    
    def cleanup(self):
        """清理空闲锁"""
        keys_to_remove = [
            key for key in self._locks
            if self._ref_counts.get(key, 0) == 0 and self._wait_counts.get(key, 0) == 0
        ]
        for key in keys_to_remove:
            del self._locks[key]
            del self._ref_counts[key]
            del self._wait_counts[key]


class Semaphore:
    """信号量"""
    
    def __init__(self, max_concurrent: int = 10):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._current = 0
    
    async def acquire(self):
        """获取信号量"""
        await self._semaphore.acquire()
        self._current += 1
    
    async def release(self):
        """释放信号量"""
        self._semaphore.release()
        self._current -= 1
    
    @asynccontextmanager
    async def limit(self):
        """上下文管理器"""
        await self.acquire()
        try:
            yield
        finally:
            await self.release()
    
    @property
    def current(self) -> int:
        """当前使用数量"""
        return self._current
    
    @property
    def available(self) -> int:
        """可用数量"""
        return self._max_concurrent - self._current


class TaskGroup:
    """任务组"""
    
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}
        self._errors: Dict[str, Exception] = {}
    
    def add_task(self, name: str, coro):
        """添加任务"""
        task = asyncio.create_task(coro)
        self._tasks[name] = task
        task.add_done_callback(lambda t: self._task_done(name, t))
        return task
    
    def _task_done(self, name: str, task: asyncio.Task):
        """任务完成回调"""
        if name in self._tasks:
            del self._tasks[name]
        
        if task.exception():
            self._errors[name] = task.exception()
        else:
            self._results[name] = task.result()
    
    async def wait_all(self):
        """等待所有任务完成"""
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
    
    async def wait_any(self):
        """等待任意任务完成"""
        if self._tasks:
            done, pending = await asyncio.wait(
                self._tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            return done
        return set()
    
    def cancel_all(self):
        """取消所有任务"""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
    
    def get_results(self) -> Dict[str, Any]:
        """获取所有结果"""
        return self._results.copy()
    
    def get_errors(self) -> Dict[str, Exception]:
        """获取所有错误"""
        return self._errors.copy()
    
    def is_complete(self) -> bool:
        """检查是否完成"""
        return len(self._tasks) == 0
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "running": len(self._tasks),
            "completed": len(self._results),
            "failed": len(self._errors),
        }


class RateLimiter:
    """速率限制器"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: Dict[str, list] = {}
    
    async def check(self, key: str) -> bool:
        """检查是否允许请求"""
        import time
        
        now = time.time()
        if key not in self._requests:
            self._requests[key] = []
        
        # 清理过期请求
        self._requests[key] = [
            t for t in self._requests[key]
            if now - t < self._window_seconds
        ]
        
        # 检查是否超过限制
        if len(self._requests[key]) >= self._max_requests:
            return False
        
        # 记录请求
        self._requests[key].append(now)
        return True
    
    def get_remaining(self, key: str) -> int:
        """获取剩余请求数"""
        import time
        
        now = time.time()
        if key not in self._requests:
            return self._max_requests
        
        # 清理过期请求
        self._requests[key] = [
            t for t in self._requests[key]
            if now - t < self._window_seconds
        ]
        
        return max(0, self._max_requests - len(self._requests[key]))
    
    def reset(self, key: str = None):
        """重置"""
        if key:
            self._requests.pop(key, None)
        else:
            self._requests.clear()


# 全局实例
_keyed_mutex: Optional[KeyedMutex] = None
_semaphore: Optional[Semaphore] = None


def get_keyed_mutex() -> KeyedMutex:
    """获取KeyedMutex单例"""
    global _keyed_mutex
    if _keyed_mutex is None:
        _keyed_mutex = KeyedMutex()
    return _keyed_mutex


def get_semaphore(max_concurrent: int = 10) -> Semaphore:
    """获取Semaphore单例"""
    global _semaphore
    if _semaphore is None:
        _semaphore = Semaphore(max_concurrent)
    return _semaphore
