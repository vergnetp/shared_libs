"""
Simple TTL Cache for HTTP responses.

Useful for API calls that don't change frequently:
- Server lists (refresh every 30-60s)
- Configuration data (refresh every 5 min)
- Static resources (refresh hourly)

Usage:
    from http_client import cached_request, get_cache
    
    # Decorator for functions
    @cached_request(ttl=30)
    async def get_droplets(client, token):
        return await client.get("/v2/droplets", headers={"Authorization": f"Bearer {token}"})
    
    # Direct cache access
    cache = get_cache()
    result = await cache.get_or_set("droplets:abc", fetch_func, ttl=30)
    
    # Invalidate
    await cache.invalidate("droplets:abc")
    await cache.invalidate_prefix("droplets:")
"""

from __future__ import annotations
import asyncio
import time
import hashlib
import functools
from typing import Dict, Any, Optional, Callable, TypeVar
from dataclasses import dataclass

T = TypeVar('T')


@dataclass
class CacheEntry:
    """Cached value with expiration."""
    value: Any
    expires_at: float
    created_at: float
    hit_count: int = 0
    
    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
    
    @property
    def ttl_remaining(self) -> float:
        return max(0, self.expires_at - time.time())


class ResponseCache:
    """
    Async-safe TTL cache for HTTP responses.
    
    Features:
        - TTL-based expiration
        - Async-safe with locks
        - Cache statistics
        - Prefix-based invalidation
    """
    
    def __init__(
        self,
        default_ttl: float = 60.0,
        max_entries: int = 1000,
    ):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        
        # Stats
        self._hits = 0
        self._misses = 0
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value if exists and not expired."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry and not entry.is_expired:
                entry.hit_count += 1
                self._hits += 1
                return entry.value
            
            # Remove expired entry
            if entry:
                del self._cache[key]
            
            self._misses += 1
            return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: float = None,
    ) -> None:
        """Set value with TTL."""
        ttl = ttl if ttl is not None else self._default_ttl
        now = time.time()
        
        async with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self._max_entries and key not in self._cache:
                await self._evict_oldest_unlocked()
            
            self._cache[key] = CacheEntry(
                value=value,
                expires_at=now + ttl,
                created_at=now,
            )
    
    async def get_or_set(
        self,
        key: str,
        factory: Callable,
        ttl: float = None,
    ) -> Any:
        """Get from cache or compute and cache."""
        # Check cache first (without lock for read)
        value = await self.get(key)
        if value is not None:
            return value
        
        # Compute value (outside lock to not block others)
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        # Cache it
        await self.set(key, value, ttl)
        return value
    
    async def invalidate(self, key: str) -> bool:
        """Remove key from cache. Returns True if key existed."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def invalidate_prefix(self, prefix: str) -> int:
        """Remove all keys starting with prefix. Returns count removed."""
        async with self._lock:
            keys = [k for k in self._cache if k.startswith(prefix)]
            for key in keys:
                del self._cache[key]
            return len(keys)
    
    async def clear(self) -> None:
        """Clear entire cache."""
        async with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    async def _evict_oldest_unlocked(self) -> None:
        """Evict oldest entry (must hold lock)."""
        if not self._cache:
            return
        
        # Find oldest by created_at
        oldest_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k].created_at
        )
        del self._cache[oldest_key]
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        
        # Count expired entries
        now = time.time()
        expired = sum(1 for e in self._cache.values() if e.is_expired)
        
        return {
            "entries": len(self._cache),
            "expired_entries": expired,
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
            "default_ttl": self._default_ttl,
        }


# Global cache instance
_cache: Optional[ResponseCache] = None


def get_cache() -> ResponseCache:
    """Get the global response cache."""
    global _cache
    if _cache is None:
        _cache = ResponseCache()
    return _cache


def make_cache_key(*args, **kwargs) -> str:
    """Generate cache key from arguments."""
    parts = [str(a) for a in args]
    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    combined = ":".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def cached_request(
    ttl: float = 60.0,
    key_prefix: str = "",
    include_args: bool = True,
):
    """
    Decorator to cache async function results.
    
    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache key (default: function name)
        include_args: Include function args in cache key
        
    Usage:
        @cached_request(ttl=30, key_prefix="droplets")
        async def list_droplets(token: str):
            ...
            
        # First call: hits API
        result = await list_droplets("abc123")
        
        # Second call within 30s: returns cached
        result = await list_droplets("abc123")
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache = get_cache()
            
            # Build cache key
            prefix = key_prefix or func.__name__
            if include_args:
                key = f"{prefix}:{make_cache_key(*args, **kwargs)}"
            else:
                key = prefix
            
            # Get or compute
            return await cache.get_or_set(
                key,
                lambda: func(*args, **kwargs),
                ttl,
            )
        
        # Add cache control methods to wrapper
        wrapper.invalidate = lambda *args, **kwargs: get_cache().invalidate(
            f"{key_prefix or func.__name__}:{make_cache_key(*args, **kwargs)}"
        )
        wrapper.invalidate_all = lambda: get_cache().invalidate_prefix(
            key_prefix or func.__name__
        )
        
        return wrapper
    return decorator


async def clear_cache() -> None:
    """Clear the global response cache."""
    cache = get_cache()
    await cache.clear()


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return get_cache().stats()
