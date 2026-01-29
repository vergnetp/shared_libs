"""Cache client - Redis with in-memory fallback."""

import json
import time
from typing import Any, Optional, Dict
from collections import OrderedDict


class InMemoryCache:
    """Simple in-memory LRU cache (fallback when Redis unavailable)."""
    
    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
    
    async def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        
        value, expires_at = self._cache[key]
        
        # Check expiry
        if expires_at and time.time() > expires_at:
            del self._cache[key]
            return None
        
        # Move to end (LRU)
        self._cache.move_to_end(key)
        return value
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expires_at = time.time() + ttl if ttl else None
        
        # Remove oldest if at capacity
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        
        self._cache[key] = (value, expires_at)
        self._cache.move_to_end(key)
    
    async def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern (supports * wildcard)."""
        import fnmatch
        
        to_delete = [k for k in self._cache if fnmatch.fnmatch(k, pattern)]
        for key in to_delete:
            del self._cache[key]
        return len(to_delete)
    
    async def clear(self) -> None:
        self._cache.clear()
    
    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None


class RedisCache:
    """Redis-backed cache."""
    
    def __init__(self, redis_client, prefix: str = "cache:"):
        self._redis = redis_client
        self._prefix = prefix
    
    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"
    
    async def get(self, key: str) -> Optional[Any]:
        try:
            value = await self._redis.get(self._key(key))
            if value is None:
                return None
            return json.loads(value)
        except Exception:
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            serialized = json.dumps(value)
            if ttl:
                await self._redis.setex(self._key(key), ttl, serialized)
            else:
                await self._redis.set(self._key(key), serialized)
        except Exception:
            pass  # Fail silently
    
    async def delete(self, key: str) -> bool:
        try:
            result = await self._redis.delete(self._key(key))
            return result > 0
        except Exception:
            return False
    
    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern."""
        try:
            keys = []
            async for key in self._redis.scan_iter(self._key(pattern)):
                keys.append(key)
            
            if keys:
                return await self._redis.delete(*keys)
            return 0
        except Exception:
            return 0
    
    async def clear(self) -> None:
        """Clear all cache keys (dangerous!)."""
        await self.delete_pattern("*")
    
    async def exists(self, key: str) -> bool:
        try:
            return await self._redis.exists(self._key(key)) > 0
        except Exception:
            return False


class Cache:
    """
    Cache facade - uses Redis if available, falls back to in-memory.
    
    In-memory cache doesn't share across processes, but still helps
    reduce repeated expensive operations within a single process.
    """
    
    def __init__(self, redis_url: Optional[str] = None, prefix: str = "cache:"):
        self._redis_url = redis_url
        self._prefix = prefix
        self._backend: Optional[Any] = None
        self._initialized = False
    
    async def _ensure_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        
        if self._redis_url:
            try:
                import redis.asyncio as redis
                client = redis.from_url(self._redis_url)
                # Test connection
                await client.ping()
                self._backend = RedisCache(client, self._prefix)
            except Exception as e:
                # Fall back to in-memory
                import logging
                logging.warning(f"Redis cache unavailable, using in-memory: {e}")
                self._backend = InMemoryCache()
        else:
            self._backend = InMemoryCache()
        
        self._initialized = True
        return self._backend
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        backend = await self._ensure_backend()
        return await backend.get(key)
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with optional TTL (seconds)."""
        backend = await self._ensure_backend()
        await backend.set(key, value, ttl)
    
    async def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        backend = await self._ensure_backend()
        return await backend.delete(key)
    
    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern (e.g., 'projects:*')."""
        backend = await self._ensure_backend()
        return await backend.delete_pattern(pattern)
    
    async def clear(self) -> None:
        """Clear entire cache."""
        backend = await self._ensure_backend()
        await backend.clear()
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        backend = await self._ensure_backend()
        return await backend.exists(key)
    
    async def get_or_set(
        self,
        key: str,
        factory,
        ttl: Optional[int] = None,
    ) -> Any:
        """Get from cache or call factory to populate."""
        value = await self.get(key)
        if value is not None:
            return value
        
        # Call factory (can be sync or async)
        import asyncio
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        await self.set(key, value, ttl)
        return value


# Module-level singleton
_cache: Optional[Cache] = None


def init_cache(redis_url: Optional[str] = None, prefix: str = "cache:") -> Cache:
    """Initialize the global cache instance."""
    global _cache
    _cache = Cache(redis_url=redis_url, prefix=prefix)
    return _cache


def get_cache() -> Cache:
    """Get the global cache instance."""
    global _cache
    if _cache is None:
        _cache = Cache()  # Default to in-memory
    return _cache


# Convenience - module-level cache
cache = get_cache()
