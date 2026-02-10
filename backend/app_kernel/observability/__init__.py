"""
app_kernel.observability - Logging, metrics, audit, and request metrics.

This module provides:
- Structured logging with context
- Metrics collection
- Audit logging for security events
- Request metrics (latency, errors, geo) with async storage

Usage:
    from app_kernel.observability import get_logger, log_context, get_metrics, get_audit
    
    # Logging with context
    logger = get_logger()
    with log_context(request_id="123"):
        logger.info("Processing request")
    
    # Metrics
    metrics = get_metrics()
    metrics.increment("api.requests")
    
    # Audit
    audit = get_audit()
    await audit.log("user.login", actor_id=user.id)
    
    # Request metrics
    from app_kernel.observability import RequestMetricsStore, get_real_ip
    store = RequestMetricsStore()
    stats = await store.get_stats(hours=24)
"""

from .logging import (
    KernelLogger,
    StdlibLogger,
    log_context,
    init_kernel_logger,
    get_logger,
)

from .metrics import (
    MetricPoint,
    MetricsCollector,
    init_metrics,
    get_metrics,
)

from .audit import (
    AuditEvent,
    AuditStore,
    InMemoryAuditStore,
    AuditLogger,
    init_audit,
    get_audit,
)

from .request_metrics import (
    RequestMetric,
    RequestMetricsMiddleware,
    RequestMetricsStore,
    get_real_ip,
    get_geo_from_headers,
    setup_request_metrics,
    create_request_metrics_router,
    mask_sensitive_params,
    DEFAULT_SENSITIVE_PARAMS,
)

__all__ = [
    # Logging
    "KernelLogger",
    "StdlibLogger",
    "log_context",
    "init_kernel_logger",
    "get_logger",
    
    # Metrics
    "MetricPoint",
    "MetricsCollector",
    "init_metrics",
    "get_metrics",
    
    # Audit
    "AuditEvent",
    "AuditStore",
    "InMemoryAuditStore",
    "AuditLogger",
    "init_audit",
    "get_audit",
    
    # Request Metrics
    "RequestMetric",
    "RequestMetricsMiddleware",
    "RequestMetricsStore",
    "get_real_ip",
    "get_geo_from_headers",
    "setup_request_metrics",
    "create_request_metrics_router",
    "mask_sensitive_params",
    "DEFAULT_SENSITIVE_PARAMS",
]
