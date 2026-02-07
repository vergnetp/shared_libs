"""
Idempotency - Prevent duplicate execution of dangerous operations.

Usage:
    from app_kernel import idempotent
    
    @router.post("/payments")
    @idempotent
    async def create_payment(req: PaymentRequest, user = Depends(get_current_user)):
        # Executes ONCE per unique (user + endpoint + body)
        # Duplicates return cached response
        return await process_payment(req)

How it works:
    Request 1: POST /payments {amount: 100}
      → Executes handler
      → Caches response in Redis (key = user + endpoint + body_hash)
      → Returns {status: 'success', paid_at: '18:04'}
    
    Request 2: POST /payments {amount: 100}  (same user, same body)
      → Finds cached response
      → Returns {status: 'success', paid_at: '18:04'} (from cache)
      → Handler NOT called, payment NOT processed again
      → Header: X-Idempotency-Replayed: true
"""
import hashlib
import json
import functools
from typing import Optional, Any
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import Response, JSONResponse


# =============================================================================
# Configuration
# =============================================================================

ONE_YEAR = 365 * 24 * 60 * 60  # 31536000 seconds

@dataclass 
class IdempotencyConfig:
    """Configuration for idempotency."""
    default_ttl: int = ONE_YEAR
    key_prefix: str = "idempotent:"


# =============================================================================
# Module state
# =============================================================================

_redis_client = None
_config = IdempotencyConfig()


def init_idempotency(redis_client, config: Optional[IdempotencyConfig] = None):
    """Initialize idempotency with Redis client."""
    global _redis_client, _config
    _redis_client = redis_client
    if config:
        _config = config


def get_redis():
    """Get Redis client (may be None if not configured)."""
    return _redis_client


# =============================================================================
# Decorator
# =============================================================================

def idempotent(func=None, *, ttl: int = ONE_YEAR):
    """
    Decorator to make an endpoint idempotent.
    
    Args:
        ttl: Cache TTL in seconds. Default 1 year.
    
    Usage:
        @idempotent
        async def handler(): ...
        
        @idempotent(ttl=300)  # 5 minutes
        async def handler(): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            redis = get_redis()
            if not redis:
                # No Redis configured, just execute
                return await fn(*args, **kwargs)
            
            # Extract request from args/kwargs
            request = _find_request(args, kwargs)
            if not request:
                # No request found, just execute
                return await fn(*args, **kwargs)
            
            # Build key
            key = await _build_key(request)
            
            # Check cache
            cached = await _get_cached(redis, key)
            if cached is not None:
                return _make_cached_response(cached, key)
            
            # Execute handler
            result = await fn(*args, **kwargs)
            
            # Cache successful result
            await _cache_result(redis, key, result, ttl)
            
            return result
        
        wrapper._idempotent = True
        return wrapper
    
    # Handle @idempotent vs @idempotent()
    if func is not None:
        return decorator(func)
    return decorator


# =============================================================================
# Helpers
# =============================================================================

def _find_request(args, kwargs) -> Optional[Request]:
    """Find Request object in function arguments."""
    # Check kwargs
    if 'request' in kwargs:
        return kwargs['request']
    
    # Check args
    for arg in args:
        if isinstance(arg, Request):
            return arg
    
    return None


async def _build_key(request: Request) -> str:
    """Build idempotency key from request."""
    # User ID (from auth middleware)
    user_id = "anon"
    if hasattr(request, 'state'):
        if hasattr(request.state, 'user_id'):
            user_id = request.state.user_id
        elif hasattr(request.state, 'user') and hasattr(request.state.user, 'id'):
            user_id = request.state.user.id
    
    # Method + path
    method = request.method
    path = request.url.path
    
    # Body hash
    body = await request.body()
    body_hash = hashlib.sha256(body).hexdigest()[:16] if body else "empty"
    
    return f"{_config.key_prefix}{user_id}:{method}:{path}:{body_hash}"


async def _get_cached(redis, key: str) -> Optional[dict]:
    """Get cached response from Redis."""
    try:
        data = await redis.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception:
        return None


async def _cache_result(redis, key: str, result: Any, ttl: int):
    """Cache the result in Redis."""
    try:
        # Handle different result types
        if isinstance(result, Response):
            # Read body from response
            if hasattr(result, 'body'):
                body = result.body
            else:
                body = b""
                async for chunk in result.body_iterator:
                    body += chunk
            
            try:
                body_content = json.loads(body.decode()) if body else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_content = body.decode() if body else None
            
            cache_data = {
                "status_code": result.status_code,
                "body": body_content,
            }
        elif isinstance(result, dict):
            cache_data = {
                "status_code": 200,
                "body": result,
            }
        else:
            # Try to serialize
            cache_data = {
                "status_code": 200,
                "body": result,
            }
        
        await redis.setex(key, ttl, json.dumps(cache_data))
    except Exception:
        pass  # Caching failure shouldn't break the request


def _make_cached_response(cached: dict, key: str) -> JSONResponse:
    """Create response from cached data."""
    response = JSONResponse(
        content=cached.get("body"),
        status_code=cached.get("status_code", 200),
    )
    response.headers["X-Idempotency-Replayed"] = "true"
    return response


# =============================================================================
# Manual checker for complex cases
# =============================================================================

class IdempotencyChecker:
    """
    Manual idempotency checker for complex cases where decorator doesn't fit.
    
    Usage:
        checker = IdempotencyChecker(redis)
        
        is_new, cached = await checker.check(f"payment:{payment_id}")
        if not is_new:
            return cached
        
        result = await process_payment(...)
        await checker.set(f"payment:{payment_id}", result)
        return result
    """
    
    def __init__(self, redis_client, ttl: int = ONE_YEAR):
        self._redis = redis_client
        self._ttl = ttl
        self._prefix = "idempotent:"
    
    async def check(self, key: str) -> tuple[bool, Optional[Any]]:
        """
        Check if operation was already performed.
        
        Returns:
            (is_new, cached_result)
        """
        full_key = f"{self._prefix}{key}"
        try:
            existing = await self._redis.get(full_key)
            if existing:
                return (False, json.loads(existing))
            return (True, None)
        except Exception:
            return (True, None)
    
    async def set(self, key: str, result: Any, ttl: Optional[int] = None):
        """Store result for idempotency key."""
        full_key = f"{self._prefix}{key}"
        ttl = ttl or self._ttl
        try:
            await self._redis.setex(full_key, ttl, json.dumps(result))
        except Exception:
            pass
