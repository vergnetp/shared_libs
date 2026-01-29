"""
Usage Metering - Track API calls, quotas, billing.

Usage:
    # Automatic tracking via middleware (every request)
    app.add_middleware(UsageMeteringMiddleware, get_db_connection=get_db_connection)
    
    # Manual tracking for custom metrics
    await track_usage(db, user_id, workspace_id,
        tokens=1500,        # AI tokens
        deployments=1,      # Custom counter
    )
    
    # Query usage
    usage = await get_usage(db, workspace_id, period="2025-01")
    # {"requests": 4521, "tokens": 125000, ...}
    
    # Check quota
    if not await check_quota(db, workspace_id, "tokens", limit=100000, period="month"):
        raise HTTPException(402, "Token limit reached")
"""

from .stores import (
    track_request,
    track_usage,
    get_usage,
    get_usage_by_endpoint,
    check_quota,
    init_metering_schema,
)
from .middleware import UsageMeteringMiddleware
from .router import create_metering_router

__all__ = [
    # Stores
    "track_request",
    "track_usage",
    "get_usage",
    "get_usage_by_endpoint",
    "check_quota",
    "init_metering_schema",
    # Middleware
    "UsageMeteringMiddleware",
    # Router
    "create_metering_router",
]
