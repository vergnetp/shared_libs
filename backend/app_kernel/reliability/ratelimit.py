"""
Rate limiting middleware and decorators.

Provides:
- Global rate limiting middleware with tiers (anonymous/authenticated/admin)
- @rate_limit(n) decorator for per-route limits
- @no_rate_limit decorator to exclude routes

Usage:
    # Middleware (auto-applied by kernel)
    # Anonymous: 30/min, Authenticated: 120/min, Admin: 600/min
    
    # Override for specific routes:
    @rate_limit(5)  # 5 per minute
    async def expensive_operation():
        ...
    
    @rate_limit(100, window=3600)  # 100 per hour
    async def send_email():
        ...
    
    @no_rate_limit  # Exclude from rate limiting
    async def health():
        ...
"""
from typing import Optional, Callable, Set
from dataclasses import dataclass, field
import time
import functools

from fastapi import Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..auth.models import UserIdentity
from ..auth.deps import get_current_user_optional


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""
    # Default limits per minute
    anonymous_rpm: int = 30
    authenticated_rpm: int = 120
    admin_rpm: int = 600
    
    # Key prefix
    key_prefix: str = "ratelimit:"
    
    # Paths to exclude from rate limiting
    exclude_paths: Set[str] = field(default_factory=lambda: {
        "/health", "/healthz", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"
    })
    
    # Path prefixes to exclude
    exclude_prefixes: Set[str] = field(default_factory=lambda: {
        "/static", "/_next"
    })


class RateLimiter:
    """
    Async Redis-backed sliding window rate limiter.
    
    Uses a sorted set to track request timestamps.
    Works with real redis.asyncio or fakeredis.aioredis.
    """
    
    def __init__(self, redis_client, config: Optional[RateLimitConfig] = None):
        self._redis = redis_client
        self._config = config or RateLimitConfig()
    
    def _get_key(self, identifier: str) -> str:
        return f"{self._config.key_prefix}{identifier}"
    
    async def check(
        self,
        identifier: str,
        limit: int,
        window: int = 60
    ) -> tuple[bool, int, int]:
        """
        Check if request is allowed and record it.
        
        Returns:
            (allowed, remaining, reset_seconds)
        """
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
        
        remaining = max(0, limit - current_count - 1)
        allowed = current_count < limit
        
        return allowed, remaining, window
    
    async def get_limit_for_request(
        self,
        request: Request,
        user: Optional[UserIdentity] = None
    ) -> tuple[str, int]:
        """
        Get rate limit key and limit for a request.
        
        Returns:
            (key, limit_per_minute)
        """
        if user:
            if getattr(user, 'is_admin', False) or getattr(user, 'role', None) == 'admin':
                return f"admin:{user.id}", self._config.admin_rpm
            else:
                return f"user:{user.id}", self._config.authenticated_rpm
        else:
            # Anonymous - use IP
            client_ip = request.client.host if request.client else "unknown"
            return f"ip:{client_ip}", self._config.anonymous_rpm


# Route-level rate limit storage
_route_limits: dict[str, tuple[int, int]] = {}  # path -> (limit, window)
_no_rate_limit_routes: Set[str] = set()


def rate_limit(limit: int, window: int = 60):
    """
    Decorator to set custom rate limit for a route.
    
    Args:
        limit: Maximum requests allowed
        window: Time window in seconds (default: 60)
    
    Usage:
        @router.post("/expensive")
        @rate_limit(5)  # 5 per minute
        async def expensive():
            ...
        
        @router.post("/hourly")
        @rate_limit(100, window=3600)  # 100 per hour
        async def hourly():
            ...
    """
    def decorator(func):
        # Store the limit for this route
        # We'll look it up by function name in middleware
        _route_limits[func.__name__] = (limit, window)
        
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        
        # Mark the wrapper with rate limit info
        wrapper._rate_limit = (limit, window)
        return wrapper
    
    return decorator


def no_rate_limit(func):
    """
    Decorator to exclude a route from rate limiting.
    
    Usage:
        @router.get("/health")
        @no_rate_limit
        async def health():
            ...
    """
    _no_rate_limit_routes.add(func.__name__)
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)
    
    wrapper._no_rate_limit = True
    return wrapper


