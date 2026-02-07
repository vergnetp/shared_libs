"""
Idempotency middleware.

Provides idempotency key support for POST/PUT/PATCH requests.
Explicitly excludes streaming routes.

Usage:
    from app_kernel.reliability import IdempotencyMiddleware
    
    # Add to app
    app.add_middleware(
        IdempotencyMiddleware,
        redis_client=redis_client,  # async redis or fakeredis.aioredis
        exclude_paths=["/stream", "/chat/stream"]
    )
    
    # Client sends Idempotency-Key header
    # POST /api/payment
    # Idempotency-Key: unique-request-id-123
"""
from typing import Optional, Set, Any
from dataclasses import dataclass
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


@dataclass
class IdempotencyConfig:
    """Configuration for idempotency."""
    ttl_seconds: int = 86400  # 24 hours
    key_prefix: str = "idempotency:"
    header_name: str = "Idempotency-Key"
    
    # Paths to exclude (streaming routes should be excluded)
    exclude_paths: Set[str] = None
    
    # Methods that support idempotency
    methods: Set[str] = None
    
    def __post_init__(self):
        if self.exclude_paths is None:
            self.exclude_paths = set()
        if self.methods is None:
            self.methods = {"POST", "PUT", "PATCH"}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces idempotency for non-GET requests.
    
    If a request with the same idempotency key has been processed,
    returns the cached response instead of processing again.
    """
    
    def __init__(
        self,
        app,
        redis_client,
        config: Optional[IdempotencyConfig] = None,
        exclude_paths: Optional[Set[str]] = None
    ):
        """
        Initialize middleware.
        
        Args:
            app: ASGI application
            redis_client: Async Redis client (redis.asyncio or fakeredis.aioredis)
            config: Idempotency configuration
            exclude_paths: Additional paths to exclude
        """
        super().__init__(app)
        self._redis = redis_client
        self._config = config or IdempotencyConfig()
        
        if exclude_paths:
            self._config.exclude_paths.update(exclude_paths)
    
    def _should_process(self, request: Request) -> bool:
        """Check if request should be processed for idempotency."""
        # Check method
        if request.method not in self._config.methods:
            return False
        
        # Check excluded paths
        path = request.url.path
        for excluded in self._config.exclude_paths:
            if path.startswith(excluded):
                return False
        
        # Check for idempotency key header
        return self._config.header_name in request.headers
    
    def _get_key(self, request: Request) -> str:
        """Get Redis key for the idempotency key."""
        idempotency_key = request.headers.get(self._config.header_name)
        
        # Include user info if available (from request state)
        user_id = getattr(request.state, 'user_id', None) if hasattr(request, 'state') else None
        
        if user_id:
            key = f"{user_id}:{idempotency_key}"
        else:
            key = idempotency_key
        
        return f"{self._config.key_prefix}{key}"
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with idempotency check."""
        # Skip if not applicable
        if not self._should_process(request):
            return await call_next(request)
        
        redis_key = self._get_key(request)
        
        # Check for cached response
        cached = await self._get_cached_response(redis_key)
        
        if cached:
            # Return cached response
            return JSONResponse(
                content=cached.get("body"),
                status_code=cached.get("status_code", 200),
                headers={
                    "X-Idempotency-Replayed": "true",
                    **cached.get("headers", {})
                }
            )
        
        # Process request
        response = await call_next(request)
        
        # Cache successful responses
        if 200 <= response.status_code < 300:
            await self._cache_response(redis_key, response)
        
        return response
    
    async def _get_cached_response(self, key: str) -> Optional[dict]:
        """Get cached response from Redis."""
        try:
            data = await self._redis.get(key)
            
            if data:
                return json.loads(data)
            return None
            
        except Exception:
            # On error, proceed without idempotency
            return None
    
    async def _cache_response(self, key: str, response: Response):
        """Cache the response in Redis."""
        try:
            # Read response body
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            
            # Cache the response data
            cache_data = {
                "status_code": response.status_code,
                "body": json.loads(body.decode()) if body else None,
                "headers": dict(response.headers)
            }
            
            await self._redis.setex(
                key,
                self._config.ttl_seconds,
                json.dumps(cache_data)
            )
            
            # Create new response with the body
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type
            )
            
        except Exception:
            # On error, just return original response
            pass


# Simpler functional approach for checking idempotency
class IdempotencyChecker:
    """
    Functional idempotency checker.
    
    Use this for explicit idempotency control in handlers.
    """
    
    def __init__(self, redis_client, config: Optional[IdempotencyConfig] = None):
        self._redis = redis_client
        self._config = config or IdempotencyConfig()
    
    async def check_and_set(
        self,
        key: str,
        ttl: Optional[int] = None
    ) -> tuple[bool, Optional[Any]]:
        """
        Check if key exists and set if not.
        
        Returns:
            (is_new, cached_result) - is_new is True if first request
        """
        ttl = ttl or self._config.ttl_seconds
        full_key = f"{self._config.key_prefix}{key}"
        
        # Try to get existing
        existing = await self._redis.get(full_key)
        if existing:
            return (False, json.loads(existing))
        
        # Set placeholder
        await self._redis.setex(full_key, ttl, json.dumps({"status": "processing"}))
        return (True, None)
    
    async def set_result(self, key: str, result: Any, ttl: Optional[int] = None):
        """Store the result for an idempotency key."""
        ttl = ttl or self._config.ttl_seconds
        full_key = f"{self._config.key_prefix}{key}"
        await self._redis.setex(full_key, ttl, json.dumps(result))


# Module-level checker
_idempotency_checker: Optional[IdempotencyChecker] = None


def init_idempotency_checker(redis_client, config: Optional[IdempotencyConfig] = None):
    """Initialize idempotency checker."""
    global _idempotency_checker
    _idempotency_checker = IdempotencyChecker(redis_client, config)


def get_idempotency_checker() -> IdempotencyChecker:
    """Get the initialized idempotency checker."""
    if _idempotency_checker is None:
        raise RuntimeError("Idempotency checker not initialized.")
    return _idempotency_checker
