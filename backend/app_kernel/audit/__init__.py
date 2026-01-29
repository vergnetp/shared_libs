"""
Audit Logging - Track who changed what, when.

Usage:
    # Manual logging
    await audit_log(db, 
        user_id=user.id,
        action="deployment.created",
        entity="deployments",
        entity_id=deployment_id,
        changes={"status": ["pending", "running"]},
        ip=request.client.host,
    )
    
    # Query audit logs
    logs = await get_audit_logs(db, 
        workspace_id=workspace_id,
        entity="deployments",
        since="2025-01-01",
    )
    
    # Get history for specific entity
    history = await get_entity_audit_history(db, "deployments", deployment_id)
"""

from .stores import (
    audit_log,
    get_audit_logs,
    get_entity_audit_history,
    init_audit_schema,
)
from .router import create_audit_router

__all__ = [
    "audit_log",
    "get_audit_logs",
    "get_entity_audit_history",
    "init_audit_schema",
    "create_audit_router",
]
