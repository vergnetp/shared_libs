"""
app_kernel.reliability - Reliability middleware.

This module provides:
- Rate limiting middleware with tiers (anonymous/authenticated/admin)
- @rate_limit(n) decorator for per-route limits
- @no_rate_limit decorator to exclude routes
- Idempotency (request deduplication)

Usage:
    from app_kernel.reliability import rate_limit, no_rate_limit
    
    @router.post("/expensive")
    @rate_limit(5)  # 5 per minute
    async def expensive():
        ...
    
    @router.get("/health")
    @no_rate_limit
    async def health():
        ...
"""

from .ratelimit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    init_rate_limiter,
    get_rate_limiter,
    is_fake_redis,
    rate_limit,
    no_rate_limit,
)

from .idempotency import (
    IdempotencyConfig,
    IdempotencyMiddleware,
    IdempotencyChecker,
    init_idempotency_checker,
    get_idempotency_checker,
)

__all__ = [
    # Rate limiting
    "RateLimitConfig",
    "RateLimiter",
    "RateLimitMiddleware",
    "init_rate_limiter",
    "get_rate_limiter",
    "is_fake_redis",
    "rate_limit",
    "no_rate_limit",
    
    # Idempotency
    "IdempotencyConfig",
    "IdempotencyMiddleware",
    "IdempotencyChecker",
    "init_idempotency_checker",
    "get_idempotency_checker",
]
