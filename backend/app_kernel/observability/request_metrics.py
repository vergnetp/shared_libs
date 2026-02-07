"""
Request metrics collection and storage.

Captures rich metadata for every HTTP request and stores it asynchronously
via the job queue (non-blocking to the main request).

Storage:
- DB (hot): Fast queries for dashboard, recent data
- OpenSearch (cold): Full history, analytics, aggregations

Metadata captured:
- Request: method, path, query_params, request_id
- Response: status_code, error details
- Timing: server_latency_ms
- Client: real IP (behind CF/nginx), user_agent, referer
- Auth: user_id (if authenticated)
- Geo: country (from CF-IPCountry header)
- Partitioning: timestamp, year, month, day, hour

Usage:
    from app_kernel.observability.request_metrics import (
        RequestMetricsMiddleware,
        RequestMetricsStore,
        store_request_metrics,  # Worker task
    )
    
    # Middleware is auto-configured by init_app_kernel() if enabled
    # Worker task is registered automatically
    
    # Query recent metrics:
    store = RequestMetricsStore()
    metrics = await store.get_recent(limit=100, path_prefix="/api/v1/infra")
"""
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field, asdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


# =============================================================================
# Real IP Extraction
# =============================================================================

def get_real_ip(request: Request) -> str:
    """
    Extract real client IP from request, handling reverse proxies.
    
    Priority order (first non-empty wins):
    1. CF-Connecting-IP (Cloudflare)
    2. X-Real-IP (nginx)
    3. X-Forwarded-For (first IP in chain)
    4. request.client.host (direct connection)
    
    Args:
        request: FastAPI/Starlette request
        
    Returns:
        Client IP address string
    """
    # Cloudflare
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    
    # nginx X-Real-IP
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # X-Forwarded-For (can be comma-separated chain)
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # First IP is the original client
        return xff.split(",")[0].strip()
    
    # Direct connection
    if request.client:
        return request.client.host
    
    return "unknown"


def get_geo_from_headers(request: Request) -> Dict[str, Optional[str]]:
    """
    Extract geolocation from Cloudflare headers.
    
    Available CF headers:
    - CF-IPCountry: 2-letter country code
    - CF-IPCity: City name (Enterprise only)
    - CF-IPContinent: Continent code (Enterprise only)
    
    Args:
        request: FastAPI/Starlette request
        
    Returns:
        Dict with country, city, continent (None if not available)
    """
    return {
        "country": request.headers.get("CF-IPCountry"),
        "city": request.headers.get("CF-IPCity"),
        "continent": request.headers.get("CF-IPContinent"),
    }


# =============================================================================
# Sensitive Data Masking
# =============================================================================

# Default param names to mask (case-insensitive substring match)
DEFAULT_SENSITIVE_PARAMS = {
    'token', 'key', 'secret', 'password', 'passwd', 'pwd',
    'api_key', 'apikey', 'auth', 'credential', 'bearer',
    'access_token', 'refresh_token', 'private',
}


def mask_sensitive_params(
    query_params: str,
    sensitive_params: Optional[set] = None,
    mask_char: str = '*',
    visible_chars: int = 4,
) -> str:
    """
    Mask sensitive values in query parameter string.
    
    Args:
        query_params: Query string like "do_token=abc123&name=test"
        sensitive_params: Set of param name substrings to mask (default: DEFAULT_SENSITIVE_PARAMS)
        mask_char: Character to use for masking (default: *)
        visible_chars: Number of chars to show at end (default: 4)
        
    Returns:
        Masked query string like "do_token=***3123&name=test"
        
    Example:
        >>> mask_sensitive_params("do_token=dop_v1_abc123def&page=1")
        "do_token=***3def&page=1"
    """
    if not query_params:
        return query_params
    
    sensitive = sensitive_params or DEFAULT_SENSITIVE_PARAMS
    
    # Parse query string
    parts = []
    for pair in query_params.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            key_lower = key.lower()
            
            # Check if key contains any sensitive substring
            is_sensitive = any(s in key_lower for s in sensitive)
            
            if is_sensitive and value:
                # Mask value, keep last N chars visible
                if len(value) > visible_chars:
                    masked = mask_char * 3 + value[-visible_chars:]
                else:
                    masked = mask_char * len(value)
                parts.append(f"{key}={masked}")
            else:
                parts.append(pair)
        else:
            parts.append(pair)
    
    return '&'.join(parts)


# =============================================================================
# Request Metrics Data
# =============================================================================

