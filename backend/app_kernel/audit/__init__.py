"""
Audit Logging - Track who changed what, when.

Writes to shared admin_db via Redis (async, no runtime penalty).

Usage:
    # Auto-audit: enabled by default when db_connection used
    # Intercepts save_entity/delete_entity calls
    
    # Query audit logs (from admin_db)
    logs = await get_audit_logs(admin_db,
        app="deploy_api",
        entity="deployments",
        since="2025-01-01",
    )
    
    # Get history for specific entity
    history = await get_entity_audit_history(admin_db, "deployments", deployment_id)
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
