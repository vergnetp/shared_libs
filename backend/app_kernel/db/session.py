"""
Database connection management.

Kernel manages the connection pool via DatabaseManager.
Apps provide config (in ServiceConfig) and schema (via schema_init).

Two connection modes:
    db_dependency / db_context         - strict (default for app code)
    raw_db_dependency / raw_db_context - no guard (kernel internals)

Strict mode enforces that all operations go through entity classes
(e.g. User.find(db, ...)) not raw db.find_entities(). This ensures
proper type coercion and prevents backend-specific bugs (e.g. SQLite
string comparison on integer fields).

When REDIS_URL is configured, audit logging is automatically enabled:
- Every save_entity() and delete_entity() pushes an audit event to Redis
- The admin_worker consumes these events and writes to admin_db

Usage:
    # In routes (FastAPI dependency) - strict by default
    from app_kernel.db import db_dependency
    
    @app.get("/deployments")
    async def list_deps(db=Depends(db_dependency)):
        return await Deployment.find(db, where="env = ?", params=("prod",))
    
    # In workers (context manager) - strict by default
    from app_kernel.db import db_context
    
    async with db_context() as db:
        deploys = await Deployment.find(db)
"""
import os
import sys
import time
import logging
from typing import Optional, Callable, Awaitable
from contextlib import asynccontextmanager

logger = logging.getLogger("app_kernel.db")

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
        # Import sentinel for strict entity access bypass
        from ...databases.entity.decorators import _ENTITY_CALLER
        self._entity_caller = _ENTITY_CALLER
    
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
    
    async def save_entity(self, table, data, match_by=None, **kwargs):
        """
        Save entity and push audit event.
        
        Args:
            table: Entity table name
            data: Entity data dict
            match_by: Field(s) to match existing entity by (for upsert without id).
                      Passed through to underlying database save_entity.
            **kwargs: Additional args passed to underlying save
        """
        # Remove _caller from kwargs if present (we provide our own)
        kwargs.pop('_caller', None)
        
        # Get old value for diff (if updating by id)
        old = None
        entity_id = data.get("id")
        if entity_id:
            try:
                old = await self._conn.get_entity(table, entity_id, _caller=self._entity_caller)
            except:
                pass
        
        # Actual save (match_by handled by databases module)
        result = await self._conn.save_entity(table, data, match_by=match_by, _caller=self._entity_caller, **kwargs)
        
        # For match_by without id, we need to check if it was update or create
        if not entity_id and match_by:
            # Result has id now - check if it existed before
            try:
                # If result id matches something that existed, it was an update
                # We already have result, so this was handled by databases module
                pass
            except:
                pass
        
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
        # Remove _caller from kwargs if present (we provide our own)
        kwargs.pop('_caller', None)
        
        # Get snapshot before delete
        old = None
        try:
            old = await self._conn.get_entity(table, entity_id, _caller=self._entity_caller)
        except:
            pass
        
        # Actual delete
        result = await self._conn.delete_entity(table, entity_id, _caller=self._entity_caller, **kwargs)
        
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


# =============================================================================
# Connection providers
# =============================================================================

def _get_caller() -> str:
    """Walk up frames to find the first caller outside session.py/contextlib."""
    try:
        for i in range(1, 15):
            f = sys._getframe(i)
            fn = f.f_code.co_filename
            if ('session.py' not in fn and 'contextlib' not in fn 
                and 'decorators.py' not in fn):
                short = os.path.basename(fn)
                return f"{short}:{f.f_lineno} {f.f_code.co_name}"
    except (ValueError, AttributeError):
        pass
    return "unknown"


def _pool_stats() -> str:
    """Get pool stats string. Returns empty string if pool not accessible."""
    try:
        pool = _db_manager._db.pool_manager._pool
        if pool:
            return f"pool: {pool.idle} idle, {pool.in_use} in_use, {pool.size}/{pool.max_size} total"
    except (AttributeError, TypeError):
        pass
    return ""


@asynccontextmanager
async def _base_connection():
    """
    Internal: get a raw connection from the pool, with audit wrapping if enabled.
    All public connection providers build on this.
    
    Uses _db_manager.connection() (not `async with _db_manager`) to ensure
    each caller gets its own connection — safe for concurrent requests.
    """
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Set database_url in ServiceConfig.")
    
    caller = _get_caller()
    stats = _pool_stats()
    logger.debug(f"DB acquire [{caller}] {stats}")
    t0 = time.monotonic()
    
    async with _db_manager.connection() as conn:
        if _audit_redis_url and _audit_app_name:
            yield AuditWrappedConnection(conn, _audit_redis_url, _audit_app_name)
        else:
            yield conn
    
    held = time.monotonic() - t0
    stats = _pool_stats()
    level = logging.WARNING if held > 30 else logging.DEBUG
    logger.log(level, f"DB release [{caller}] held {held:.2f}s {stats}")


# --- Strict (default for app code) ---

@asynccontextmanager
async def db_context():
    """
    Context manager for database connections (strict, default for app code).
    
    Enforces entity class usage: db.find_entities() etc will raise RuntimeError.
    Use MyEntity.find(db, ...) instead.
    
    Usage:
        async with db_context() as db:
            deploys = await Deployment.find(db, where="env = ?", params=("prod",))
    """
    async with _base_connection() as conn:
        conn._strict_entity_access = True
        conn._block_raw_execute = True
        yield conn


async def db_dependency():
    """
    FastAPI dependency for database connections (strict, default for app code).
    
    Enforces entity class usage: db.find_entities() etc will raise RuntimeError.
    Use MyEntity.find(db, ...) instead.
    
    Usage:
        from app_kernel.db import db_dependency
        
        @app.get("/deployments")
        async def list_deployments(db=Depends(db_dependency)):
            return await Deployment.find(db, where="env = ?", params=("prod",))
    """
    async with db_context() as conn:
        yield conn


# --- Raw (kernel internals, power users) ---

@asynccontextmanager
async def raw_db_context():
    """
    Context manager for database connections WITHOUT strict entity access.
    
    Used by kernel internal stores that don't have @entity schemas.
    App code should use db_context() instead.
    """
    async with _base_connection() as conn:
        yield conn


async def raw_db_dependency():
    """
    FastAPI dependency for database connections WITHOUT strict entity access.
    
    Used by kernel internal stores that don't have @entity schemas.
    App code should use db_dependency() instead.
    """
    async with raw_db_context() as conn:
        yield conn


# =============================================================================
# Schema init / shutdown
# =============================================================================

async def init_schema(init_fn: Callable[[any], Awaitable[None]]):
    """Initialize database schema using provided function."""
    async with raw_db_context() as db:
        await init_fn(db)


async def close_db():
    """Close database connections on shutdown."""
    global _db_manager
    if _db_manager is not None:
        from ...databases.manager import DatabaseManager
        await DatabaseManager.close_all()
        _db_manager = None


# =============================================================================
# Backward compatibility aliases (kernel code uses old names)
# =============================================================================

# Old name -> new name:
#   get_db_connection -> raw_db_context   (context manager, no guard)
#   db_connection     -> db_dependency    (FastAPI dep, strict)
get_db_connection = raw_db_context
db_connection = db_dependency