"""
DatabaseManager - Lightweight wrapper for database lifecycle management.

Handles configuration and connection acquisition. Pool cleanup is explicit via close_all().
"""

import contextlib
from typing import Optional, Dict, Any, Union

from .config import DatabaseConfig
from .factory import DatabaseFactory
from .database import ConnectionManager
from .pools import PoolManager
from .connections import SyncConnection, AsyncConnection


class DatabaseManager:
    """
    Lightweight database manager for simplified connection handling.
    
    Combines configuration, factory, and connection context in one call.
    Works with both async and sync contexts via the same connect() method.
    
    Usage (Async - FastAPI, etc.):
        @app.get("/users")
        async def get_users():
            async with DatabaseManager.connect("postgres", database="mydb") as conn:
                return await conn.find_entities("users")
            # Connection released to pool
    
    Usage (Sync):
        with DatabaseManager.connect("sqlite", database="./app.db") as conn:
            conn.save_entity("logs", {"msg": "started"})
        # Connection released
    
    Usage (Transaction):
        async with DatabaseManager.connect("postgres", database="mydb") as conn:
            async with conn.transaction():
                await conn.save_entity("orders", {"total": 99})
                await conn.save_entity("payments", {"amount": 99})
    
    App shutdown (FastAPI lifespan):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            await DatabaseManager.close_all()
        
        app = FastAPI(lifespan=lifespan)
    """
    
    DEFAULT_PORTS: Dict[str, Optional[int]] = {
        "postgres": 5432,
        "mysql": 3306,
        "sqlite": None,
    }
    
    def __init__(
        self,
        db_type: str,
        config: Optional[DatabaseConfig] = None,
        *,
        database: str = None,
        host: str = "localhost",
        port: int = None,
        user: str = None,
        password: str = None,
        alias: str = None,
        env: str = "prod",
        connection_acquisition_timeout: float = 10.0,
        pool_creation_timeout: float = 30.0,
        query_execution_timeout: float = 60.0,
        connection_creation_timeout: float = 15.0,
        migrations_on: bool = True,
    ):
        self._db_type = db_type.lower()
        
        if port is None:
            port = self.DEFAULT_PORTS.get(self._db_type, 5432)
        
        if config:
            self._config = config
        else:
            self._config = DatabaseConfig(
                database=database,
                host=host,
                port=port,
                user=user,
                password=password,
                alias=alias or database,
                env=env,
                connection_acquisition_timeout=connection_acquisition_timeout,
                pool_creation_timeout=pool_creation_timeout,
                query_execution_timeout=query_execution_timeout,
                connection_creation_timeout=connection_creation_timeout,
                migrations_on=migrations_on,
            )
        
        self._db: ConnectionManager = DatabaseFactory.create_database(self._db_type, self._config)
        self._async_cm = None  # For backwards-compat __aenter__/__aexit__ (single-caller only)
        self._sync_conn: Optional[SyncConnection] = None
    
    # region ---- Factory ----
    
    @classmethod
    def connect(cls, db_type: str, **kwargs) -> "DatabaseManager":
        """
        Create a connection context.
        
        Use with `async with` for async connections, `with` for sync connections.
        
        Args:
            db_type: Database type ('postgres', 'mysql', 'sqlite')
            **kwargs: Connection parameters (database, host, port, user, password, 
                      alias, env, timeouts) or config=DatabaseConfig instance
        
        Returns:
            DatabaseManager that works as both async and sync context manager
        """
        return cls(db_type, **kwargs)
    
    @classmethod
    def from_config(cls, db_type: str, config: DatabaseConfig) -> "DatabaseManager":
        """Create from existing DatabaseConfig."""
        return cls(db_type, config=config)
    
    # endregion
    
    # region ---- Properties ----
    
    @property
    def config(self) -> DatabaseConfig:
        return self._config
    
    @property
    def hash(self) -> str:
        return self._config.hash()
    
    # endregion
    
    # region ---- Async Context Manager ----
    #
    # WARNING: DatabaseManager is often used as a shared, module-level singleton
    # (e.g. app_kernel's _db_manager). The __aenter__/__aexit__ pattern CANNOT
    # safely store per-call state on self because concurrent callers would
    # overwrite each other's connections.
    #
    # Instead, use connection() which returns a proper per-call context manager,
    # or use __aenter__/__aexit__ ONLY for single-caller scenarios (scripts, tests).
    #
    # For concurrent use (FastAPI, workers), always use:
    #     async with db_manager.connection() as conn: ...
    
    @contextlib.asynccontextmanager
    async def connection(self):
        """
        Get a connection from the pool. Safe for concurrent use.
        
        This is the recommended way to get connections from a shared DatabaseManager.
        Each call gets its own connection, properly released on exit.
        
        Usage:
            async with db_manager.connection() as conn:
                await conn.find_entities("users")
        """
        async with self._db.async_connection() as conn:
            yield conn
    
    async def __aenter__(self) -> AsyncConnection:
        # For backwards compatibility. Safe ONLY when DatabaseManager is not shared.
        # For shared instances, use db_manager.connection() instead.
        self._async_cm = self._db.async_connection()
        return await self._async_cm.__aenter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        cm = getattr(self, '_async_cm', None)
        if cm:
            await cm.__aexit__(exc_type, exc_val, exc_tb)
            self._async_cm = None
    
    # endregion
    
    # region ---- Sync Context Manager ----
    
    def __enter__(self) -> SyncConnection:
        self._sync_conn = self._db.get_sync_connection()
        return self._sync_conn
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._sync_conn:
            self._db.release_sync_connection()
            self._sync_conn = None
    
    # endregion
    
    # region ---- Pool Management ----
    
    @classmethod
    async def close_all(cls, timeout: float = 60.0) -> None:
        """
        Close all connection pools. Call at app shutdown.
        
        Args:
            timeout: Seconds to wait for graceful shutdown
        """
        await PoolManager.close_pool(config_hash=None, timeout=timeout)
    
    @classmethod
    async def close_pool(cls, config_hash: str, timeout: float = 30.0) -> None:
        """
        Close a specific pool by config hash.
        
        Args:
            config_hash: Hash from DatabaseConfig.hash()
            timeout: Seconds to wait for graceful shutdown
        """
        await PoolManager.close_pool(config_hash=config_hash, timeout=timeout)
    
    # endregion
    
    # region ---- Diagnostics ----
    
    @classmethod
    def get_pool_metrics(cls, config_hash: Optional[str] = None) -> Dict[str, Any]:
        """Get metrics for specific or all pools."""
        return PoolManager.get_pool_metrics(config_hash)
    
    # endregion
    
    def __repr__(self) -> str:
        return f"<DatabaseManager({self._db_type}) alias='{self._config.alias()}'>"