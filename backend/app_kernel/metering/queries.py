"""Metering queries - read from kernel_usage_summary."""

from datetime import datetime, timezone
from typing import Optional, Dict


def _get_period_key(period: str = "month") -> str:
    """Get period key for aggregation."""
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.strftime("%Y-%m-%d")
    elif period == "month":
        return now.strftime("%Y-%m")
    elif period == "year":
        return now.strftime("%Y")
    else:
        return period


async def get_usage(
    db,
    app: str = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    period: str = "month",
) -> Dict[str, int]:
    """
    Get usage summary.
    
    Args:
        app: Ignored (kept for API compat, each app has own DB)
    Returns dict of metric -> value.
    """
    period_key = _get_period_key(period) if period in ("day", "month", "year") else period
    
    where = "[period] = ?"
    params = [period_key]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    
    if user_id:
        where += " AND [user_id] = ?"
        params.append(user_id)
    
    results = await db.find_entities(
        "kernel_usage_summary",
        where_clause=where,
        params=tuple(params),
    )
    
    usage = {}
    for row in results:
        metric = row.get("metric")
        value = row.get("value", 0)
        if metric and not metric.startswith("endpoint:"):
            usage[metric] = value
    
    return usage


async def get_usage_by_endpoint(
    db,
    app: str = None,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    period: str = "month",
) -> Dict[str, int]:
    """Get usage broken down by endpoint."""
    period_key = _get_period_key(period) if period in ("day", "month", "year") else period
    
    where = "[period] = ? AND [metric] LIKE 'endpoint:%'"
    params = [period_key]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    
    if user_id:
        where += " AND [user_id] = ?"
        params.append(user_id)
    
    results = await db.find_entities(
        "kernel_usage_summary",
        where_clause=where,
        params=tuple(params),
    )
    
    endpoints = {}
    for row in results:
        metric = row.get("metric", "")
        if metric.startswith("endpoint:"):
            parts = metric.split(":", 2)
            if len(parts) == 3:
                key = f"{parts[1]} {parts[2]}"
                endpoints[key] = row.get("value", 0)
    
    return endpoints


async def check_quota(
    db,
    app: str = None,
    workspace_id: str = None,
    metric: str = None,
    limit: int = 0,
    period: str = "month",
    user_id: Optional[str] = None,
) -> bool:
    """Check if workspace/user is within quota. Returns True if within quota."""
    usage = await get_usage(db, workspace_id=workspace_id, user_id=user_id, period=period)
    current = usage.get(metric, 0)
    return current < limit


async def get_quota_remaining(
    db,
    app: str = None,
    workspace_id: str = None,
    metric: str = None,
    limit: int = 0,
    period: str = "month",
    user_id: Optional[str] = None,
) -> int:
    """Get remaining quota for a metric."""
    usage = await get_usage(db, workspace_id=workspace_id, user_id=user_id, period=period)
    current = usage.get(metric, 0)
    return max(0, limit - current)
