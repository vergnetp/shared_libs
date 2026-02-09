"""
app_kernel.db - Database connection management.

Entity methods auto-acquire connections when db is omitted:
    project = await Project.get(id="abc")       # auto-acquires + releases
    projects = await Project.find(where="x=?")  # auto-acquires + releases

For batching multiple ops on one connection:
    async with db_context() as db:
        project = await Project.get(db, id="abc")
        service = await Service.get(db, id="xyz")

Kernel internals use raw_db_context() for non-entity operations.
"""

from .session import (
    # Setup
    init_db_session,
    get_db_manager,
    init_schema,
    close_db,
    
    # Strict (default for app code)
    db_context,
    
    # Raw (kernel internals)
    raw_db_context,
    
    # Backward compatibility alias
    get_db_connection,  # -> raw_db_context
)

from .schema import (
    init_all_schemas,
    cleanup_expired_idempotency_keys,
    cleanup_old_rate_limits,
)

# Re-export connection types so apps don't import from databases directly
from ...databases.connections import AsyncConnection, SyncConnection

__all__ = [
    # Setup
    "init_db_session",
    "get_db_manager",
    "init_schema",
    "close_db",
    
    # Connections
    "db_context",
    "raw_db_context",
    "get_db_connection",
    
    # Schema init
    "init_all_schemas",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
    
    # Connection types (for type hints)
    "AsyncConnection",
    "SyncConnection",
]