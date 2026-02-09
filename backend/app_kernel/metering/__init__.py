"""
Usage Metering - Track API calls, quotas, billing.

Writes to database via Redis (async, no runtime penalty).

Usage:
    # Auto-tracked via middleware (every request) - pushed to Redis
    # No setup needed - enabled by default
    
    # Manual tracking for custom metrics (AI tokens, etc)
    await track_usage(redis, app="my-api",
        user_id=user.id,
        workspace_id=workspace_id,
        tokens=1500,
    )
    
    # Query usage
    usage = await get_usage(db, app="my-api", period="2025-01")
    # {"requests": 4521, "tokens": 125000, ...}
    
    # Check quota
    if not await check_quota(db, app="my-api", workspace_id=ws, metric="tokens", limit=100000):
        raise HTTPException(402, "Token limit reached")
    
    # Auto-mounted routes:
    #   GET /api/v1/usage                - Get usage for current user
    #   GET /api/v1/usage/workspace/{id} - Get workspace usage
    #   GET /api/v1/usage/endpoints      - Get usage by endpoint
    #   GET /api/v1/usage/quota/{metric} - Check quota status
"""

from .publisher import track_request, track_usage
from .queries import get_usage, get_usage_by_endpoint, check_quota, get_quota_remaining
from .middleware import UsageMeteringMiddleware
from .router import create_metering_router

__all__ = [
    # Publishers (write to Redis)
    "track_request",
    "track_usage",
    # Queries (read from DB)
    "get_usage",
    "get_usage_by_endpoint",
    "check_quota",
    "get_quota_remaining",
    # Middleware
    "UsageMeteringMiddleware",
    # Router
    "create_metering_router",
]
