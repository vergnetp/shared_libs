"""
app_kernel.db - Database connection management.

Kernel manages the connection pool via DatabaseManager.
Apps provide config (in ServiceConfig) and schema (via init_schema callback).

Usage:
    # Config in create_service
    ServiceConfig(
        database_name="./data/app.db",
        database_type="sqlite",
    )
    
    # In routes (FastAPI dependency)
    @app.get("/users")
    async def get_users(db=Depends(db_connection)):
        return await db.find_entities("kernel_users")
    
    # In workers/scripts (context manager)
    async with get_db_connection() as db:
        await db.save_entity("kernel_users", user)
    
    # Initialize app schema (in on_startup)
    await init_schema(my_init_fn)
"""

from .session import (
    init_db_session,
    get_db_manager,
    get_db_connection,
    db_connection,
    init_schema,
    close_db,
)

from .schema import (
    init_all_schemas,
    cleanup_expired_idempotency_keys,
    cleanup_old_rate_limits,
)

# Re-export connection types so apps don't import from databases directly
from ...databases.connections import AsyncConnection, SyncConnection

__all__ = [
    # Connection management
    "init_db_session",
    "get_db_manager",
    "get_db_connection",
    "db_connection",
    "init_schema",
    "close_db",
    
    # Schema init
    "init_all_schemas",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
    
    # Connection types (for type hints)
    "AsyncConnection",
    "SyncConnection",
]