@dataclass
class RequestMetric:
    """
    Rich metadata for a single HTTP request.
    
    All fields are JSON-serializable for storage.
    """
    # Request
    request_id: str
    method: str
    path: str
    query_params: Optional[str] = None
    
    # Response
    status_code: int = 0
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    # Timing
    server_latency_ms: float = 0.0
    
    # Client
    client_ip: str = "unknown"
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    
    # Auth
    user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    
    # Geo (from CF headers)
    country: Optional[str] = None
    city: Optional[str] = None
    continent: Optional[str] = None
    
    # Partitioning (for efficient queries)
    timestamp: str = ""
    year: int = 0
    month: int = 0
    day: int = 0
    hour: int = 0
    
    # Extra metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Set timestamp fields if not provided."""
        if not self.timestamp:
            now = datetime.now(timezone.utc)
            self.timestamp = now.isoformat()
            self.year = now.year
            self.month = now.month
            self.day = now.day
            self.hour = now.hour
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)


# =============================================================================
# Request Metrics Middleware
# =============================================================================

class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware that captures request metrics and enqueues for async storage.
    
    Non-blocking: Metrics are enqueued to job queue immediately,
    processed by worker in background.
    
    Args:
        app: ASGI application
        job_client: JobClient instance for enqueueing
        task_name: Name of the storage task (default: "store_request_metrics")
        exclude_paths: Paths to exclude from metrics (e.g., health checks)
        sensitive_params: Param name substrings to mask (default: tokens, keys, passwords)
        include_request_body: Whether to capture request body (default: False)
        include_response_body: Whether to capture response body (default: False)
    """
    
    DEFAULT_EXCLUDE_PATHS = {
        "/health", "/healthz", "/readyz",
        "/metrics", "/favicon.ico",
    }
    
    def __init__(
        self,
        app: ASGIApp,
        job_client = None,
        task_name: str = "store_request_metrics",
        exclude_paths: Optional[set] = None,
        sensitive_params: Optional[set] = None,
        include_request_body: bool = False,
        include_response_body: bool = False,
    ):
        super().__init__(app)
        self._job_client = job_client
        self._task_name = task_name
        self._exclude_paths = exclude_paths or self.DEFAULT_EXCLUDE_PATHS
        self._sensitive_params = sensitive_params  # None = use defaults
        self._include_request_body = include_request_body
        self._include_response_body = include_response_body
    
    def set_job_client(self, job_client):
        """Set job client after initialization (for late binding)."""
        self._job_client = job_client
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Capture metrics and enqueue for storage."""
        path = request.url.path
        
        # Skip excluded paths
        if path in self._exclude_paths:
            return await call_next(request)
        
        # Start timing
        start_time = time.perf_counter()
        
        # Get request context
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        
        # Initialize metric
        now = datetime.now(timezone.utc)
        geo = get_geo_from_headers(request)
        
        # Mask sensitive query params (tokens, keys, passwords, etc.)
        raw_query = str(request.query_params) if request.query_params else None
        masked_query = mask_sensitive_params(raw_query, self._sensitive_params) if raw_query else None
        
        metric = RequestMetric(
            request_id=request_id,
            method=request.method,
            path=path,
            query_params=masked_query,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("User-Agent"),
            referer=request.headers.get("Referer"),
            country=geo.get("country"),
            city=geo.get("city"),
            continent=geo.get("continent"),
            timestamp=now.isoformat(),
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
        )
        
        # Process request
        error_info = None
        try:
            response = await call_next(request)
            metric.status_code = response.status_code
        except Exception as e:
            # Capture error details
            metric.status_code = 500
            metric.error = str(e)
            metric.error_type = type(e).__name__
            error_info = e
            raise
        finally:
            # Calculate duration
            metric.server_latency_ms = (time.perf_counter() - start_time) * 1000
            
            # Get user/workspace from request state (set by auth middleware)
            metric.user_id = getattr(request.state, "user_id", None)
            metric.workspace_id = getattr(request.state, "workspace_id", None)
            
            # Enqueue for async storage (non-blocking)
            await self._enqueue_metric(metric)
        
        return response
    
    async def _enqueue_metric(self, metric: RequestMetric):
        """Enqueue metric for async storage."""
        if not self._job_client:
            # No job client - log and skip
            logger.debug(f"Request metric not stored (no job client): {metric.path}")
            return
        
        try:
            await self._job_client.enqueue(
                self._task_name,
                metric.to_dict(),
                priority="low",  # Don't block important jobs
            )
        except Exception as e:
            # Don't fail the request if metrics storage fails
            logger.warning(f"Failed to enqueue request metric: {e}")


# =============================================================================
# Request Metrics Store (DB)
# =============================================================================