def _extract_user_from_token(request: Request) -> Optional[UserIdentity]:
    """
    Lightweight user extraction from Bearer token for rate-limit tiering.
    Runs in middleware (before route handler), so we decode the JWT directly.
    Returns None on any failure — request falls to anonymous tier.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        from ..auth.utils import decode_token
        payload = decode_token(token, RateLimitMiddleware._token_secret)
        if payload.type != "access":
            return None
        return UserIdentity(id=payload.sub, email=payload.email, role=payload.role)
    except Exception:
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Global rate limiting middleware.
    
    Applies tiered rate limits:
    - Anonymous: 30/min (configurable)
    - Authenticated: 120/min (configurable)
    - Admin: 600/min (configurable)
    
    Routes can override with @rate_limit(n) or exclude with @no_rate_limit.
    """
    
    # Cached token secret (set during init_rate_limiter)
    _token_secret: str = ""
    
    def __init__(
        self,
        app,
        redis_client,
        config: Optional[RateLimitConfig] = None,
    ):
        super().__init__(app)
        self._limiter = RateLimiter(redis_client, config)
        self._config = config or RateLimitConfig()
    
    def _should_skip(self, request: Request) -> bool:
        """Check if request should skip rate limiting."""
        path = request.url.path
        
        # Check exact path exclusions
        if path in self._config.exclude_paths:
            return True
        
        # Check prefix exclusions
        for prefix in self._config.exclude_prefixes:
            if path.startswith(prefix):
                return True
        
        # Check if route has @no_rate_limit
        route = request.scope.get("route")
        if route:
            endpoint = getattr(route, "endpoint", None)
            if endpoint:
                if getattr(endpoint, "_no_rate_limit", False):
                    return True
                if endpoint.__name__ in _no_rate_limit_routes:
                    return True
        
        return False
    
    def _get_route_limit(self, request: Request) -> Optional[tuple[int, int]]:
        """Get custom rate limit for route if set via @rate_limit decorator."""
        route = request.scope.get("route")
        if route:
            endpoint = getattr(route, "endpoint", None)
            if endpoint:
                # Check wrapper attribute
                if hasattr(endpoint, "_rate_limit"):
                    return endpoint._rate_limit
                # Check registry
                if endpoint.__name__ in _route_limits:
                    return _route_limits[endpoint.__name__]
        return None
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with rate limiting."""
        # Skip if excluded
        if self._should_skip(request):
            return await call_next(request)
        
        # Extract user identity from Bearer token (lightweight decode, no DB call)
        user = _extract_user_from_token(request)
        
        try:
            # Get rate limit key and default limit
            key, default_limit = await self._limiter.get_limit_for_request(request, user)
            
            # Check for route-specific override
            route_limit = self._get_route_limit(request)
            if route_limit:
                limit, window = route_limit
            else:
                limit, window = default_limit, 60
            
            # Check rate limit
            allowed, remaining, reset = await self._limiter.check(key, limit, window)
        except Exception:
            # Redis unavailable (BusyLoadingError, ConnectionError, etc.)
            # Degrade gracefully: allow the request through
            return await call_next(request)
        
        if not allowed:
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                    "Retry-After": str(reset),
                }
            )
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
        
        return response


# Module-level limiter (for direct use)
_rate_limiter: Optional[RateLimiter] = None
_is_fake_redis: bool = False


def init_rate_limiter(
    redis_client,
    config: Optional[RateLimitConfig] = None,
    is_fake: bool = False,
    token_secret: str = "",
):
    """Initialize the rate limiter."""
    global _rate_limiter, _is_fake_redis
    _rate_limiter = RateLimiter(redis_client, config)
    _is_fake_redis = is_fake
    RateLimitMiddleware._token_secret = token_secret


def get_rate_limiter() -> RateLimiter:
    """Get the initialized rate limiter."""
    if _rate_limiter is None:
        raise RuntimeError("Rate limiter not initialized.")
    return _rate_limiter


def is_fake_redis() -> bool:
    """Check if using fakeredis."""
    return _is_fake_redis
