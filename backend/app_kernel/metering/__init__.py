"""
Usage Metering - Track API calls, quotas, billing.

Writes to shared admin_db via Redis (async, no runtime penalty).

Usage:
    # Auto-tracked via middleware (every request) - pushed to Redis
    app.add_middleware(UsageMeteringMiddleware, ...)
    
    # Manual tracking for custom metrics (AI tokens, etc)
    await track_usage(redis, app="deploy_api",
        user_id=user.id,
        workspace_id=workspace_id,
        tokens=1500,
    )
    
    # Query usage (from admin_db)
    usage = await get_usage(admin_db, app="deploy_api", period="2025-01")
    # {"requests": 4521, "tokens": 125000, ...}
    
    # Check quota
    if not await check_quota(admin_db, app="deploy_api", workspace_id=ws, metric="tokens", limit=100000):
        raise HTTPException(402, "Token limit reached")
"""

from .publisher import track_request, track_usage
from .queries import get_usage, get_usage_by_endpoint, check_quota, get_quota_remaining
from .schema import init_metering_schema
from .middleware import UsageMeteringMiddleware
from .router import create_metering_router

__all__ = [
    # Publishers (write to Redis)
    "track_request",
    "track_usage",
    # Queries (read from admin_db)
    "get_usage",
    "get_usage_by_endpoint",
    "check_quota",
    "get_quota_remaining",
    # Schema
    "init_metering_schema",
    # Middleware
    "UsageMeteringMiddleware",
    # Router
    "create_metering_router",
]
