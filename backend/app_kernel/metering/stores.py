"""Usage metering storage and queries."""

from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        # Assume it's already a period key like "2025-01"
        return period


def _get_period_start(period_key: str) -> datetime:
    """Get start datetime for a period key."""
    if len(period_key) == 10:  # "2025-01-28" (day)
        return datetime.fromisoformat(period_key + "T00:00:00+00:00")
    elif len(period_key) == 7:  # "2025-01" (month)
        return datetime.fromisoformat(period_key + "-01T00:00:00+00:00")
    elif len(period_key) == 4:  # "2025" (year)
        return datetime.fromisoformat(period_key + "-01-01T00:00:00+00:00")
    return datetime.now(timezone.utc)


async def init_metering_schema(db) -> None:
    """Create metering tables."""
    # Individual request log (optional, can be expensive)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS usage_requests (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            workspace_id TEXT,
            endpoint TEXT,
            method TEXT,
            status_code INTEGER,
            latency_ms INTEGER,
            bytes_in INTEGER,
            bytes_out INTEGER,
            timestamp TEXT,
            created_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_requests_workspace ON usage_requests(workspace_id, timestamp)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_requests_user ON usage_requests(user_id, timestamp)")
    
    # Aggregated usage (main table for billing)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS usage_summary (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            user_id TEXT,
            period TEXT,
            metric TEXT,
            value INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(workspace_id, user_id, period, metric)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_summary_lookup ON usage_summary(workspace_id, period)")


async def track_request(
    db,
    user_id: Optional[str],
    workspace_id: Optional[str],
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: int,
    bytes_in: int = 0,
    bytes_out: int = 0,
    log_individual: bool = False,
) -> None:
    """
    Track an API request.
    
    Args:
        log_individual: If True, also logs to usage_requests table (expensive for high volume)
    """
    import uuid
    
    now = _now_iso()
    period = _get_period_key("month")
    
    # Log individual request (optional)
    if log_individual:
        await db.save_entity("usage_requests", {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "workspace_id": workspace_id,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            "timestamp": now,
            "created_at": now,
        })
    
    # Update aggregated summary
    await _increment_metric(db, workspace_id, user_id, period, "requests", 1)
    await _increment_metric(db, workspace_id, user_id, period, "latency_ms_total", latency_ms)
    await _increment_metric(db, workspace_id, user_id, period, "bytes_in", bytes_in)
    await _increment_metric(db, workspace_id, user_id, period, "bytes_out", bytes_out)
    
    # Track by endpoint
    endpoint_key = f"endpoint:{method}:{endpoint}"
    await _increment_metric(db, workspace_id, user_id, period, endpoint_key, 1)


async def track_usage(
    db,
    user_id: str,
    workspace_id: Optional[str] = None,
    period: str = "month",
    **metrics: int,
) -> None:
    """
    Track custom usage metrics.
    
    Example:
        await track_usage(db, user_id, workspace_id,
            tokens=1500,
            deployments=1,
            ai_calls=1,
        )
    """
    period_key = _get_period_key(period)
    
    for metric, value in metrics.items():
        if value:
            await _increment_metric(db, workspace_id, user_id, period_key, metric, value)


async def _increment_metric(
    db,
    workspace_id: Optional[str],
    user_id: Optional[str],
    period: str,
    metric: str,
    value: int,
) -> None:
    """Increment a metric in the summary table."""
    import uuid
    
    # Try to find existing record
    where = "[period] = ? AND [metric] = ?"
    params = [period, metric]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    else:
        where += " AND [workspace_id] IS NULL"
    
    if user_id:
        where += " AND [user_id] = ?"
        params.append(user_id)
    else:
        where += " AND [user_id] IS NULL"
    
    results = await db.find_entities(
        "usage_summary",
        where_clause=where,
        params=tuple(params),
        limit=1,
    )
    
    now = _now_iso()
    
    if results:
        # Update existing
        await db.save_entity("usage_summary", {
            "id": results[0]["id"],
            "value": (results[0].get("value") or 0) + value,
            "updated_at": now,
        })
    else:
        # Create new
        await db.save_entity("usage_summary", {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "period": period,
            "metric": metric,
            "value": value,
            "created_at": now,
            "updated_at": now,
        })


async def get_usage(
    db,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    period: str = "month",
) -> Dict[str, int]:
    """
    Get usage summary for a workspace/user.
    
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
        "usage_summary",
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
        "usage_summary",
        where_clause=where,
        params=tuple(params),
    )
    
    endpoints = {}
    for row in results:
        metric = row.get("metric", "")
        if metric.startswith("endpoint:"):
            # "endpoint:POST:/api/v1/deployments" -> "POST /api/v1/deployments"
            parts = metric.split(":", 2)
            if len(parts) == 3:
                key = f"{parts[1]} {parts[2]}"
                endpoints[key] = row.get("value", 0)
    
    return endpoints


async def check_quota(
    db,
    workspace_id: str,
    metric: str,
    limit: int,
    period: str = "month",
    user_id: Optional[str] = None,
) -> bool:
    """
    Check if workspace/user is within quota for a metric.
    
    Returns True if within quota, False if exceeded.
    """
    usage = await get_usage(db, workspace_id=workspace_id, user_id=user_id, period=period)
    current = usage.get(metric, 0)
    return current < limit


async def get_quota_remaining(
    db,
    workspace_id: str,
    metric: str,
    limit: int,
    period: str = "month",
    user_id: Optional[str] = None,
) -> int:
    """Get remaining quota for a metric."""
    usage = await get_usage(db, workspace_id=workspace_id, user_id=user_id, period=period)
    current = usage.get(metric, 0)
    return max(0, limit - current)
