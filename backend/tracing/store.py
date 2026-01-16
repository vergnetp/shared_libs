"""
Trace storage for persisting request traces.

Supports multiple backends (SQLite, PostgreSQL, etc.) through a simple interface.
Traces are stored with their nested spans as JSON for easy drill-down.
"""

from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import RequestContext

logger = logging.getLogger(__name__)


class TraceStore(ABC):
    """
    Abstract base class for trace storage.
    
    Implementations should handle:
    - Saving traces with spans
    - Querying traces by various criteria
    - Auto-cleanup of old traces
    """
    
    @abstractmethod
    def save(self, ctx: 'RequestContext') -> None:
        """Save a request context (sync)."""
        pass
    
    @abstractmethod
    async def save_async(self, ctx: 'RequestContext') -> None:
        """Save a request context (async)."""
        pass
    
    @abstractmethod
    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get a single trace by request ID."""
        pass
    
    @abstractmethod
    async def get_async(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get a single trace by request ID (async)."""
        pass
    
    @abstractmethod
    def query(
        self,
        path_prefix: Optional[str] = None,
        min_duration_ms: Optional[float] = None,
        status_code: Optional[int] = None,
        status_class: Optional[str] = None,
        user_id: Optional[str] = None,
        has_errors: Optional[bool] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query traces with filters."""
        pass
    
    @abstractmethod
    async def query_async(self, **kwargs) -> List[Dict[str, Any]]:
        """Query traces with filters (async)."""
        pass
    
    @abstractmethod
    def get_stats(
        self,
        since: Optional[datetime] = None,
        path_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregated statistics."""
        pass
    
    @abstractmethod
    async def get_stats_async(self, **kwargs) -> Dict[str, Any]:
        """Get aggregated statistics (async)."""
        pass
    
    @abstractmethod
    def cleanup(self, older_than: timedelta) -> int:
        """Delete traces older than specified duration. Returns count deleted."""
        pass


class SQLiteTraceStore(TraceStore):
    """
    SQLite-based trace storage.
    
    Good for single-instance deployments or development.
    Uses JSON for spans storage.
    """
    
    def __init__(self, db_path: str = "traces.db", service_name: str = "unknown"):
        """
        Initialize SQLite trace store.
        
        Args:
            db_path: Path to SQLite database file
            service_name: Name of the service being traced
        """
        self.db_path = db_path
        self.service_name = service_name
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        import sqlite3
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_traces (
                    request_id TEXT PRIMARY KEY,
                    trace_id TEXT,
                    service_name TEXT,
                    method TEXT,
                    path TEXT,
                    status_code INTEGER,
                    duration_ms REAL,
                    user_id TEXT,
                    workspace_id TEXT,
                    has_errors INTEGER DEFAULT 0,
                    span_count INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    spans TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON request_traces(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_service ON request_traces(service_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_path ON request_traces(path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_duration ON request_traces(duration_ms DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON request_traces(status_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_user ON request_traces(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_errors ON request_traces(has_errors)")
            
            conn.commit()
    
    def _get_connection(self):
        """Get database connection."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def save(self, ctx: 'RequestContext') -> None:
        """Save request context to database."""
        spans = ctx.get_spans()
        spans_json = json.dumps([s.to_dict() for s in spans])
        timestamp = datetime.utcnow().isoformat()
        
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO request_traces 
                (request_id, trace_id, service_name, method, path, status_code, duration_ms, 
                 user_id, workspace_id, has_errors, span_count, timestamp, spans)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ctx.request_id,
                ctx.trace_id,
                self.service_name,
                ctx.method,
                ctx.path,
                getattr(ctx, 'status_code', None),
                ctx.duration_ms,
                ctx.user_id,
                ctx.workspace_id,
                1 if ctx.has_errors else 0,
                len(spans),
                timestamp,
                spans_json,
            ))
            conn.commit()
    
    async def save_async(self, ctx: 'RequestContext') -> None:
        """Save request context (runs sync in executor)."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.save, ctx)
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert database row to dictionary."""
        result = {
            "request_id": row["request_id"],
            "trace_id": row["trace_id"],
            "service_name": row["service_name"] if "service_name" in row.keys() else self.service_name,
            "method": row["method"],
            "path": row["path"],
            "status_code": row["status_code"],
            "duration_ms": row["duration_ms"],
            "user_id": row["user_id"],
            "workspace_id": row["workspace_id"],
            "has_errors": bool(row["has_errors"]),
            "span_count": row["span_count"],
            "timestamp": row["timestamp"],
        }
        
        if row["spans"]:
            result["spans"] = json.loads(row["spans"])
        else:
            result["spans"] = []
        
        return result
    
    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get trace by request ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM request_traces WHERE request_id = ?",
                (request_id,)
            ).fetchone()
            
            if row:
                return self._row_to_dict(row)
            return None
    
    async def get_async(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get trace (runs sync in executor)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get, request_id)
    
    def query(
        self,
        service_name: Optional[str] = None,
        path_prefix: Optional[str] = None,
        min_duration_ms: Optional[float] = None,
        status_code: Optional[int] = None,
        status_class: Optional[str] = None,
        user_id: Optional[str] = None,
        has_errors: Optional[bool] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query traces with filters."""
        conditions = []
        params = []
        
        if service_name:
            conditions.append("service_name = ?")
            params.append(service_name)
        
        if path_prefix:
            conditions.append("path LIKE ?")
            params.append(f"{path_prefix}%")
        
        if min_duration_ms:
            conditions.append("duration_ms >= ?")
            params.append(min_duration_ms)
        
        if status_code:
            conditions.append("status_code = ?")
            params.append(status_code)
        
        if status_class:
            if status_class == "2xx":
                conditions.append("status_code >= 200 AND status_code < 300")
            elif status_class == "4xx":
                conditions.append("status_code >= 400 AND status_code < 500")
            elif status_class == "5xx":
                conditions.append("status_code >= 500")
        
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        if has_errors is not None:
            conditions.append("has_errors = ?")
            params.append(1 if has_errors else 0)
        
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        
        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        query = f"""
            SELECT * FROM request_traces 
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    async def query_async(self, **kwargs) -> List[Dict[str, Any]]:
        """Query traces (runs sync in executor)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.query(**kwargs))
    
    def get_stats(
        self,
        since: Optional[datetime] = None,
        path_prefix: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregated statistics."""
        conditions = []
        params = []
        
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        
        if path_prefix:
            conditions.append("path LIKE ?")
            params.append(f"{path_prefix}%")
        
        if service_name:
            conditions.append("service_name = ?")
            params.append(service_name)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        with self._get_connection() as conn:
            # Basic stats
            row = conn.execute(f"""
                SELECT 
                    COUNT(*) as total_requests,
                    AVG(duration_ms) as avg_latency_ms,
                    MAX(duration_ms) as max_latency_ms,
                    SUM(has_errors) as error_count,
                    COUNT(CASE WHEN duration_ms > 1000 THEN 1 END) as slow_count
                FROM request_traces
                WHERE {where_clause}
            """, params).fetchone()
            
            total = row["total_requests"] or 0
            errors = row["error_count"] or 0
            
            # P95 latency (approximation)
            p95_row = conn.execute(f"""
                SELECT duration_ms FROM request_traces
                WHERE {where_clause}
                ORDER BY duration_ms DESC
                LIMIT 1 OFFSET ?
            """, params + [max(0, int(total * 0.05))]).fetchone()
            
            # Top slow endpoints
            slow_endpoints = conn.execute(f"""
                SELECT path, AVG(duration_ms) as avg_ms, COUNT(*) as count
                FROM request_traces
                WHERE {where_clause}
                GROUP BY path
                ORDER BY avg_ms DESC
                LIMIT 10
            """, params).fetchall()
            
            # Error breakdown by path
            error_endpoints = conn.execute(f"""
                SELECT path, COUNT(*) as count
                FROM request_traces
                WHERE {where_clause} AND has_errors = 1
                GROUP BY path
                ORDER BY count DESC
                LIMIT 10
            """, params).fetchall()
            
            return {
                "total_requests": total,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
                "max_latency_ms": round(row["max_latency_ms"] or 0, 2),
                "p95_latency_ms": round(p95_row["duration_ms"], 2) if p95_row else 0,
                "error_count": errors,
                "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
                "slow_count": row["slow_count"] or 0,
                "slow_endpoints": [
                    {"path": r["path"], "avg_ms": round(r["avg_ms"], 2), "count": r["count"]}
                    for r in slow_endpoints
                ],
                "error_endpoints": [
                    {"path": r["path"], "count": r["count"]}
                    for r in error_endpoints
                ],
            }
    
    async def get_stats_async(self, **kwargs) -> Dict[str, Any]:
        """Get stats (runs sync in executor)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.get_stats(**kwargs))
    
    def cleanup(self, older_than: timedelta) -> int:
        """Delete traces older than specified duration."""
        cutoff = datetime.utcnow() - older_than
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM request_traces WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            conn.commit()
            return cursor.rowcount
    
    def get_services(self) -> List[str]:
        """Get list of distinct service names in traces."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT service_name FROM request_traces WHERE service_name IS NOT NULL ORDER BY service_name"
            ).fetchall()
            return [row["service_name"] for row in rows]
    
    async def get_services_async(self) -> List[str]:
        """Get services (runs sync in executor)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_services)
