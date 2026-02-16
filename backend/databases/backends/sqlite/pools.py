import asyncio
from typing import Any, Optional
import aiosqlite

from ....utils import async_method

from ...config import DatabaseConfig
from ...pools import ConnectionPool, PoolManager


class SqliteConnectionPool(ConnectionPool):
    """
    SQLite multi-connection pool using WAL mode for concurrent access.
    
    With WAL (Write-Ahead Logging) mode, SQLite supports:
    - Multiple concurrent readers
    - One writer (others wait via busy_timeout)
    - Readers don't block writers and vice versa
    
    This pool maintains multiple connections to allow concurrent FastAPI requests.
    
    Attributes:
        _db_path: Path to the SQLite database file
        _available: Queue of available connections
        _in_use_conns: Set of connections currently checked out
        _min_size: Minimum pool size
        _max_size: Maximum pool size
        _lock: Lock for pool operations
        _closed: Whether the pool has been closed
    """
    
    def __init__(self, db_path: str, min_size: int = 2, max_size: int = 10):
        """
        Initialize a SQLite connection pool.
        
        Args:
            db_path: Path to the SQLite database file
            min_size: Minimum number of connections to maintain
            max_size: Maximum number of connections allowed
        """
        self._db_path = db_path
        self._available: asyncio.Queue = asyncio.Queue()
        self._in_use_conns: set = set()
        self._all_conns: list = []
        self._min_size = min_size
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._closed = False
    
    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new SQLite connection with optimal settings."""
        conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        
        # WAL mode for concurrent access (readers don't block writers)
        await conn.execute("PRAGMA journal_mode=WAL")
        
        # Wait up to 5 seconds for locks instead of failing immediately
        # Kept short because with autocommit, locks are held only per-statement
        await conn.execute("PRAGMA busy_timeout=5000")
        
        # NORMAL sync is good balance of safety and speed
        await conn.execute("PRAGMA synchronous=NORMAL")
        
        # Enable foreign keys
        await conn.execute("PRAGMA foreign_keys=ON")
        
        return conn
    
    async def initialize(self) -> None:
        """Initialize the pool with minimum connections."""
        for _ in range(self._min_size):
            conn = await self._create_connection()
            self._all_conns.append(conn)
            await self._available.put(conn)
    
    @async_method
    async def acquire(self, timeout: Optional[float] = None) -> Any:
        """
        Acquire a connection from the pool.
        
        Args:
            timeout: Maximum time to wait for a connection (default 10s)
            
        Returns:
            A SQLite connection
            
        Raises:
            TimeoutError: If no connection available within timeout
            RuntimeError: If pool is closed
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")
            
        timeout = timeout if timeout is not None else 10.0
        
        try:
            # Try to get from available queue (non-blocking first)
            try:
                conn = self._available.get_nowait()
                self._in_use_conns.add(conn)
                return conn
            except asyncio.QueueEmpty:
                pass
            
            # Check if we can create a new connection
            async with self._lock:
                if len(self._all_conns) < self._max_size:
                    conn = await self._create_connection()
                    self._all_conns.append(conn)
                    self._in_use_conns.add(conn)
                    return conn
            
            # Wait for an available connection
            conn = await asyncio.wait_for(self._available.get(), timeout=timeout)
            self._in_use_conns.add(conn)
            return conn
            
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timeout acquiring connection from {self._db_path} pool after {timeout:.2f}s "
                f"(pool: {len(self._in_use_conns)}/{self._max_size} in use)"
            )
    
    @async_method
    async def release(self, connection: Any) -> None:
        """
        Release a connection back to the pool.
        Commits any open transaction as safety net.
        
        Args:
            connection: The connection to release
        """
        if self._closed:
            try:
                await connection.close()
            except Exception:
                pass
            return
            
        # Safety: commit any lingering transaction before returning to pool
        try:
            if connection.in_transaction:
                await connection.commit()
        except Exception:
            pass
        
        if connection in self._in_use_conns:
            self._in_use_conns.discard(connection)
            await self._available.put(connection)
    
    @async_method
    async def close(self, timeout: Optional[float] = None) -> None:
        """
        Close all connections in the pool.
        
        Args:
            timeout: Maximum time to wait for in-use connections
        """
        self._closed = True
        
        # Close all connections
        for conn in self._all_conns:
            try:
                await conn.close()
            except Exception:
                pass
        
        self._all_conns.clear()
        self._in_use_conns.clear()
        
        # Clear the queue
        while not self._available.empty():
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    async def _test_connection(self, connection) -> None:
        """Test that a connection is valid."""
        await connection.execute("SELECT 1")
    
    @property
    def min_size(self) -> int:
        """Minimum pool size."""
        return self._min_size
    
    @property
    def max_size(self) -> int:
        """Maximum pool size."""
        return self._max_size
    
    @property
    def size(self) -> int:
        """Current total connections (in use + idle)."""
        return len(self._all_conns)
    
    @property
    def in_use(self) -> int:
        """Number of connections currently checked out."""
        return len(self._in_use_conns)
    
    @property
    def idle(self) -> int:
        """Number of connections available in the pool."""
        return self._available.qsize()


class SqlitePoolManager(PoolManager):
    """Pool manager for SQLite databases."""
    
    async def _create_pool(self, config: DatabaseConfig) -> ConnectionPool:
        """
        Create a SQLite connection pool.
        
        Pool size is determined by config or defaults:
        - min_size: 2 (keep some connections warm)
        - max_size: 10 (reasonable for SQLite with WAL)
        """
        db_path = config.config()["database"]
        
        # Get pool size from config or use sensible defaults for SQLite
        # SQLite with WAL can handle multiple readers, but writes serialize
        min_size = getattr(config, 'pool_min_size', None) or 2
        max_size = getattr(config, 'pool_max_size', None) or 10
        
        pool = SqliteConnectionPool(
            db_path=db_path,
            min_size=min_size,
            max_size=max_size,
        )
        
        # Initialize with minimum connections
        await pool.initialize()
        
        return pool
    
