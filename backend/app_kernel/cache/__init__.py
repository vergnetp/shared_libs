"""
Cache - Redis-backed with fallback to in-memory.

Usage:
    from app_kernel.cache import cache
    
    # Simple get/set
    await cache.set("projects:ws-123", projects, ttl=300)
    projects = await cache.get("projects:ws-123")
    await cache.delete("projects:ws-123")
    
    # Pattern delete
    await cache.delete_pattern("projects:*")
    
    # Decorator
    from app_kernel.cache import cached
    
    @cached(ttl=300, key="projects:{workspace_id}")
    async def get_projects(workspace_id: str):
        return await db.find_entities("projects", ...)
"""

from .client import (
    Cache,
    NoOpCache,
    get_cache,
    init_cache,
)
from .decorator import cached, CACHE_ENABLED


def __getattr__(name):
    """Lazy access to cache singleton via `from app_kernel.cache import cache`."""
    if name == "cache":
        return get_cache()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Cache",
    "NoOpCache",
    "get_cache",
    "init_cache",
    "cache",
    "cached",
    "CACHE_ENABLED",
]