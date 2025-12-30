"""
app_kernel.db - Database session management and schema.

Kernel manages the connection pool via DatabaseManager.
Apps provide config (in ServiceConfig) and schema (via init_schema callback).

Usage:
    # Config in create_service
    ServiceConfig(
        database_url="./data/app.db",
        database_type="sqlite",
    )
    
    # In routes
    @app.get("/users")
    async def get_users(db = Depends(db_session_dependency)):
        return await db.find_entities("users")
    
    # In workers/scripts
    async with get_db_session() as db:
        await db.save_entity("users", user)
    
    # Initialize app schema (in on_startup)
    await init_schema(my_init_fn)
"""

from .session import (
    init_db_session,
    get_db_manager,
    get_db_session,
    db_session_dependency,
    init_schema,
    close_db_session,
)

from .schema import (
    init_kernel_schema,
    cleanup_expired_idempotency_keys,
    cleanup_old_rate_limits,
)

# Re-export connection types so apps don't import from databases directly
from backend.databases.connections import AsyncConnection, SyncConnection

__all__ = [
    # Session management
    "init_db_session",
    "get_db_manager",
    "get_db_session",
    "db_session_dependency",
    "init_schema",
    "close_db_session",
    
    # Kernel schema
    "init_kernel_schema",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
    
    # Connection types (for type hints)
    "AsyncConnection",
    "SyncConnection",
]
