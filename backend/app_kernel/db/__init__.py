"""
app_kernel.db - Database session management.

This module provides:
- Session factory for database connections
- FastAPI dependency for database access
- Context manager for workers/scripts

Usage:
    from app_kernel.db import get_db_session, db_session_dependency
    
    # In routes
    @app.get("/users")
    async def get_users(db = Depends(db_session_dependency)):
        return await db.fetch_all("SELECT * FROM users")
    
    # In workers
    async with get_db_session() as db:
        await db.execute("UPDATE ...")
"""

from .session import (
    DatabaseSession,
    DatabaseSessionFactory,
    init_db_session,
    get_db_factory,
    get_db_session,
    db_session_dependency,
)

__all__ = [
    "DatabaseSession",
    "DatabaseSessionFactory",
    "init_db_session",
    "get_db_factory",
    "get_db_session",
    "db_session_dependency",
]
