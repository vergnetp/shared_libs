"""
app_kernel.reliability - Reliability middleware.

This module provides:
- Rate limiting (Redis-backed sliding window)
- Idempotency (request deduplication)

Usage:
    from app_kernel.reliability import rate_limit, IdempotencyMiddleware
    
    # Rate limiting
    @app.post("/api/action")
    async def action(_: None = Depends(rate_limit(10, 60))):
        ...
    
    # Idempotency middleware
    app.add_middleware(IdempotencyMiddleware, redis_config=redis_config)
"""

from .ratelimit import (
    RateLimitConfig,
    RateLimiter,
    init_rate_limiter,
    get_rate_limiter,
    rate_limit,
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
    "init_rate_limiter",
    "get_rate_limiter",
    "rate_limit",
    
    # Idempotency
    "IdempotencyConfig",
    "IdempotencyMiddleware",
    "IdempotencyChecker",
    "init_idempotency_checker",
    "get_idempotency_checker",
]
