"""
Audit Logging - Track who changed what, when.

Writes to database via Redis (async, no runtime penalty).

Usage:
    # Auto-audit: enabled by default when db_connection used
    # Intercepts save_entity/delete_entity calls
    
    # Query audit logs
    logs = await get_audit_logs(db,
        app="my-api",
        entity="deployments",
        since="2025-01-01",
    )
    
    # Get history for specific entity
    history = await get_entity_audit_history(db, "deployments", deployment_id)
    
    # Auto-mounted routes (admin only):
    #   GET /api/v1/audit               - Query audit logs
    #   GET /api/v1/audit/entity/{type}/{id} - Get entity history
"""

from .publisher import push_audit_event, enable_audit
from .queries import get_audit_logs, get_entity_audit_history
from .schema import init_audit_schema
from .router import create_audit_router

__all__ = [
    "push_audit_event",
    "enable_audit",
    "get_audit_logs",
    "get_entity_audit_history",
    "init_audit_schema",
    "create_audit_router",
]
