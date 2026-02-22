import asyncio
import time
import random
from typing import Dict, Any, List, Tuple, Optional

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
    exceptions=(sqlite3.OperationalError, Exception),  # Catch "database is locked"
)

# Lock retry config used by async execute/executemany overrides.
# These retries wrap the ENTIRE execute (including its internal timeout),
# so backoff sleeps are never killed by asyncio.wait_for.
_LOCK_RETRY_MAX       = SQLITE_RETRY_CONFIG['max_retries']
_LOCK_RETRY_BASE      = SQLITE_RETRY_CONFIG['base_delay']
_LOCK_RETRY_MAX_DELAY = SQLITE_RETRY_CONFIG['max_delay']
_LOCK_RETRY_TIMEOUT   = SQLITE_RETRY_CONFIG['total_timeout']


def _is_lock_error(exc: Exception) -> bool:
    """Check if an exception is a SQLite lock contention error."""
    msg = str(exc).lower()
    return 'database is locked' in msg or 'database is busy' in msg


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
        
        Sync path: time.sleep() can't be cancelled by asyncio, so retry
        decorator works correctly here (unlike the async path).
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
        
        Safety: skips BEGIN if already in a transaction (defense-in-depth).
        """
        if self._conn.in_transaction:
            return
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
    
    IMPORTANT — retry layering:
    The base class execute() wraps _execute_statement_async in
    execute_with_timeout (asyncio.wait_for). If retry lives INSIDE that
    timeout, the backoff sleeps get cancelled instantly on timeout.
    
    Fix: retry is on execute()/executemany() (OUTSIDE the timeout), so each
    attempt gets its own fresh timeout and backoff sleeps are never cancelled.
    _execute_statement_async is a plain single-attempt call.
    
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
  
    async def _prepare_statement_async(self, native_sql: str) -> Any:
        """
        SQLite with aiosqlite doesn't have a separate prepare API.
        """       
        return native_sql
    
    async def _execute_statement_async(self, statement: Any, params=None) -> Any:
        """
        Execute a prepared statement using aiosqlite (single attempt).
        
        No retry decorator — retry lives at execute() level so backoff
        sleeps are outside the timeout wrapper and can't be cancelled.
        """
        async with self._conn.execute(statement, params or ()) as cursor:
            return await cursor.fetchall()

    # -----------------------------------------------------------------
    # Retry-outside-timeout overrides
    # -----------------------------------------------------------------

    async def _with_lock_retry(self, coro_fn, *args, **kwargs):
        """
        Retry wrapper for SQLite lock contention.
        
        Calls coro_fn(*args, **kwargs) which is expected to be
        super().execute() or super().executemany(). Those methods
        internally use execute_with_timeout, so each attempt gets
        its own timeout. The backoff sleep here lives OUTSIDE any
        timeout wrapper and cannot be cancelled by asyncio.wait_for.
        
        Only retries on 'database is locked' / 'database is busy'.
        All other errors propagate immediately.
        """
        retries = 0
        delay = _LOCK_RETRY_BASE
        start = time.monotonic()
        last_exc = None

        while True:
            # Total timeout guard
            elapsed = time.monotonic() - start
            if _LOCK_RETRY_TIMEOUT and elapsed > _LOCK_RETRY_TIMEOUT:
                logger.warning(
                    f"SQLite lock retry timeout ({_LOCK_RETRY_TIMEOUT}s) "
                    f"exceeded for {coro_fn.__name__}"
                )
                raise last_exc or TimeoutError(
                    f"SQLite lock retry timed out after {_LOCK_RETRY_TIMEOUT}s"
                )

            try:
                return await coro_fn(*args, **kwargs)

            except Exception as e:
                if not _is_lock_error(e):
                    raise  # Not a lock error — propagate immediately

                last_exc = e
                retries += 1

                if retries > _LOCK_RETRY_MAX:
                    logger.warning(
                        f"SQLite lock retry exhausted ({_LOCK_RETRY_MAX} retries) "
                        f"for {coro_fn.__name__}: {e}"
                    )
                    raise

                # Exponential backoff with jitter — this sleep is SAFE
                # because it's outside any asyncio.wait_for wrapper
                jitter = random.uniform(0.8, 1.2)
                sleep_time = min(delay * jitter, _LOCK_RETRY_MAX_DELAY)

                # Don't sleep past total timeout
                remaining = _LOCK_RETRY_TIMEOUT - (time.monotonic() - start)
                if remaining < sleep_time:
                    if remaining > 0.1:
                        sleep_time = remaining * 0.9
                    else:
                        raise

                logger.info(
                    f"SQLite locked, retry {retries}/{_LOCK_RETRY_MAX} "
                    f"in {sleep_time:.1f}s: {str(e)[:80]}"
                )
                await asyncio.sleep(sleep_time)
                delay = min(delay * 2, _LOCK_RETRY_MAX_DELAY)

    async def execute(self, sql: str, params: Optional[tuple] = None,
                      timeout: Optional[float] = None,
                      tags: Optional[Dict[str, Any]] = None) -> List[Tuple]:
        """
        SQLite override: retry wraps timeout for proper lock handling.
        
        Each retry attempt calls super().execute() which has its own
        execute_with_timeout internally. Backoff sleeps live outside
        that timeout and can never be cancelled.
        """
        return await self._with_lock_retry(
            super().execute, sql, params, timeout, tags
        )

    async def executemany(self, sql: str, param_list: List[tuple],
                          timeout: Optional[float] = None,
                          tags: Optional[Dict[str, Any]] = None) -> List[Tuple]:
        """
        SQLite override: retry wraps timeout for proper lock handling.
        """
        return await self._with_lock_retry(
            super().executemany, sql, param_list, timeout, tags
        )

    # -----------------------------------------------------------------

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
        
        Safety: skips BEGIN if already in a transaction (defense-in-depth against
        nested transaction errors when @auto_transaction check races with execution).
        """
        if self._conn.in_transaction:
            return
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