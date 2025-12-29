"""
Database session management.

Provides a factory for database sessions usable by:
- API routes
- Middleware
- Job workers

This wraps the existing databases module.

Usage:
    from app_kernel.db import get_db_session, db_session_dependency
    
    # In routes
    @app.get("/users")
    async def get_users(db = Depends(db_session_dependency)):
        return await db.fetch_all("SELECT * FROM users")
    
    # In workers
    async with get_db_session() as db:
        await db.execute("UPDATE jobs SET status = ?", ["completed"])
"""
from typing import Optional, Any, AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends


class DatabaseSession:
    """
    Wrapper around database connection for unified interface.
    
    Apps can use this with any database backend.
    """
    
    def __init__(self, connection):
        """
        Initialize session with a connection.
        
        Args:
            connection: Database connection (from databases module or SQLAlchemy)
        """
        self._conn = connection
    
    @property
    def connection(self):
        """Get the underlying connection."""
        return self._conn
    
    async def execute(self, query: str, values: Optional[list] = None) -> Any:
        """Execute a query."""
        if hasattr(self._conn, 'execute'):
            return await self._conn.execute(query, values or [])
        raise NotImplementedError("Connection doesn't support execute")
    
    async def fetch_one(self, query: str, values: Optional[list] = None) -> Optional[dict]:
        """Fetch a single row."""
        if hasattr(self._conn, 'fetch_one'):
            return await self._conn.fetch_one(query, values or [])
        raise NotImplementedError("Connection doesn't support fetch_one")
    
    async def fetch_all(self, query: str, values: Optional[list] = None) -> list:
        """Fetch all rows."""
        if hasattr(self._conn, 'fetch_all'):
            return await self._conn.fetch_all(query, values or [])
        raise NotImplementedError("Connection doesn't support fetch_all")


class DatabaseSessionFactory:
    """
    Factory for creating database sessions.
    
    Initialized with connection configuration, provides sessions on demand.
    """
    
    def __init__(self, connection_manager=None, database_url: Optional[str] = None):
        """
        Initialize session factory.
        
        Args:
            connection_manager: ConnectionManager from databases module
            database_url: Optional database URL for auto-configuration
        """
        self._connection_manager = connection_manager
        self._database_url = database_url
    
    @asynccontextmanager
    async def get_session(self) -> AsyncIterator[DatabaseSession]:
        """
        Get a database session.
        
        Usage:
            async with factory.get_session() as db:
                await db.execute("SELECT 1")
        """
        if self._connection_manager is None:
            raise RuntimeError("Database not configured. Provide connection_manager or database_url.")
        
        # Get connection from manager
        async with self._connection_manager.connection() as conn:
            yield DatabaseSession(conn)
    
    async def dependency(self) -> AsyncIterator[DatabaseSession]:
        """
        FastAPI dependency for getting a database session.
        
        Usage:
            @app.get("/")
            async def handler(db = Depends(db_factory.dependency)):
                ...
        """
        async with self.get_session() as session:
            yield session


# Module-level factory
_db_factory: Optional[DatabaseSessionFactory] = None


def init_db_session(
    connection_manager=None,
    database_url: Optional[str] = None
) -> DatabaseSessionFactory:
    """Initialize the database session factory. Called by init_app_kernel()."""
    global _db_factory
    _db_factory = DatabaseSessionFactory(connection_manager, database_url)
    return _db_factory


def get_db_factory() -> DatabaseSessionFactory:
    """Get the database session factory."""
    if _db_factory is None:
        raise RuntimeError("Database not initialized. Call init_app_kernel() first.")
    return _db_factory


@asynccontextmanager
async def get_db_session() -> AsyncIterator[DatabaseSession]:
    """
    Get a database session.
    
    Convenience function for use in workers and scripts.
    """
    factory = get_db_factory()
    async with factory.get_session() as session:
        yield session


async def db_session_dependency() -> AsyncIterator[DatabaseSession]:
    """
    FastAPI dependency for database sessions.
    
    Usage:
        @app.get("/")
        async def handler(db = Depends(db_session_dependency)):
            ...
    """
    factory = get_db_factory()
    async for session in factory.dependency():
        yield session
