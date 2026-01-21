import asyncio
from typing import Dict, Any

from .... import log as logger
from ....resilience import retry_with_backoff
from ....utils import async_method

from ...connections import SyncConnection, AsyncConnection
from .generators import SqliteSqlGenerator
from ...config import DatabaseConfig

from ...entity.mixins import EntitySyncMixin, EntityAsyncMixin

# SQLite-specific retry config:
# - busy_timeout is 60s (SQLite waits for locks at DB level) 
# - max_retries=5 with longer delays to outlast lock contention
# - total_timeout=300s allows multiple busy_timeout cycles
# 
# IMPORTANT: SQLite allows only ONE writer at a time even with WAL mode.
# Concurrent writes will queue up. Be patient!
import sqlite3
SQLITE_RETRY_CONFIG = dict(
    max_retries=5,
    base_delay=2.0,        # Start with 2s between retries
    max_delay=30.0,        # Cap at 30s between retries  
    total_timeout=300.0,   # 5 minutes total - allows for heavy contention
    retry_on=(sqlite3.OperationalError, Exception),  # Catch "database is locked"
)


class SqliteSyncConnection(SyncConnection, EntitySyncMixin):
    """
    SQLite implementation of the SyncConnection interface.
    
    This class wraps a raw sqlite3 connection and cursor to provide
    the standardized SyncConnection interface.
    
    Args:
        conn: Raw sqlite3 connection object.
    """
    def __init__(self, conn, config: DatabaseConfig):
        super().__init__(conn, config)
        self._cursor = self._conn.cursor()
        self._sql_generator = None

    @property
    def sql_generator(self) -> SqliteSqlGenerator:
        """Returns the SQL parameter converter."""
        if not self._sql_generator:
            self._sql_generator = SqliteSqlGenerator()
        return self._sql_generator

    @retry_with_backoff(**SQLITE_RETRY_CONFIG)
    def _prepare_statement_sync(self, native_sql: str) -> Any:
        """
        SQLite with sqlite3 doesn't have a separate prepare API,
        so we just return the SQL for later execution.
        """
        return native_sql  # Just return the SQL string
    
    @retry_with_backoff(**SQLITE_RETRY_CONFIG)
    def _execute_statement_sync(self, statement: Any, params=None) -> Any:
        """
        Execute a statement using sqlite3.
        
        Note: busy_timeout (60s) handles lock waiting at the database level.
        This retry decorator (90s total) allows additional retries if needed.
        """
        self._cursor.execute(statement, params or ())
        return self._cursor.fetchall()
        
    def in_transaction(self) -> bool:
        """Return True if connection is in an active transaction.""" 
        return self._conn.in_transaction

    def begin_transaction(self):
        """
        Begins a database transaction.
        
        After calling this method, subsequent queries will be part of the transaction
        until either commit_transaction() or rollback_transaction() is called.
        """
        self._conn.execute("BEGIN")

    def commit_transaction(self):
        """
        Commits the current transaction.
        
        This permanently applies all changes made since begin_transaction() was called.
        """
        self._conn.commit()

    def rollback_transaction(self):
        """
        Rolls back the current transaction.
        
        This discards all changes made since begin_transaction() was called.
        """
        self._conn.rollback()

    def close(self):
        """
        Closes the database connection and cursor.
        
        This releases all resources used by the connection. The connection
        should not be used after calling this method.
        """
        self._cursor.close()
        self._conn.close()

    def get_version_details(self) -> Dict[str, str]:
        """ Returns {'db_server_version', 'db_driver'} """
        cursor = self._conn.cursor()
        cursor.execute("SELECT sqlite_version();")
        server_version = cursor.fetchone()[0]

        import sqlite3
        driver_version = f"sqlite3 {sqlite3.sqlite_version}"

        return {          
            "db_server_version": server_version,
            "db_driver": driver_version
        }
           
class SqliteAsyncConnection(AsyncConnection, EntityAsyncMixin):
    """
    SQLite implementation of the AsyncConnection interface.
    
    This class wraps a raw aiosqlite connection to provide the standardized
    AsyncConnection interface, including transaction management.
    
    Args:
        conn: Raw aiosqlite connection object.
    """
    def __init__(self, conn, config: DatabaseConfig):
        super().__init__(conn, config) 
        self._sql_generator = None

    @property
    def sql_generator(self) -> SqliteSqlGenerator:
        """Returns the SQL parameter converter."""
        if not self._sql_generator:
            self._sql_generator = SqliteSqlGenerator()
        return self._sql_generator
  
    @retry_with_backoff(**SQLITE_RETRY_CONFIG)
    async def _prepare_statement_async(self, native_sql: str) -> Any:
        """
        SQLite with aiosqlite doesn't have a separate prepare API.
        """       
        return native_sql
    
    @retry_with_backoff(**SQLITE_RETRY_CONFIG)
    async def _execute_statement_async(self, statement: Any, params=None) -> Any:
        """
        Execute a prepared statement using aiosqlite.
        
        Note: busy_timeout (60s) handles lock waiting at the database level.
        This retry decorator (90s total) allows additional retries if needed.
        """
        async with self._conn.execute(statement, params or ()) as cursor:
            return await cursor.fetchall()

    @async_method
    async def in_transaction(self) -> bool:
        """Return True if connection is in an active transaction.""" 
        # aiosqlite allows checking transaction status via in_transaction property
        return self._conn.in_transaction

    @async_method
    async def begin_transaction(self):
        """
        Asynchronously begins a database transaction.
        
        After calling this method, subsequent queries will be part of the transaction
        until either commit_transaction_async() or rollback_transaction_async() is called.
        """
        await self._conn.execute("BEGIN")

    @async_method
    async def commit_transaction(self):
        """
        Asynchronously commits the current transaction.
        
        This permanently applies all changes made since begin_transaction_async() was called.
        """
        await self._conn.commit()

    @async_method
    async def rollback_transaction(self):
        """
        Asynchronously rolls back the current transaction.
        
        This discards all changes made since begin_transaction_async() was called.
        """
        await self._conn.rollback()

    @async_method
    async def close(self):
        """
        Asynchronously closes the database connection.
        
        This releases any resources used by the connection. The connection
        should not be used after calling this method.
        """
        await self._conn.close()       

    @async_method
    async def get_version_details(self) -> Dict[str, str]:
        """ Returns {'db_server_version', 'db_driver'} """
        async with self._conn.execute("SELECT sqlite_version();") as cursor:
            row = await cursor.fetchone()
            server_version = row[0]

        import aiosqlite
        driver_version = f"aiosqlite {aiosqlite.__version__}"

        return {
            "db_server_version": server_version,
            "db_driver": driver_version
        }
 