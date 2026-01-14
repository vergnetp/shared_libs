"""
Database connection management.

Kernel manages the connection pool via DatabaseManager.
Apps provide config (in ServiceConfig) and schema (via schema_init).

Usage:
    # Config in create_service
    ServiceConfig(
        database_name="./data/app.db",
        database_type="sqlite",
    )
    
    # In routes (FastAPI dependency)
    from ..db import db_connection
    
    @app.get("/users")
    async def get_users(db=Depends(db_connection)):
        return await db.find_entities("users")
    
    # In workers/scripts (context manager)
    from ..db import get_db_connection
    
    async with get_db_connection() as db:
        await db.save_entity("jobs", {"id": "123", "status": "done"})
"""
from typing import Optional, Callable, Awaitable
from contextlib import asynccontextmanager


# Module-level database manager
_db_manager = None


def init_db_session(
    database_name: str,
    database_type: str = "sqlite",
    host: str = "localhost",
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
):
    """
    Initialize the database manager. Called by bootstrap.
    
    Args:
        database_name: Database name or file path (for sqlite)
        database_type: One of 'sqlite', 'postgres', 'mysql'
        host: Database host (postgres/mysql)
        port: Database port (None = use default for type)
        user: Database user (postgres/mysql)
        password: Database password (postgres/mysql)
    """
    global _db_manager
    
    from ...databases.manager import DatabaseManager
    
    # Build kwargs based on database type
    if database_type == "sqlite":
        # SQLite only needs database path
        _db_manager = DatabaseManager(
            db_type="sqlite",
            database=database_name,
        )
    else:
        # Postgres/MySQL need connection params
        _db_manager = DatabaseManager(
            db_type=database_type,
            database=database_name,
            host=host,
            port=port or {"postgres": 5432, "mysql": 3306}.get(database_type, 5432),
            user=user,
            password=password,
        )
    
    return _db_manager


def get_db_manager():
    """Get the database manager instance."""
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Set database_name in ServiceConfig.")
    return _db_manager


@asynccontextmanager
async def get_db_connection():
    """
    Get a database connection from the pool.
    
    Connection is automatically released when context exits.
    
    Usage:
        async with get_db_connection() as db:
            user = await db.get_entity("users", user_id)
            await db.save_entity("users", user)
    """
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Set database_name in ServiceConfig.")
    
    async with _db_manager as conn:
        yield conn


async def db_connection():
    """
    FastAPI dependency for database connections.
    
    Connection is acquired from pool at request start,
    released back to pool when request completes.
    
    Usage:
        @app.get("/")
        async def handler(db=Depends(db_connection)):
            ...
    """
    async with get_db_connection() as conn:
        yield conn


async def init_schema(init_fn: Callable[[any], Awaitable[None]]):
    """
    Initialize database schema using provided function.
    
    Args:
        init_fn: Async function that takes db connection and creates tables
    
    Usage:
        async def my_schema(db):
            await db.execute("CREATE TABLE IF NOT EXISTS ...")
        
        await init_schema(my_schema)
    """
    async with get_db_connection() as db:
        await init_fn(db)


async def close_db():
    """Close database connections on shutdown."""
    global _db_manager
    if _db_manager is not None:
        from ...databases.manager import DatabaseManager
        await DatabaseManager.close_all()
        _db_manager = None
