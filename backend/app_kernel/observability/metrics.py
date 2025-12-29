"""
Metrics collection.

Provides simple metrics collection for observability.
Can be extended with Prometheus, DataDog, etc.

Usage:
    from app_kernel.observability import metrics
    
    # Increment counter
    metrics.increment("api.requests", tags={"endpoint": "/users"})
    
    # Record timing
    with metrics.timer("api.response_time"):
        response = await process_request()
    
    # Record gauge
    metrics.gauge("connections.active", 42)
"""
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from contextlib import contextmanager
import time
import threading


@dataclass
class MetricPoint:
    """A single metric data point."""
    name: str
    value: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)
    metric_type: str = "counter"  # counter, gauge, histogram


class MetricsCollector:
    """
    Simple in-memory metrics collector.
    
    For production, replace with Prometheus, DataDog, etc.
    """
    
    def __init__(self):
        self._counters: Dict[str, float] = {}
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
    
    def _make_key(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """Create a unique key from name and tags."""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}|{tag_str}"
    
    def increment(
        self,
        name: str,
        value: float = 1,
        tags: Optional[Dict[str, str]] = None
    ):
        """Increment a counter."""
        key = self._make_key(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value
    
    def gauge(
        self,
        name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None
    ):
        """Set a gauge value."""
        key = self._make_key(name, tags)
        with self._lock:
            self._gauges[key] = value
    
    # Alias for compatibility
    set_gauge = gauge
    
    def histogram(
        self,
        name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None
    ):
        """Record a histogram value."""
        key = self._make_key(name, tags)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = []
            self._histograms[key].append(value)
    
    @contextmanager
    def timer(self, name: str, tags: Optional[Dict[str, str]] = None):
        """Context manager for timing operations."""
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            self.histogram(name, duration, tags)
    
    def get_counters(self) -> Dict[str, float]:
        """Get all counter values."""
        with self._lock:
            return dict(self._counters)
    
    def get_gauges(self) -> Dict[str, float]:
        """Get all gauge values."""
        with self._lock:
            return dict(self._gauges)
    
    def get_histogram_stats(self, name: str) -> Optional[Dict[str, float]]:
        """Get statistics for a histogram."""
        key = name
        with self._lock:
            values = self._histograms.get(key)
            if not values:
                return None
            
            sorted_values = sorted(values)
            count = len(sorted_values)
            
            return {
                "count": count,
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "mean": sum(sorted_values) / count,
                "p50": sorted_values[int(count * 0.5)],
                "p95": sorted_values[int(count * 0.95)],
                "p99": sorted_values[int(count * 0.99)] if count > 100 else sorted_values[-1],
            }
    
    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
    
    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        
        # Counters
        with self._lock:
            for key, value in self._counters.items():
                name, labels = self._parse_key(key)
                safe_name = self._prometheus_name(name)
                lines.append(f"# TYPE {safe_name} counter")
                lines.append(f"{safe_name}{labels} {value}")
            
            # Gauges
            for key, value in self._gauges.items():
                name, labels = self._parse_key(key)
                safe_name = self._prometheus_name(name)
                lines.append(f"# TYPE {safe_name} gauge")
                lines.append(f"{safe_name}{labels} {value}")
            
            # Histograms (export as summary for simplicity)
            for key, values in self._histograms.items():
                if not values:
                    continue
                name, labels = self._parse_key(key)
                safe_name = self._prometheus_name(name)
                sorted_values = sorted(values)
                count = len(sorted_values)
                total = sum(sorted_values)
                
                lines.append(f"# TYPE {safe_name} summary")
                lines.append(f"{safe_name}_count{labels} {count}")
                lines.append(f"{safe_name}_sum{labels} {total}")
                
                # Quantiles
                if count > 0:
                    # Build quantile labels - merge with existing labels if any
                    if labels:
                        # labels is like {k="v"}, we need {quantile="X",k="v"}
                        q50_labels = '{quantile="0.5",' + labels[1:]
                        q90_labels = '{quantile="0.9",' + labels[1:]
                        q99_labels = '{quantile="0.99",' + labels[1:]
                    else:
                        q50_labels = '{quantile="0.5"}'
                        q90_labels = '{quantile="0.9"}'
                        q99_labels = '{quantile="0.99"}'
                    
                    lines.append(f'{safe_name}{q50_labels} {sorted_values[int(count * 0.5)]}')
                    lines.append(f'{safe_name}{q90_labels} {sorted_values[int(count * 0.9)]}')
                    lines.append(f'{safe_name}{q99_labels} {sorted_values[min(int(count * 0.99), count - 1)]}')
        
        return "\n".join(lines)
    
    def _parse_key(self, key: str) -> tuple:
        """Parse key into name and labels."""
        if "|" in key:
            name, tag_str = key.split("|", 1)
            # Convert k=v,k=v to {k="v",k="v"}
            pairs = tag_str.split(",")
            label_parts = []
            for pair in pairs:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    label_parts.append(f'{k}="{v}"')
            labels = "{" + ",".join(label_parts) + "}" if label_parts else ""
            return name, labels
        return key, ""
    
    def _prometheus_name(self, name: str) -> str:
        """Convert name to valid Prometheus metric name."""
        return name.replace(".", "_").replace("-", "_")


# Module-level metrics collector
_metrics: Optional[MetricsCollector] = None


def init_metrics() -> MetricsCollector:
    """Initialize the metrics collector."""
    global _metrics
    _metrics = MetricsCollector()
    return _metrics


def get_metrics() -> MetricsCollector:
    """Get the metrics collector."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


# Convenience access
metrics = property(lambda self: get_metrics())


# =============================================================================
# Metrics Router
# =============================================================================

from typing import Callable, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response


def create_metrics_router(
    metrics_path: str = "/metrics",
    protect_metrics: Literal["admin", "internal", "none"] = "admin",
    get_current_user: Callable = None,
    is_admin: Callable = None,
) -> APIRouter:
    """
    Create metrics router with protection.
    
    Args:
        metrics_path: Path for metrics endpoint
        protect_metrics: Protection level
            - "admin": require admin user (MUST provide is_admin callback)
            - "internal": TODO - IP allowlist (falls back to admin until implemented)
            - "none": no protection (dev only)
        get_current_user: Dependency to get current user (required if protect_metrics="admin")
        is_admin: Function to check if user is admin (REQUIRED if protect_metrics="admin")
    
    Security:
        - Fails closed: if is_admin callback not provided, denies all access
        - "internal" mode not yet implemented, falls back to "admin" behavior
    """
    import logging
    logger = logging.getLogger(__name__)
    
    router = APIRouter(tags=["Observability"])
    
    # Warn at router creation time if misconfigured
    if protect_metrics == "admin" and is_admin is None:
        logger.warning(
            "SECURITY: protect_metrics='admin' but is_admin callback not provided. "
            "Metrics endpoint will deny ALL requests until is_admin is configured."
        )
    
    if protect_metrics == "internal":
        logger.warning(
            "SECURITY: protect_metrics='internal' (IP allowlist) not yet implemented. "
            "Falling back to 'admin' protection. Provide is_admin callback or use protect_metrics='admin'."
        )
    
    async def check_admin(request: Request):
        """Admin check dependency - fails closed."""
        if protect_metrics == "none":
            return True
        
        # "internal" not implemented - fall back to admin protection
        # This is safer than allowing all traffic
        if protect_metrics in ("admin", "internal"):
            # FAIL CLOSED: if is_admin not provided, deny everyone
            if is_admin is None:
                logger.error(
                    f"Metrics access DENIED: protect_metrics='{protect_metrics}' "
                    "but is_admin callback not configured"
                )
                raise HTTPException(
                    status_code=403, 
                    detail="Metrics endpoint misconfigured: admin check not available"
                )
            
            if get_current_user is None:
                raise HTTPException(status_code=403, detail="Admin required but auth not configured")
            
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
    
    @router.get(
        metrics_path,
        summary="Prometheus metrics",
        description="Export metrics in Prometheus text format.",
        response_class=Response,
    )
    async def prometheus_metrics(
        request: Request,
        _: bool = Depends(check_admin),
    ):
        """
        Export metrics in Prometheus format.
        
        Protected by default (admin only).
        """
        collector = get_metrics()
        content = collector.to_prometheus()
        return Response(
            content=content,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    
    return router
