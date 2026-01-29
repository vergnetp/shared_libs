"""Cache decorator for functions."""

import functools
from typing import Optional, Callable


def cached(
    ttl: int = 300,
    key: Optional[str] = None,
    prefix: str = "",
):
    """
    Decorator to cache function results.
    
    Args:
        ttl: Time to live in seconds
        key: Cache key template with {arg_name} placeholders
        prefix: Optional key prefix
    
    Usage:
        @cached(ttl=300, key="projects:{workspace_id}")
        async def get_projects(workspace_id: str):
            return await db.find_entities("projects", ...)
        
        # Cache key will be "projects:ws-123"
        
        @cached(ttl=60)
        async def get_stats():
            # Key auto-generated from function name
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            from .client import get_cache
            
            cache = get_cache()
            
            # Build cache key
            cache_key = _build_key(func, key, prefix, args, kwargs)
            
            # Try cache
            result = await cache.get(cache_key)
            if result is not None:
                return result
            
            # Call function
            result = await func(*args, **kwargs)
            
            # Store in cache
            if result is not None:
                await cache.set(cache_key, result, ttl)
            
            return result
        
        # Add cache control methods
        wrapper.cache_key = lambda *a, **kw: _build_key(func, key, prefix, a, kw)
        wrapper.invalidate = lambda *a, **kw: get_cache().delete(_build_key(func, key, prefix, a, kw))
        
        return wrapper
    
    return decorator


def _build_key(
    func: Callable,
    key_template: Optional[str],
    prefix: str,
    args: tuple,
    kwargs: dict,
) -> str:
    """Build cache key from template and arguments."""
    import inspect
    
    if key_template:
        # Get function signature to map args to names
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        
        # Format template with arguments
        try:
            cache_key = key_template.format(**bound.arguments)
        except KeyError:
            # Fallback if template has unknown keys
            cache_key = f"{func.__module__}.{func.__name__}:{args}:{kwargs}"
    else:
        # Auto-generate key from function name and args
        cache_key = f"{func.__module__}.{func.__name__}"
        if args:
            cache_key += f":{hash(args)}"
        if kwargs:
            cache_key += f":{hash(frozenset(kwargs.items()))}"
    
    if prefix:
        cache_key = f"{prefix}:{cache_key}"
    
    return cache_key
