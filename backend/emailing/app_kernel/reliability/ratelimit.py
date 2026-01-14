"""
Rate limiting middleware.

Provides Redis-backed rate limiting for API endpoints.

Usage:
    from app_kernel.reliability import RateLimiter, rate_limit
    
    # Use as dependency
    @app.post("/api/action")
    async def action(
        user: UserIdentity = Depends(get_current_user),
        _: None = Depends(rate_limit(requests=10, window=60))
    ):
        ...
    
    # Or use limiter directly
    limiter = get_rate_limiter()
    allowed = await limiter.check("user:123", limit=10, window=60)
"""
from typing import Optional, Callable
from dataclasses import dataclass
import time
import asyncio

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
    Redis-backed sliding window rate limiter.
    
    Uses a sorted set to track request timestamps.
    """
    
    def __init__(self, redis_config, config: Optional[RateLimitConfig] = None):
        """
        Initialize rate limiter.
        
        Args:
            redis_config: Redis configuration with get_client()
            config: Rate limit configuration
        """
        self._redis_config = redis_config
        self._config = config or RateLimitConfig()
    
    def _get_key(self, identifier: str) -> str:
        """Get Redis key for identifier."""
        return f"{self._config.key_prefix}{identifier}"
    
    def check(
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
        
        redis = self._redis_config.get_client()
        key = self._get_key(identifier)
        now = time.time()
        window_start = now - window
        
        pipe = redis.pipeline(transaction=True)
        try:
            # Remove old entries
            pipe.zremrangebyscore(key, 0, window_start)
            
            # Count current entries
            pipe.zcard(key)
            
            # Add new entry
            pipe.zadd(key, {str(now): now})
            
            # Set expiry
            pipe.expire(key, window + 1)
            
            results = pipe.execute()
            current_count = results[1]
            
            return current_count < limit
            
        finally:
            pipe.reset()
    
    def get_remaining(
        self,
        identifier: str,
        limit: Optional[int] = None,
        window: Optional[int] = None
    ) -> int:
        """Get remaining requests in window."""
        limit = limit or self._config.requests
        window = window or self._config.window_seconds
        
        redis = self._redis_config.get_client()
        key = self._get_key(identifier)
        now = time.time()
        window_start = now - window
        
        # Clean and count
        redis.zremrangebyscore(key, 0, window_start)
        count = redis.zcard(key)
        
        return max(0, limit - count)
    
    def reset(self, identifier: str):
        """Reset rate limit for identifier."""
        redis = self._redis_config.get_client()
        key = self._get_key(identifier)
        redis.delete(key)


# Module-level limiter
_rate_limiter: Optional[RateLimiter] = None


def init_rate_limiter(redis_config, config: Optional[RateLimitConfig] = None):
    """Initialize the rate limiter. Called by init_app_kernel()."""
    global _rate_limiter
    _rate_limiter = RateLimiter(redis_config, config)


def get_rate_limiter() -> RateLimiter:
    """Get the initialized rate limiter."""
    if _rate_limiter is None:
        raise RuntimeError("Rate limiter not initialized. Call init_app_kernel() first.")
    return _rate_limiter


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
        
        # Check rate limit (sync, run in thread)
        allowed = await asyncio.to_thread(
            limiter.check, key, requests, window
        )
        
        if not allowed:
            remaining = await asyncio.to_thread(
                limiter.get_remaining, key, requests, window
            )
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
