"""Usage metering API routes."""

from typing import Dict, List, Optional, Callable
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel


class UsageResponse(BaseModel):
    period: str
    workspace_id: Optional[str]
    metrics: Dict[str, int]


class UsageByEndpointResponse(BaseModel):
    period: str
    workspace_id: Optional[str]
    endpoints: Dict[str, int]


class QuotaResponse(BaseModel):
    metric: str
    limit: int
    used: int
    remaining: int
    within_quota: bool


def create_metering_router(
    get_current_user: Callable,
    get_db_connection: Callable,
    prefix: str = "/usage",
    tags: List[str] = None,
    require_admin_for_all: bool = False,
    is_admin: Optional[Callable] = None,
) -> APIRouter:
    """
    Create usage metering router.
    
    Endpoints:
        GET /usage                - Get usage summary for current user
        GET /usage/workspace/{id} - Get workspace usage (admin or member)
        GET /usage/endpoints      - Get usage by endpoint
        GET /usage/quota          - Check quota status
    """
    router = APIRouter(prefix=prefix, tags=tags or ["usage"])
    
    def _check_admin(user):
        if is_admin:
            return is_admin(user)
        role = getattr(user, "role", None)
        return role == "admin"
    
    @router.get("", response_model=UsageResponse)
    async def get_my_usage(
        period: str = Query("month", description="Period: 'day', 'month', 'year', or specific like '2025-01'"),
        user = Depends(get_current_user),
    ):
        """Get usage summary for current user."""
        from .stores import get_usage, _get_period_key
        
        async with get_db_connection() as db:
            metrics = await get_usage(
                db,
                user_id=user.id,
                period=period,
            )
        
        return UsageResponse(
            period=_get_period_key(period) if period in ("day", "month", "year") else period,
            workspace_id=getattr(user, "workspace_id", None),
            metrics=metrics,
        )
    
    @router.get("/workspace/{workspace_id}", response_model=UsageResponse)
    async def get_workspace_usage(
        workspace_id: str,
        period: str = Query("month"),
        user = Depends(get_current_user),
    ):
        """Get usage summary for a workspace."""
        from .stores import get_usage, _get_period_key
        
        # Check access (admin or workspace member)
        user_workspace = getattr(user, "workspace_id", None)
        if user_workspace != workspace_id and not _check_admin(user):
            raise HTTPException(403, "Access denied")
        
        async with get_db_connection() as db:
            metrics = await get_usage(
                db,
                workspace_id=workspace_id,
                period=period,
            )
        
        return UsageResponse(
            period=_get_period_key(period) if period in ("day", "month", "year") else period,
            workspace_id=workspace_id,
            metrics=metrics,
        )
    
    @router.get("/endpoints", response_model=UsageByEndpointResponse)
    async def get_endpoint_usage(
        period: str = Query("month"),
        workspace_id: Optional[str] = None,
        user = Depends(get_current_user),
    ):
        """Get usage breakdown by endpoint."""
        from .stores import get_usage_by_endpoint, _get_period_key
        
        # Use user's workspace if not specified
        ws_id = workspace_id or getattr(user, "workspace_id", None)
        
        # Check access
        if workspace_id and workspace_id != getattr(user, "workspace_id", None):
            if not _check_admin(user):
                raise HTTPException(403, "Access denied")
        
        async with get_db_connection() as db:
            endpoints = await get_usage_by_endpoint(
                db,
                workspace_id=ws_id,
                period=period,
            )
        
        return UsageByEndpointResponse(
            period=_get_period_key(period) if period in ("day", "month", "year") else period,
            workspace_id=ws_id,
            endpoints=endpoints,
        )
    
    @router.get("/quota/{metric}", response_model=QuotaResponse)
    async def check_quota_status(
        metric: str,
        limit: int = Query(..., description="Quota limit to check against"),
        period: str = Query("month"),
        workspace_id: Optional[str] = None,
        user = Depends(get_current_user),
    ):
        """Check quota status for a specific metric."""
        from .stores import get_usage
        
        ws_id = workspace_id or getattr(user, "workspace_id", None)
        
        async with get_db_connection() as db:
            usage = await get_usage(db, workspace_id=ws_id, period=period)
        
        used = usage.get(metric, 0)
        remaining = max(0, limit - used)
        
        return QuotaResponse(
            metric=metric,
            limit=limit,
            used=used,
            remaining=remaining,
            within_quota=used < limit,
        )
    
    return router
