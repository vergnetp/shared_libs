"""
Concurrency utilities for multi-agent safety.

Provides locks and thread-safe patterns for shared resources.

Key shared resources that need protection:
- User context (multiple agents updating same user)
- Thread messages (if agents share a thread)
- External resources (files, databases, APIs)

Usage:
    from ai_agents.concurrency import (
        get_lock,
        with_lock,
        ResourceLock,
        LockManager,
    )
    
    # Simple locking
    async with get_lock("user_context", user_id):
        await update_context(...)
    
    # Decorator
    @with_lock("user_context", key_arg="user_id")
    async def update_user(user_id: str, data: dict):
        ...
"""
from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary
import time


@dataclass
class LockStats:
    """Statistics for a lock."""
    acquisitions: int = 0
    contentions: int = 0  # Times had to wait
    total_wait_ms: float = 0
    total_held_ms: float = 0
    
    @property
    def avg_wait_ms(self) -> float:
        return self.total_wait_ms / max(1, self.contentions)
    
    @property
    def avg_held_ms(self) -> float:
        return self.total_held_ms / max(1, self.acquisitions)


class LockManager:
    """
    Manages named locks for shared resources.
    
    Singleton pattern - use get_lock_manager() to access.
    
    Features:
    - Named lock namespaces (user_context, thread, etc.)
    - Per-key locks within namespaces
    - Automatic cleanup of unused locks
    - Statistics tracking
    - Timeout support
    """
    
    _instance: Optional["LockManager"] = None
    
    def __init__(self):
        # namespace -> key -> lock
        self._locks: dict[str, dict[str, asyncio.Lock]] = {}
        self._stats: dict[str, dict[str, LockStats]] = {}
        self._creation_lock = asyncio.Lock()
        
        # Track last access for cleanup
        self._last_access: dict[str, dict[str, float]] = {}
    
    @classmethod
    def get_instance(cls) -> "LockManager":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None
    
    async def get_lock(self, namespace: str, key: str) -> asyncio.Lock:
        """Get or create a lock for namespace:key."""
        if namespace not in self._locks:
            async with self._creation_lock:
                if namespace not in self._locks:
                    self._locks[namespace] = {}
                    self._stats[namespace] = {}
                    self._last_access[namespace] = {}
        
        if key not in self._locks[namespace]:
            async with self._creation_lock:
                if key not in self._locks[namespace]:
                    self._locks[namespace][key] = asyncio.Lock()
                    self._stats[namespace][key] = LockStats()
        
        self._last_access[namespace][key] = time.time()
        return self._locks[namespace][key]
    
    async def acquire(
        self,
        namespace: str,
        key: str,
        timeout: float = None,
    ) -> bool:
        """
        Acquire a lock with optional timeout.
        
        Returns True if acquired, False if timeout.
        """
        lock = await self.get_lock(namespace, key)
        stats = self._stats[namespace][key]
        
        start = time.time()
        was_locked = lock.locked()
        
        if was_locked:
            stats.contentions += 1
        
        try:
            if timeout is not None:
                acquired = await asyncio.wait_for(
                    lock.acquire(),
                    timeout=timeout,
                )
            else:
                await lock.acquire()
                acquired = True
            
            if was_locked:
                stats.total_wait_ms += (time.time() - start) * 1000
            
            stats.acquisitions += 1
            return acquired
            
        except asyncio.TimeoutError:
            return False
    
    def release(self, namespace: str, key: str, held_since: float = None):
        """Release a lock."""
        if namespace in self._locks and key in self._locks[namespace]:
            lock = self._locks[namespace][key]
            
            if held_since and namespace in self._stats and key in self._stats[namespace]:
                self._stats[namespace][key].total_held_ms += (time.time() - held_since) * 1000
            
            if lock.locked():
                lock.release()
    
    @asynccontextmanager
    async def lock(
        self,
        namespace: str,
        key: str,
        timeout: float = None,
    ):
        """
        Context manager for acquiring a lock.
        
        Usage:
            async with manager.lock("user_context", user_id):
                await update_context(...)
        """
        held_since = time.time()
        acquired = await self.acquire(namespace, key, timeout)
        
        if not acquired:
            raise asyncio.TimeoutError(f"Could not acquire lock {namespace}:{key}")
        
        try:
            yield
        finally:
            self.release(namespace, key, held_since)
    
    def get_stats(self, namespace: str = None) -> dict:
        """Get lock statistics."""
        if namespace:
            return {
                key: {
                    "acquisitions": s.acquisitions,
                    "contentions": s.contentions,
                    "avg_wait_ms": s.avg_wait_ms,
                    "avg_held_ms": s.avg_held_ms,
                }
                for key, s in self._stats.get(namespace, {}).items()
            }
        
        return {
            ns: self.get_stats(ns)
            for ns in self._stats.keys()
        }
    
    async def cleanup(self, max_age_seconds: float = 3600):
        """Remove locks not accessed recently."""
        now = time.time()
        
        async with self._creation_lock:
            for namespace in list(self._locks.keys()):
                for key in list(self._locks[namespace].keys()):
                    last_access = self._last_access.get(namespace, {}).get(key, 0)
                    if now - last_access > max_age_seconds:
                        lock = self._locks[namespace][key]
                        if not lock.locked():
                            del self._locks[namespace][key]
                            del self._stats[namespace][key]
                            del self._last_access[namespace][key]


