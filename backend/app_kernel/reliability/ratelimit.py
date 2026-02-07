"""
Rate limiting middleware.

Provides async Redis-backed rate limiting for API endpoints.
Uses fakeredis.aioredis as fallback when real Redis unavailable.

Usage:
    from app_kernel.reliability import rate_limit
    
    @app.post("/api/action")
    async def action(
        user: UserIdentity = Depends(get_current_user),
        _: None = Depends(rate_limit(requests=10, window=60))
    ):
        ...
"""
from typing import Optional, Callable
from dataclasses import dataclass
import time

from fastapi import Request, HTTPException, Depends

from ..auth.models import UserIdentity
from ..auth.deps import get_current_user_optional


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""
    requests: int = 100  # requests per window
    window_seconds: int = 60  # window size
    key_prefix: str = "ratelimit:"


class RateLimiter:
    """
    Async Redis-backed sliding window rate limiter.
    
    Uses a sorted set to track request timestamps.
    Works with real redis.asyncio or fakeredis.aioredis.
    """
    
    def __init__(self, redis_client, config: Optional[RateLimitConfig] = None):
        """
        Initialize rate limiter.
        
        Args:
            redis_client: Async Redis client (redis.asyncio or fakeredis.aioredis)
            config: Rate limit configuration
        """
        self._redis = redis_client
        self._config = config or RateLimitConfig()
    
    def _get_key(self, identifier: str) -> str:
        """Get Redis key for identifier."""
        return f"{self._config.key_prefix}{identifier}"
    
    async def check(
        self,
        identifier: str,
        limit: Optional[int] = None,
        window: Optional[int] = None
    ) -> bool:
        """
        Check if request is allowed and record it.
        
        Args:
            identifier: Unique identifier (e.g., user_id, IP)
            limit: Optional limit override
            window: Optional window override
        
        Returns:
            True if allowed, False if rate limited
        """
        limit = limit or self._config.requests
        window = window or self._config.window_seconds
        
        key = self._get_key(identifier)
        now = time.time()
        window_start = now - window
        
        pipe = self._redis.pipeline(transaction=True)
        
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        
        # Count current entries
        pipe.zcard(key)
        
        # Add new entry
        pipe.zadd(key, {str(now): now})
        
        # Set expiry
        pipe.expire(key, window + 1)
        
        results = await pipe.execute()
        current_count = results[1]
        
        return current_count < limit
    
    async def get_remaining(
        self,
        identifier: str,
        limit: Optional[int] = None,
        window: Optional[int] = None
    ) -> int:
        """Get remaining requests in window."""
        limit = limit or self._config.requests
        window = window or self._config.window_seconds
        
        key = self._get_key(identifier)
        now = time.time()
        window_start = now - window
        
        # Clean and count
        await self._redis.zremrangebyscore(key, 0, window_start)
        count = await self._redis.zcard(key)
        
        return max(0, limit - count)
    
    async def reset(self, identifier: str):
        """Reset rate limit for identifier."""
        key = self._get_key(identifier)
        await self._redis.delete(key)


# Module-level limiter
_rate_limiter: Optional[RateLimiter] = None
_is_fake_redis: bool = False


def init_rate_limiter(
    redis_client,
    config: Optional[RateLimitConfig] = None,
    is_fake: bool = False,
):
    """
    Initialize the rate limiter.
    
    Args:
        redis_client: Async Redis client
        config: Rate limit configuration
        is_fake: Whether using fakeredis
    """
    global _rate_limiter, _is_fake_redis
    _rate_limiter = RateLimiter(redis_client, config)
    _is_fake_redis = is_fake


def get_rate_limiter() -> RateLimiter:
    """Get the initialized rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        raise RuntimeError("Rate limiter not initialized. Call init_rate_limiter() first.")
    return _rate_limiter


def is_fake_redis() -> bool:
    """Check if using fakeredis (in-memory)."""
    return _is_fake_redis


def rate_limit(
    requests: int = 100,
    window: int = 60,
    key_func: Optional[Callable[[Request, Optional[UserIdentity]], str]] = None
) -> Callable:
    """
    Create a rate limiting dependency.
    
    Args:
        requests: Max requests per window
        window: Window size in seconds
        key_func: Optional function to generate rate limit key.
                  Default uses user_id or IP address.
    
    Usage:
        @app.post("/api/action")
        async def action(_: None = Depends(rate_limit(10, 60))):
            ...
    """
    async def dependency(
        request: Request,
        user: Optional[UserIdentity] = Depends(get_current_user_optional)
    ):
        limiter = get_rate_limiter()
        
        # Determine key
        if key_func:
            key = key_func(request, user)
        elif user:
            key = f"user:{user.id}"
        else:
            key = f"ip:{request.client.host if request.client else 'unknown'}"
        
        # Check rate limit
        allowed = await limiter.check(key, requests, window)
        
        if not allowed:
            remaining = await limiter.get_remaining(key, requests, window)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Limit": str(requests),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(window)
                }
            )
    
    return dependency