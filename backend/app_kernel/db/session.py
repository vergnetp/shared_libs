"""
Database connection management.

Kernel manages the connection pool via DatabaseManager.
Apps provide config (in ServiceConfig) and schema (via schema_init).

When REDIS_URL is configured, audit logging is automatically enabled:
- Every save_entity() and delete_entity() pushes an audit event to Redis
- The admin_worker consumes these events and writes to admin_db

Usage:
    # Config in create_service
    ServiceConfig(
        database_url="sqlite:///./data/app.db",
        redis_url="redis://localhost:6379",  # Enables audit
    )
    
    # In routes (FastAPI dependency)
    from ..db import db_connection
    
    @app.get("/users")
    async def get_users(db=Depends(db_connection)):
        return await db.find_entities("users")
"""
from typing import Optional, Callable, Awaitable
from contextlib import asynccontextmanager


# Module-level state
_db_manager = None
_audit_redis_url: Optional[str] = None
_audit_app_name: Optional[str] = None


def init_db_session(
    database_name: str,
    database_type: str = "sqlite",
    host: str = "localhost",
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
):
    """Initialize the database manager. Called by bootstrap."""
    global _db_manager
    
    from ...databases.manager import DatabaseManager
    
    if database_type == "sqlite":
        _db_manager = DatabaseManager(
            db_type="sqlite",
            database=database_name,
        )
    else:
        _db_manager = DatabaseManager(
            db_type=database_type,
            database=database_name,
            host=host,
            port=port or {"postgres": 5432, "mysql": 3306}.get(database_type, 5432),
            user=user,
            password=password,
        )
    
    return _db_manager


def enable_auto_audit(redis_url: str, app_name: str):
    """
    Enable automatic audit logging for all database operations.
    Called by bootstrap when both DATABASE_URL and REDIS_URL are configured.
    """
    global _audit_redis_url, _audit_app_name
    _audit_redis_url = redis_url
    _audit_app_name = app_name


def get_db_manager():
    """Get the database manager instance."""
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Set database_url in ServiceConfig.")
    return _db_manager


class AuditWrappedConnection:
    """Wraps a database connection to automatically log audit events to Redis."""
    
    def __init__(self, conn, redis_url: str, app_name: str):
        self._conn = conn
        self._redis_url = redis_url
        self._app = app_name
        self._redis = None
    
    async def _get_redis(self):
        """Lazy-init Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url)
            except ImportError:
                pass  # No redis library
        return self._redis
    
    def __getattr__(self, name):
        return getattr(self._conn, name)
    
    async def save_entity(self, table, data, **kwargs):
        """Save entity and push audit event."""
        # Get old value for diff (if update)
        old = None
        entity_id = data.get("id")
        if entity_id:
            try:
                old = await self._conn.get_entity(table, entity_id)
            except:
                pass
        
        # Actual save
        result = await self._conn.save_entity(table, data, **kwargs)
        
        # Push audit event (fire and forget)
        redis = await self._get_redis()
        if redis:
            try:
                from ..audit.publisher import push_audit_event
                action = "update" if old else "create"
                await push_audit_event(
                    redis,
                    action=action,
                    entity=table,
                    entity_id=result.get("id", entity_id),
                    old=old,
                    new=result,
                    app=self._app,
                )
            except:
                pass  # Never fail the save
        
        return result
    
    async def delete_entity(self, table, entity_id, **kwargs):
        """Delete entity and push audit event."""
        # Get snapshot before delete
        old = None
        try:
            old = await self._conn.get_entity(table, entity_id)
        except:
            pass
        
        # Actual delete
        result = await self._conn.delete_entity(table, entity_id, **kwargs)
        
        # Push audit event
        redis = await self._get_redis()
        if redis and old:
            try:
                from ..audit.publisher import push_audit_event
                await push_audit_event(
                    redis,
                    action="delete",
                    entity=table,
                    entity_id=entity_id,
                    old=old,
                    new=None,
                    app=self._app,
                )
            except:
                pass
        
        return result


@asynccontextmanager
async def get_db_connection():
    """
    Get a database connection from the pool.
    
    If audit is enabled (REDIS_URL configured), connection is automatically
    wrapped to log all save/delete operations to Redis.
    """
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Set database_url in ServiceConfig.")
    
    async with _db_manager as conn:
        if _audit_redis_url and _audit_app_name:
            yield AuditWrappedConnection(conn, _audit_redis_url, _audit_app_name)
        else:
            yield conn


async def db_connection():
    """FastAPI dependency for database connections."""
    async with get_db_connection() as conn:
        yield conn


async def init_schema(init_fn: Callable[[any], Awaitable[None]]):
    """Initialize database schema using provided function."""
    async with get_db_connection() as db:
        await init_fn(db)


async def close_db():
    """Close database connections on shutdown."""
    global _db_manager
    if _db_manager is not None:
        from ...databases.manager import DatabaseManager
        await DatabaseManager.close_all()
        _db_manager = None
