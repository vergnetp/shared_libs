"""
app_kernel.observability - Logging, metrics, and audit.

This module provides:
- Structured logging with context
- Metrics collection
- Audit logging for security events

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
]