# Global lock manager instance
_lock_manager: Optional[LockManager] = None


def get_lock_manager() -> LockManager:
    """Get the global lock manager."""
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = LockManager.get_instance()
    return _lock_manager


@asynccontextmanager
async def get_lock(
    namespace: str,
    key: str,
    timeout: float = None,
):
    """
    Convenience function to get a lock.
    
    Usage:
        async with get_lock("user_context", user_id):
            await update_context(...)
    """
    manager = get_lock_manager()
    async with manager.lock(namespace, key, timeout):
        yield


def with_lock(
    namespace: str,
    key_arg: str = None,
    key_kwarg: str = None,
    timeout: float = None,
):
    """
    Decorator to automatically lock a function.
    
    Args:
        namespace: Lock namespace
        key_arg: Positional arg name to use as key
        key_kwarg: Keyword arg name to use as key
        timeout: Lock timeout
        
    Usage:
        @with_lock("user_context", key_kwarg="user_id")
        async def update_user(user_id: str, data: dict):
            ...
        
        @with_lock("thread", key_arg="thread_id")
        async def add_message(thread_id: str, message: dict):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract key from args/kwargs
            key = None
            
            if key_kwarg and key_kwarg in kwargs:
                key = str(kwargs[key_kwarg])
            elif key_arg:
                # Find position of key_arg in function signature
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if key_arg in params:
                    idx = params.index(key_arg)
                    if idx < len(args):
                        key = str(args[idx])
                    elif key_arg in kwargs:
                        key = str(kwargs[key_arg])
            
            if key is None:
                key = "default"
            
            async with get_lock(namespace, key, timeout):
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# =============================================================================
# RESOURCE-SPECIFIC LOCKS
# =============================================================================

# Pre-defined namespaces for common resources
LOCK_USER_CONTEXT = "user_context"
LOCK_THREAD = "thread"
LOCK_AGENT = "agent"
LOCK_FILE = "file"
LOCK_EXTERNAL = "external"


@asynccontextmanager
async def user_context_lock(user_id: str, agent_id: str = None, timeout: float = 30.0):
    """
    Lock for user context updates.
    
    Prevents race conditions when multiple agents update same user's context.
    """
    key = f"{user_id}:{agent_id}" if agent_id else user_id
    async with get_lock(LOCK_USER_CONTEXT, key, timeout):
        yield


@asynccontextmanager
async def thread_lock(thread_id: str, timeout: float = 30.0):
    """
    Lock for thread message operations.
    
    Prevents race conditions when multiple agents write to same thread.
    """
    async with get_lock(LOCK_THREAD, thread_id, timeout):
        yield


@asynccontextmanager
async def file_lock(file_path: str, timeout: float = 60.0):
    """
    Lock for file operations.
    
    Prevents race conditions when multiple agents access same file.
    """
    async with get_lock(LOCK_FILE, file_path, timeout):
        yield


# =============================================================================
# THREAD-SAFE TOOL WRAPPER
# =============================================================================

class ThreadSafeTool:
    """
    Wrapper to make any tool thread-safe.
    
    Usage:
        # Wrap existing tool
        safe_tool = ThreadSafeTool(
            tool=my_tool,
            lock_namespace="my_resource",
            key_extractor=lambda kwargs: kwargs.get("resource_id"),
        )
        
        # Or use decorator on tool class
        @thread_safe_tool("my_resource", key_arg="resource_id")
        class MyTool(Tool):
            async def execute(self, resource_id: str, ...):
                ...
    """
    
    def __init__(
        self,
        tool,
        lock_namespace: str,
        key_extractor: Callable[[dict], str],
        timeout: float = 30.0,
    ):
        self._tool = tool
        self._namespace = lock_namespace
        self._key_extractor = key_extractor
        self._timeout = timeout
        
        # Forward tool attributes
        self.name = tool.name
        self.description = getattr(tool, 'description', '')
    
    async def execute(self, **kwargs) -> Any:
        key = self._key_extractor(kwargs) or "default"
        
        async with get_lock(self._namespace, key, self._timeout):
            return await self._tool.execute(**kwargs)
    
    def get_definition(self):
        return self._tool.get_definition()
    
    def to_dict(self):
        return self._tool.to_dict()


def thread_safe_tool(
    namespace: str,
    key_arg: str = None,
    timeout: float = 30.0,
):
    """
    Decorator to make a Tool class thread-safe.
    
    Usage:
        @thread_safe_tool("user_context", key_arg="user_id")
        class UpdateContextTool(Tool):
            async def execute(self, user_id: str, updates: dict):
                ...
    """
    def decorator(cls):
        original_execute = cls.execute
        
        @functools.wraps(original_execute)
        async def safe_execute(self, **kwargs):
            key = kwargs.get(key_arg, "default") if key_arg else "default"
            
            async with get_lock(namespace, str(key), timeout):
                return await original_execute(self, **kwargs)
        
        cls.execute = safe_execute
        return cls
    
    return decorator


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Core
    "LockManager",
    "get_lock_manager",
    "get_lock",
    "with_lock",
    "LockStats",
    # Resource locks
    "user_context_lock",
    "thread_lock",
    "file_lock",
    "LOCK_USER_CONTEXT",
    "LOCK_THREAD",
    "LOCK_AGENT",
    "LOCK_FILE",
    "LOCK_EXTERNAL",
    # Tool safety
    "ThreadSafeTool",
    "thread_safe_tool",
]
