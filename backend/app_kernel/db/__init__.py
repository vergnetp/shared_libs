"""
app_kernel.db - Database session management and schema.

This module provides:
- Session factory for database connections
- FastAPI dependency for database access
- Context manager for workers/scripts
- Kernel infrastructure schema (jobs, audit_log, etc.)

Usage:
    from app_kernel.db import get_db_session, db_session_dependency
    
    # In routes
    @app.get("/users")
    async def get_users(db = Depends(db_session_dependency)):
        return await db.fetch_all("SELECT * FROM users")
    
    # In workers
    async with get_db_session() as db:
        await db.execute("UPDATE ...")
    
    # Initialize kernel tables
    from app_kernel.db import init_kernel_schema
    await init_kernel_schema(db)
"""

from .session import (
    DatabaseSession,
    DatabaseSessionFactory,
    init_db_session,
    get_db_factory,
    get_db_session,
    db_session_dependency,
)

from .schema import (
    init_kernel_schema,
    cleanup_expired_idempotency_keys,
    cleanup_old_rate_limits,
)

__all__ = [
    # Session
    "DatabaseSession",
    "DatabaseSessionFactory",
    "init_db_session",
    "get_db_factory",
    "get_db_session",
    "db_session_dependency",
    
    # Schema
    "init_kernel_schema",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
]