class RequestMetricsStore:
    """
    Database store for request metrics (hot data).
    
    Uses the app_kernel database abstraction (works with SQLite/Postgres/MySQL).
    
    NOTE: Uses raw SQL instead of Entity Framework intentionally:
    - No history/versioning needed (would explode DB size at high request volumes)
    - High-volume writes benefit from direct SQL (no ORM overhead)
    - Simple schema that won't evolve dynamically
    - Metrics are append-only, no updates
    
    Usage:
        store = RequestMetricsStore()
        
        # Save a metric
        await store.save(metric_dict)
        
        # Query recent metrics
        metrics = await store.get_recent(limit=100)
        
        # Query by path
        metrics = await store.get_by_path("/api/v1/infra", limit=50)
        
        # Get aggregated stats
        stats = await store.get_stats(hours=24)
    """
    
    TABLE_NAME = "kernel_request_metrics"
    
    # Schema for table creation
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS request_metrics (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            query_params TEXT,
            status_code INTEGER NOT NULL,
            error TEXT,
            error_type TEXT,
            server_latency_ms REAL NOT NULL,
            client_ip TEXT,
            user_agent TEXT,
            referer TEXT,
            user_id TEXT,
            workspace_id TEXT,
            country TEXT,
            city TEXT,
            continent TEXT,
            timestamp TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    
    # Indexes for common queries
    INDEXES = [
        "CREATE INDEX IF NOT EXISTS idx_request_metrics_timestamp ON request_metrics(timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_request_metrics_path ON request_metrics(path)",
        "CREATE INDEX IF NOT EXISTS idx_request_metrics_status ON request_metrics(status_code)",
        "CREATE INDEX IF NOT EXISTS idx_request_metrics_user ON request_metrics(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_request_metrics_date ON request_metrics(year, month, day)",
    ]
    
    @classmethod
    async def init_schema(cls, db):
        """
        Initialize the request_metrics table.
        
        Call this from your schema_init function:
            async def init_schema(db):
                await RequestMetricsStore.init_schema(db)
                # ... other tables
        """
        await db.execute(cls.SCHEMA)
        for index in cls.INDEXES:
            await db.execute(index)
    
    async def save(self, metric: Dict[str, Any]) -> str:
        """
        Save a request metric to the database.
        
        Args:
            metric: Metric dictionary (from RequestMetric.to_dict())
            
        Returns:
            Generated ID
        """
        from ..db import get_db_connection
        import json
        
        metric_id = str(uuid.uuid4())
        
        # Serialize metadata to JSON if present
        metadata_json = None
        if metric.get("metadata"):
            metadata_json = json.dumps(metric["metadata"])
        
        async with get_db_connection() as db:
            await db.execute(
                f"""
                INSERT INTO {self.TABLE_NAME} (
                    id, request_id, method, path, query_params,
                    status_code, error, error_type, server_latency_ms,
                    client_ip, user_agent, referer,
                    user_id, workspace_id,
                    country, city, continent,
                    timestamp, year, month, day, hour,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metric_id,
                    metric.get("request_id"),
                    metric.get("method"),
                    metric.get("path"),
                    metric.get("query_params"),
                    metric.get("status_code", 0),
                    metric.get("error"),
                    metric.get("error_type"),
                    metric.get("server_latency_ms", 0.0),
                    metric.get("client_ip"),
                    metric.get("user_agent"),
                    metric.get("referer"),
                    metric.get("user_id"),
                    metric.get("workspace_id"),
                    metric.get("country"),
                    metric.get("city"),
                    metric.get("continent"),
                    metric.get("timestamp"),
                    metric.get("year", 0),
                    metric.get("month", 0),
                    metric.get("day", 0),
                    metric.get("hour", 0),
                    metadata_json,
                )
            )
        
        return metric_id
    
    async def get_recent(
        self,
        limit: int = 100,
        offset: int = 0,
        path_prefix: Optional[str] = None,
        status_code: Optional[int] = None,
        user_id: Optional[str] = None,
        min_latency_ms: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent request metrics.
        
        Args:
            limit: Maximum number of results
            offset: Offset for pagination
            path_prefix: Filter by path prefix (e.g., "/api/v1/infra")
            status_code: Filter by exact status code
            user_id: Filter by user ID
            min_latency_ms: Filter by minimum latency (find slow requests)
            
        Returns:
            List of metric dictionaries
        """
        from ..db import get_db_connection
        
        conditions = []
        params = []
        
        if path_prefix:
            conditions.append("path LIKE ?")
            params.append(f"{path_prefix}%")
        
        if status_code is not None:
            conditions.append("status_code = ?")
            params.append(status_code)
        
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        if min_latency_ms is not None:
            conditions.append("server_latency_ms >= ?")
            params.append(min_latency_ms)
        
        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)
        
        params.extend([limit, offset])
        
        async with get_db_connection() as db:
            rows = await db.fetch_all(
                f"""
                SELECT * FROM {self.TABLE_NAME}
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params)
            )
        
        return [dict(row) for row in rows]
    
    async def get_stats(
        self,
        hours: int = 24,
        path_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregated statistics for recent requests.
        
        Args:
            hours: Number of hours to look back
            path_prefix: Optional path prefix filter
            
        Returns:
            Dict with aggregated stats
        """
        from ..db import get_db_connection
        
        # Calculate cutoff time
        cutoff = datetime.now(timezone.utc)
        cutoff = cutoff.replace(hour=cutoff.hour - hours)
        cutoff_str = cutoff.isoformat()
        
        conditions = ["timestamp >= ?"]
        params = [cutoff_str]
        
        if path_prefix:
            conditions.append("path LIKE ?")
            params.append(f"{path_prefix}%")
        
        where_clause = "WHERE " + " AND ".join(conditions)
        
        async with get_db_connection() as db:
            # Total counts
            row = await db.fetch_one(
                f"""
                SELECT 
                    COUNT(*) as total_requests,
                    AVG(server_latency_ms) as avg_latency_ms,
                    MAX(server_latency_ms) as max_latency_ms,
                    MIN(server_latency_ms) as min_latency_ms,
                    SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as error_5xx,
                    SUM(CASE WHEN status_code >= 400 AND status_code < 500 THEN 1 ELSE 0 END) as error_4xx,
                    SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) as success_2xx
                FROM {self.TABLE_NAME}
                {where_clause}
                """,
                tuple(params)
            )
            
            # Top slow endpoints
            slow_rows = await db.fetch_all(
                f"""
                SELECT path, AVG(server_latency_ms) as avg_latency, COUNT(*) as count
                FROM {self.TABLE_NAME}
                {where_clause}
                GROUP BY path
                ORDER BY avg_latency DESC
                LIMIT 10
                """,
                tuple(params)
            )
            
            # Top error endpoints
            error_rows = await db.fetch_all(
                f"""
                SELECT path, status_code, COUNT(*) as count
                FROM {self.TABLE_NAME}
                {where_clause} AND status_code >= 400
                GROUP BY path, status_code
                ORDER BY count DESC
                LIMIT 10
                """,
                tuple(params)
            )
        
        return {
            "period_hours": hours,
            "total_requests": row["total_requests"] if row else 0,
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2) if row else 0,
            "max_latency_ms": round(row["max_latency_ms"] or 0, 2) if row else 0,
            "min_latency_ms": round(row["min_latency_ms"] or 0, 2) if row else 0,
            "error_5xx": row["error_5xx"] if row else 0,
            "error_4xx": row["error_4xx"] if row else 0,
            "success_2xx": row["success_2xx"] if row else 0,
            "slow_endpoints": [dict(r) for r in slow_rows],
            "error_endpoints": [dict(r) for r in error_rows],
        }
    
    async def cleanup_old(self, days: int = 30) -> int:
        """
        Delete metrics older than N days.
        
        Args:
            days: Delete metrics older than this many days
            
        Returns:
            Number of deleted rows
        """
        from ..db import get_db_connection
        
        cutoff = datetime.now(timezone.utc)
        cutoff = cutoff.replace(day=cutoff.day - days)
        cutoff_str = cutoff.isoformat()
        
        async with get_db_connection() as db:
            result = await db.execute(
                f"DELETE FROM {self.TABLE_NAME} WHERE timestamp < ?",
                (cutoff_str,)
            )
        
        return result.rowcount if hasattr(result, 'rowcount') else 0


# =============================================================================
# Worker Task
# =============================================================================

async def store_request_metrics(payload: Dict[str, Any], ctx) -> Dict[str, Any]:
    """
    Worker task to store request metrics to database.
    
    Args:
        payload: RequestMetric.to_dict() data
        ctx: JobContext
        
    Returns:
        Storage result
    """
    try:
        store = RequestMetricsStore()
        metric_id = await store.save(payload)
        return {"status": "success", "id": metric_id}
    except Exception as e:
        logger.error(f"Failed to store metric in DB: {e}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# Setup Helper
# =============================================================================

def setup_request_metrics(
    app,
    job_client = None,
    exclude_paths: Optional[set] = None,
) -> RequestMetricsMiddleware:
    """
    Setup request metrics middleware on a FastAPI app.
    
    Args:
        app: FastAPI application
        job_client: JobClient for async storage (can be set later)
        exclude_paths: Paths to exclude from metrics
        
    Returns:
        The middleware instance (for later configuration)
    """
    middleware = RequestMetricsMiddleware(
        app,
        job_client=job_client,
        exclude_paths=exclude_paths,
    )
    app.add_middleware(RequestMetricsMiddleware, job_client=job_client, exclude_paths=exclude_paths)
    return middleware


# =============================================================================
# API Router
# =============================================================================

from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Literal


def create_request_metrics_router(
    prefix: str = "/metrics/requests",
    protect: Literal["admin", "none"] = "admin",
    get_current_user: Callable = None,
    is_admin: Callable = None,
) -> APIRouter:
    """
    Create API router for request metrics.
    
    Endpoints:
    - GET /metrics/requests - List recent requests
    - GET /metrics/requests/stats - Aggregated statistics
    - GET /metrics/requests/slow - Slow requests
    - GET /metrics/requests/errors - Error requests
    
    Args:
        prefix: URL prefix for routes
        protect: Protection level ("admin" or "none")
        get_current_user: Dependency to get current user
        is_admin: Function to check if user is admin
        
    Returns:
        FastAPI router
    """
    router = APIRouter(prefix=prefix, tags=["Request Metrics"])
    
    async def check_admin(request):
        """Admin check dependency."""
        if protect == "none":
            return True
        
        if protect == "admin":
            if is_admin is None or get_current_user is None:
                raise HTTPException(
                    status_code=403,
                    detail="Metrics endpoint requires admin configuration"
                )
            
            try:
                user = await get_current_user(request)
                if not is_admin(user):
                    raise HTTPException(status_code=403, detail="Admin access required")
                return True
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Metrics auth failed: {e}")
                raise HTTPException(status_code=403, detail="Admin access required")
        
        return True
    
    @router.get("")
    async def list_request_metrics(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        path: Optional[str] = Query(None, description="Filter by path prefix"),
        status: Optional[int] = Query(None, description="Filter by status code"),
        user_id: Optional[str] = Query(None, description="Filter by user ID"),
        min_latency: Optional[float] = Query(None, description="Min latency in ms"),
        _: bool = Depends(check_admin),
    ):
        """
        List recent request metrics.
        
        Returns paginated list of request metrics with optional filters.
        """
        store = RequestMetricsStore()
        metrics = await store.get_recent(
            limit=limit,
            offset=offset,
            path_prefix=path,
            status_code=status,
            user_id=user_id,
            min_latency_ms=min_latency,
        )
        return {
            "items": metrics,
            "count": len(metrics),
            "limit": limit,
            "offset": offset,
        }
    
    @router.get("/stats")
    async def get_request_stats(
        hours: int = Query(24, ge=1, le=168, description="Hours to look back"),
        path: Optional[str] = Query(None, description="Filter by path prefix"),
        _: bool = Depends(check_admin),
    ):
        """
        Get aggregated request statistics.
        
        Returns counts, latency stats, slow endpoints, and error endpoints.
        """
        store = RequestMetricsStore()
        return await store.get_stats(hours=hours, path_prefix=path)
    
    @router.get("/slow")
    async def get_slow_requests(
        limit: int = Query(50, ge=1, le=500),
        min_latency: float = Query(1000, description="Min latency in ms"),
        path: Optional[str] = Query(None, description="Filter by path prefix"),
        _: bool = Depends(check_admin),
    ):
        """
        Get slow requests (above latency threshold).
        """
        store = RequestMetricsStore()
        metrics = await store.get_recent(
            limit=limit,
            path_prefix=path,
            min_latency_ms=min_latency,
        )
        return {"items": metrics, "count": len(metrics), "min_latency_ms": min_latency}
    
    @router.get("/errors")
    async def get_error_requests(
        limit: int = Query(100, ge=1, le=500),
        status: int = Query(500, ge=400, le=599, description="Min status code"),
        path: Optional[str] = Query(None, description="Filter by path prefix"),
        _: bool = Depends(check_admin),
    ):
        """
        Get error requests (4xx and 5xx).
        """
        store = RequestMetricsStore()
        # Get 5xx errors
        errors_5xx = await store.get_recent(
            limit=limit,
            path_prefix=path,
            status_code=500,
        ) if status >= 500 else []
        
        # Get 4xx errors
        errors_4xx = await store.get_recent(
            limit=limit,
            path_prefix=path,
            status_code=400,
        ) if status >= 400 and status < 500 else []
        
        # Combine and sort by timestamp
        all_errors = errors_5xx + errors_4xx
        all_errors.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        return {"items": all_errors[:limit], "count": len(all_errors[:limit])}
    
    return router
