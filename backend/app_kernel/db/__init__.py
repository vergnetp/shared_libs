"""
app_kernel.db - Database connection management.

Two connection modes (strict = enforces entity class usage):

    db_dependency / db_context         - strict (default for app code)
    raw_db_dependency / raw_db_context - no guard (kernel internals)

Usage:
    # In routes (FastAPI dependency) - strict by default
    from app_kernel.db import db_dependency
    
    @app.get("/deployments")
    async def list_deps(db=Depends(db_dependency)):
        return await Deployment.find(db, where="env = ?", params=("prod",))
    
    # In workers (context manager) - strict by default
    from app_kernel.db import db_context
    
    async with db_context() as db:
        await Deployment.save(db, {"name": "new"})
    
    # Schema initialization (in on_startup)
    await init_schema(my_init_fn)
"""

from .session import (
    # Setup
    init_db_session,
    get_db_manager,
    init_schema,
    close_db,
    
    # Strict (default for app code)
    db_dependency,
    db_context,
    
    # Raw (kernel internals, power users)
    raw_db_dependency,
    raw_db_context,
    
    # Backward compatibility aliases
    get_db_connection,  # -> raw_db_context
    db_connection,      # -> db_dependency
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
    
    # Strict connections (default for app code)
    "db_dependency",
    "db_context",
    
    # Raw connections (kernel internals, power users)
    "raw_db_dependency",
    "raw_db_context",
    
    # Backward compatibility
    "get_db_connection",
    "db_connection",
    
    # Schema init
    "init_all_schemas",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
    
    # Connection types (for type hints)
    "AsyncConnection",
    "SyncConnection",
]