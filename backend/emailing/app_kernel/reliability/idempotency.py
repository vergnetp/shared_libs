"""
Idempotency middleware.

Provides idempotency key support for POST/PUT/PATCH requests.
Explicitly excludes streaming routes.

Usage:
    from app_kernel.reliability import IdempotencyMiddleware
    
    # Add to app
    app.add_middleware(
        IdempotencyMiddleware,
        redis_config=redis_config,
        exclude_paths=["/stream", "/chat/stream"]
    )
    
    # Client sends Idempotency-Key header
    # POST /api/payment
    # Idempotency-Key: unique-request-id-123
"""
from typing import Optional, Set, Callable, Any
from dataclasses import dataclass
import json
import hashlib
import asyncio

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
        redis_config,
        config: Optional[IdempotencyConfig] = None,
        exclude_paths: Optional[Set[str]] = None
    ):
        """
        Initialize middleware.
        
        Args:
            app: ASGI application
            redis_config: Redis configuration with get_client()
            config: Idempotency configuration
            exclude_paths: Additional paths to exclude
        """
        super().__init__(app)
        self._redis_config = redis_config
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
        
        # Check for cached response (sync operation)
        cached = await asyncio.to_thread(self._get_cached_response, redis_key)
        
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
    
    def _get_cached_response(self, key: str) -> Optional[dict]:
        """Get cached response from Redis."""
        try:
            redis = self._redis_config.get_client()
            data = redis.get(key)
            
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
            
            # Rebuild response with new body iterator
            # (since we consumed it)
            
            # Cache the response data
            cache_data = {
                "status_code": response.status_code,
                "body": json.loads(body.decode()) if body else None,
                "headers": dict(response.headers)
            }
            
            await asyncio.to_thread(
                self._store_cached_response,
                key,
                cache_data
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
    
    def _store_cached_response(self, key: str, data: dict):
        """Store response in Redis."""
        try:
            redis = self._redis_config.get_client()
            redis.setex(
                key,
                self._config.ttl_seconds,
                json.dumps(data)
            )
        except Exception:
            pass


# Simpler functional approach for checking idempotency
class IdempotencyChecker:
    """
    Functional idempotency checker.
    
    Use this for explicit idempotency control in handlers.
    """
    
    def __init__(self, redis_config, config: Optional[IdempotencyConfig] = None):
        self._redis_config = redis_config
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
        
        def _check():
            redis = self._redis_config.get_client()
            
            # Try to get existing
            existing = redis.get(full_key)
            if existing:
                return (False, json.loads(existing))
            
            # Set placeholder
            redis.setex(full_key, ttl, json.dumps({"status": "processing"}))
            return (True, None)
        
        return await asyncio.to_thread(_check)
    
    async def set_result(self, key: str, result: Any, ttl: Optional[int] = None):
        """Store the result for an idempotency key."""
        ttl = ttl or self._config.ttl_seconds
        full_key = f"{self._config.key_prefix}{key}"
        
        def _set():
            redis = self._redis_config.get_client()
            redis.setex(full_key, ttl, json.dumps(result))
        
        await asyncio.to_thread(_set)


# Module-level checker
_idempotency_checker: Optional[IdempotencyChecker] = None


def init_idempotency_checker(redis_config, config: Optional[IdempotencyConfig] = None):
    """Initialize idempotency checker. Called by init_app_kernel()."""
    global _idempotency_checker
    _idempotency_checker = IdempotencyChecker(redis_config, config)


def get_idempotency_checker() -> IdempotencyChecker:
    """Get the initialized idempotency checker."""
    if _idempotency_checker is None:
        raise RuntimeError("Idempotency checker not initialized.")
    return _idempotency_checker
