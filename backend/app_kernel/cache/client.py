"""Cache client - Redis with fakeredis fallback."""

import json
import time
from typing import Any, Optional
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
    """Async Redis-backed cache."""
    
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


class NoOpCache:
    """
    Cache that does nothing — used in prod when real Redis is unavailable.
    
    Safer than InMemoryCache in multi-droplet prod: no stale data, no
    cross-instance invalidation issues. Every call hits the DB.
    """
    
    async def get(self, key: str) -> Optional[Any]:
        return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        pass
    
    async def delete(self, key: str) -> bool:
        return False
    
    async def delete_pattern(self, pattern: str) -> int:
        return 0
    
    async def clear(self) -> None:
        pass
    
    async def exists(self, key: str) -> bool:
        return False


class Cache:
    """
    Cache facade with environment-aware backend selection.
    
    - Real Redis available → RedisCache (shared across droplets)
    - Dev without Redis → InMemoryCache (single process, good enough)
    - Prod without Redis → NoOpCache (disabled, no stale data risk)
    """
    
    def __init__(
        self,
        redis_client=None,
        prefix: str = "cache:",
        is_fake: bool = False,
        is_prod: bool = False,
    ):
        self._prefix = prefix
        self._backend: Optional[Any] = None
        
        if redis_client and not is_fake:
            # Real Redis — shared cache across all droplets
            self._backend = RedisCache(redis_client, prefix)
        elif is_prod:
            # Prod without real Redis — disable caching entirely
            self._backend = NoOpCache()
        else:
            # Dev without Redis — in-memory is fine
            self._backend = InMemoryCache()
    
    @property
    def backend_type(self) -> str:
        """Return the backend type for logging."""
        if isinstance(self._backend, RedisCache):
            return "redis"
        elif isinstance(self._backend, NoOpCache):
            return "disabled"
        else:
            return "in-memory"
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        return await self._backend.get(key)
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with optional TTL (seconds)."""
        await self._backend.set(key, value, ttl)
    
    async def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        return await self._backend.delete(key)
    
    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern (e.g., 'projects:*')."""
        return await self._backend.delete_pattern(pattern)
    
    async def clear(self) -> None:
        """Clear entire cache."""
        await self._backend.clear()
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self._backend.exists(key)
    
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
        
        import asyncio
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        await self.set(key, value, ttl)
        return value


# Module-level singleton
_cache: Optional[Cache] = None


def init_cache(
    redis_client=None,
    prefix: str = "cache:",
    is_fake: bool = False,
    is_prod: bool = False,
) -> Cache:
    """
    Initialize the global cache instance.
    
    Args:
        redis_client: Async Redis client (real or fakeredis)
        prefix: Key prefix for cache entries
        is_fake: Whether the client is fakeredis
        is_prod: Whether running in production
    """
    global _cache
    _cache = Cache(
        redis_client=redis_client,
        prefix=prefix,
        is_fake=is_fake,
        is_prod=is_prod,
    )
    return _cache


def get_cache() -> Cache:
    """Get the global cache instance."""
    global _cache
    if _cache is None:
        # Not initialized by bootstrap — default to in-memory (dev)
        _cache = Cache()
    return _cache
