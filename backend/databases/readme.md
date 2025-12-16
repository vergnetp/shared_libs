# Database Abstraction Layer

A unified database abstraction layer supporting PostgreSQL, MySQL, and SQLite with connection pooling, async/sync support, automatic schema evolution, and entity versioning.

---

## Quick Start

The simplest way to use the database layer is through `DatabaseManager`:

```python
from databases import DatabaseManager

# Async (FastAPI, async apps)
async with DatabaseManager.connect("postgres", database="mydb", user="admin", password="secret") as conn:
    # Full AsyncConnection API available
    user = await conn.save_entity("users", {"name": "Alice", "age": 30})
    users = await conn.find_entities("users", where_clause="[age] > ?", params=(25,))

# Sync (scripts, CLI tools)
with DatabaseManager.connect("sqlite", database="./app.db") as conn:
    conn.save_entity("logs", {"message": "App started"})
```

### With Transactions

```python
async with DatabaseManager.connect("postgres", database="mydb") as conn:
    async with conn.transaction():
        await conn.save_entity("orders", {"total": 99.99})
        await conn.save_entity("payments", {"amount": 99.99})
    # Auto-committed, or rolled back on exception
```

### FastAPI Integration

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from databases import DatabaseManager

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await DatabaseManager.close_all()  # Clean shutdown

app = FastAPI(lifespan=lifespan)

@app.get("/users")
async def get_users():
    async with DatabaseManager.connect("postgres", database="mydb", user="admin", password="secret") as conn:
        return await conn.find_entities("users", deserialize=True)

@app.post("/users")
async def create_user(name: str, age: int):
    async with DatabaseManager.connect("postgres", database="mydb", user="admin", password="secret") as conn:
        return await conn.save_entity("users", {"name": name, "age": age})
```

---

## Features

- **Multiple backends**: PostgreSQL, MySQL, SQLite (extensible to Oracle, etc.)
- **Async & Sync**: Same API for both async and sync usage
- **Connection pooling**: Automatic pool management with health checks
- **Entity framework**: Auto-schema evolution, versioning, soft deletes
- **Transactions**: Explicit control via `conn.transaction()` context manager
- **Resilience**: Circuit breakers, retries, timeouts
- **SQL abstraction**: `?` placeholders and `[column]` escaping work across backends

---

## Alternative Usage (Lower-Level API)

If you need more control, you can use `DatabaseConfig`, `DatabaseFactory`, and `PoolManager` directly:

```python
from databases import DatabaseConfig, DatabaseFactory, PoolManager

config = DatabaseConfig(
    database="my_database",
    host="localhost",
    port=5432,
    user="postgres",
    password="secret",
    alias="main_db",
    env="dev",
    connection_acquisition_timeout=10.0,
    pool_creation_timeout=30.0,
    query_execution_timeout=60.0,
    connection_creation_timeout=15.0
)

db = DatabaseFactory.create_database("postgres", config)

async with db.async_connection() as conn:   
    await conn.begin_transaction()    
    try:
        # Entity operations with automatic schema management
        users = await conn.save_entities("users", [
            {'name': 'Phil', 'age': 30},
            {'name': 'Karen', 'surname': 'Brown', 'age': 25}
        ])
        uid = users[0].get('id', None)
        
        # Direct SQL execution
        result = await conn.execute(
            "SELECT * FROM [users] WHERE [id] = ?", 
            (uid,),
            timeout=5.0,
            tags={"operation": "get_user"}
        ) 
        
        # Update entity with new fields (schema evolves automatically)
        await conn.save_entity("users", {
            'id': uid, 
            'name': 'Bob', 
            'age': 24,
            'department': 'Engineering'  # New field added automatically
        })
        
        # Get with automatic deserialization
        result = await conn.get_entity("users", uid, deserialize=True)
        
        # Query with filtering and pagination
        engineers = await conn.find_entities(
            "users",
            where_clause="[department] = ?",
            params=("Engineering",),
            order_by="[name] ASC",
            limit=10,
            deserialize=True
        )
        
        # View complete change history
        history = await conn.get_entity_history("users", uid, deserialize=False)
        
        # Restore previous version
        old_version = await conn.get_entity_by_version("users", uid, 1)        
        await conn.save_entity("users", old_version)        
        
        result = await conn.get_entity("users", uid)
        
        await conn.commit_transaction()
    except Exception:
        await conn.rollback_transaction()
        raise

# Don't forget to close the pool at shutdown!
await PoolManager.close_pool(config_hash=config.hash(), timeout=30)
```

---

## SQL Conventions

This layer works with multiple database backends. To write portable, safe SQL:

### Table/Column Names

* **Universal**: Use `[column_name]` with square brackets
* **Native**: Or use database-specific quoting (`"name"` PostgreSQL, `` `name` `` MySQL)

```python
# Recommended - works on all backends
await conn.execute("SELECT [id], [name] FROM [users] WHERE [status] = ?", ("active",))

# Also works - native PostgreSQL
await conn.execute('SELECT "id", "name" FROM "users" WHERE "status" = $1', ("active",))
```

### Parameters

* **Universal**: Use `?` for all value placeholders
* **Native**: Or use database-specific placeholders (`$1` PostgreSQL, `%s` MySQL)

The system automatically translates between formats, handling SQL keywords safely and preventing injection attacks.

**Need a literal `?` in your SQL?** Use double question marks `??`.

---

## MySQL Transaction Caveat

> **Warning:**  
> MySQL auto-commits DDL (`CREATE`, `ALTER`, `DROP`) even inside transactions.  
> This means preceding SQL in the transaction cannot be rolled back — unlike PostgreSQL.

---

## Entity Framework

The system includes a powerful Entity Framework that provides:

- **Automatic schema creation** - Tables, metadata, and history tables created on first use
- **Dynamic schema evolution** - New fields added automatically when entities are saved
- **Type inference and serialization** - Handles complex Python types (datetime, dict, list, etc.)
- **Versioning and history** - Complete audit trail of all changes
- **Soft deletes** - Mark records as deleted without losing data
- **Flexible querying** - Rich query interface with filtering, sorting, pagination
- **Batch operations** - Efficient bulk insert/update operations

### Entity Usage Examples

```python
async with DatabaseManager.connect("postgres", database="mydb") as conn:
    # Create - schema created automatically
    user = await conn.save_entity("users", {
        "name": "John Doe",
        "email": "john@example.com", 
        "preferences": {"theme": "dark", "language": "en"},  # Complex types handled automatically
        "created_date": datetime.now()
    })
    uid = user["id"]  # Auto-generated UUID
    
    # Read
    user = await conn.get_entity("users", uid, deserialize=True)
    
    # Update - new fields added automatically
    await conn.save_entity("users", {
        "id": uid,
        "department": "Engineering"  # New column added automatically
    })
    
    # Query with filtering
    active_users = await conn.find_entities(
        "users",
        where_clause="[status] = ? AND [created_date] > ?",
        params=("active", datetime.now() - timedelta(days=30)),
        order_by="[created_date] DESC",
        limit=50,
        deserialize=True
    )
    
    # Update with history tracking
    await conn.save_entity("users", {
        "id": uid,
        "name": "John Smith",   # Name changed
        "status": "premium"     # New field added automatically
    }, user_id="admin", comment="Upgraded to premium")
    
    # View complete history
    history = await conn.get_entity_history("users", uid)
    
    # Restore previous version
    old_version = await conn.get_entity_by_version("users", uid, version=1)
    await conn.save_entity("users", old_version)
    
    # Count
    count = await conn.count_entities("users", where_clause="[age] > ?", params=(30,))
    
    # Soft delete (sets deleted_at)
    await conn.delete_entity("users", uid)
    
    # Restore soft-deleted
    await conn.restore_entity("users", uid)
    
    # Permanent delete
    await conn.delete_entity("users", uid, permanent=True)
```

### Direct SQL Execution

```python
async with DatabaseManager.connect("postgres", database="mydb") as conn:
    # Single query with timeout and tags
    result = await conn.execute(
        "SELECT * FROM [users] WHERE [age] > ?",
        (25,),
        timeout=5.0,
        tags={"operation": "find_adults"}
    )
    
    # Batch execution
    results = await conn.executemany(
        "INSERT INTO [logs] ([message], [level]) VALUES (?, ?)",
        [
            ("User logged in", "INFO"),
            ("Action completed", "DEBUG"),
        ],
        timeout=10.0
    )
```

---

## Configuration and Timeouts

The system provides comprehensive timeout configuration:

```python
async with DatabaseManager.connect(
    "postgres",
    database="mydb",
    host="localhost",
    port=5432,                              # Default: 5432 (postgres), 3306 (mysql), 0 (sqlite)
    user="admin",
    password="secret",
    alias="main_db",                        # Friendly name for logging
    env="prod",                             # Environment label
    connection_acquisition_timeout=10.0,    # Time to get connection from pool
    pool_creation_timeout=30.0,             # Time to initialize pool
    query_execution_timeout=60.0,           # Default query timeout
    connection_creation_timeout=15.0,       # Time to create connections
) as conn:
    ...
```

Each timeout serves a specific purpose:

| Timeout | Purpose | When it triggers |
|---------|---------|------------------|
| `connection_acquisition_timeout` | How long to wait when the pool is busy | All connections in use, waiting for one to free up |
| `pool_creation_timeout` | How long to wait for initial pool setup | First connection, pool initialization |
| `query_execution_timeout` | Default timeout for SQL operations | Slow queries, can be overridden per query |
| `connection_creation_timeout` | How long to wait for individual database connections | Network issues, database overload |

Or use a pre-built config:

```python
from databases import DatabaseConfig, DatabaseManager

config = DatabaseConfig(
    database="mydb",
    host="localhost",
    port=5432,
    user="admin",
    password="secret"
)

async with DatabaseManager.from_config("postgres", config) as conn:
    ...
```

---

## Pool Management

Pools are shared by config hash and persist across `DatabaseManager.connect()` calls:

```python
# These share the same pool (same config)
async with DatabaseManager.connect("postgres", database="mydb") as conn1:
    ...

async with DatabaseManager.connect("postgres", database="mydb") as conn2:
    ...  # Reuses same pool

# Close specific pool
await DatabaseManager.close_pool(config.hash())

# Close all pools (app shutdown)
await DatabaseManager.close_all()

# Get pool metrics
metrics = DatabaseManager.get_pool_metrics()
```

---

## Monitoring and Scaling Signals

### Built-in Metrics

- **Connections**: Acquisition times, success/failures
- **Pools**: Utilization, capacity
- **Cache**: Hit/miss, evictions
- **Errors**: Categorized rates
- **Performance**: Query durations
- **Circuit Breakers**: State changes, failure rates

### Timeout Rates → Scaling Insights

Timeouts directly correlate with concurrency and capacity planning:

| Timeout Rate | Interpretation | Suggested Action |
|--------------|----------------|------------------|
| < 0.1% | Normal load | No action needed |
| 0.1% - 1% | Mild contention | Review slow queries |
| 1% - 5% | High load | Scale vertically (bigger DB/app servers) |
| > 5% | Critical pressure | Scale horizontally (add servers) |

**Timeouts reflect concurrency limits.**  
When users start hitting timeouts, it signals that app servers are maxing out their connections.

To serve more users:

- ✅ Increase app servers → spreads connections
- ✅ Upgrade database → supports more total connections

---

## Scaling Guide

### 1️⃣ Start Simple

Use the default connection pool:

```python
async with DatabaseManager.connect("postgres", database="mydb") as conn:
    await conn.execute(...)
```

### 2️⃣ Tune Pool Sizes

Adjust pool based on workload:

```python
# In your pool configuration
min_size=5      # Minimum connections
max_size=20     # Maximum connections
```

### 3️⃣ Add Read Replicas

Split reads from writes:

```python
# Write to primary
async with DatabaseManager.connect("postgres", database="mydb", host="primary.db") as conn:
    await conn.save_entity(...)

# Read from replica  
async with DatabaseManager.connect("postgres", database="mydb", host="replica.db") as conn:
    await conn.find_entities(...)
```

⚡ Requires code changes for read/write awareness.

### 4️⃣ Connection Poolers (PgBouncer)

Add PgBouncer in front of PostgreSQL for connection multiplexing. This is an **infrastructure change** — minimal code changes required.

#### What PgBouncer Does

- Maintains a pool of real PostgreSQL connections
- Multiplexes many client connections onto fewer database connections
- Reduces PostgreSQL memory usage and connection overhead
- Handles connection surges gracefully

#### Installation

**Docker (recommended for dev/testing):**

```bash
docker run -d \
  --name pgbouncer \
  -p 6432:6432 \
  -e DATABASE_URL="postgres://user:password@postgres-host:5432/mydb" \
  -e POOL_MODE=session \
  -e MAX_CLIENT_CONN=1000 \
  -e DEFAULT_POOL_SIZE=20 \
  edoburu/pgbouncer
```

**Docker Compose:**

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: mydb
    ports:
      - "5432:5432"

  pgbouncer:
    image: edoburu/pgbouncer
    environment:
      DATABASE_URL: postgres://admin:secret@postgres:5432/mydb
      POOL_MODE: session
      MAX_CLIENT_CONN: 1000
      DEFAULT_POOL_SIZE: 20
      MIN_POOL_SIZE: 5
    ports:
      - "6432:6432"
    depends_on:
      - postgres
```

**Ubuntu/Debian:**

```bash
sudo apt-get install pgbouncer

# Edit config
sudo nano /etc/pgbouncer/pgbouncer.ini
```

#### Configuration (pgbouncer.ini)

```ini
[databases]
mydb = host=localhost port=5432 dbname=mydb

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt

# Pool settings
pool_mode = session          # session | transaction | statement
default_pool_size = 20       # Connections per user/database pair
min_pool_size = 5            # Minimum connections to keep open
max_client_conn = 1000       # Max client connections allowed
max_db_connections = 50      # Max connections to PostgreSQL

# Timeouts
server_idle_timeout = 600    # Close idle server connections after 10 min
client_idle_timeout = 0      # Disable client idle timeout
query_timeout = 0            # Disable query timeout (use app-level)
```

**userlist.txt:**

```
"admin" "md5hash_of_password"
```

Generate MD5 hash: `echo -n "passwordusername" | md5sum`

#### Pool Modes

| Mode | Description | Compatibility | Use Case |
|------|-------------|---------------|----------|
| `session` | Connection assigned for entire session | ✅ Full (prepared statements, SET, transactions) | Default, safest |
| `transaction` | Connection assigned per transaction | ⚠️ No prepared statements, no SET | High concurrency, stateless apps |
| `statement` | Connection assigned per statement | ❌ No transactions, no prepared statements | Simple queries only |

**Recommendation:** Use `session` mode with this library (prepared statements are used internally).

#### App Configuration

Point your app to PgBouncer instead of PostgreSQL:

```python
# Before: direct to PostgreSQL
async with DatabaseManager.connect(
    "postgres",
    host="db.example.com",
    port=5432,  # PostgreSQL port
    database="mydb",
    user="admin",
    password="secret"
) as conn:
    ...

# After: through PgBouncer
async with DatabaseManager.connect(
    "postgres",
    host="pgbouncer.example.com",  # PgBouncer host
    port=6432,                      # PgBouncer port
    database="mydb",
    user="admin",
    password="secret"
) as conn:
    ...
```

**Reduce app pool size** — PgBouncer handles multiplexing:

```python
# With PgBouncer, you need fewer app-level connections
# PgBouncer's default_pool_size handles the actual DB connections
DatabaseManager.connect(
    "postgres",
    database="mydb",
    connection_acquisition_timeout=5.0,  # Can be shorter with PgBouncer
    ...
)
```

#### Monitoring PgBouncer

```bash
# Connect to admin console
psql -h localhost -p 6432 -U admin pgbouncer

# Useful commands
SHOW STATS;       # Query statistics
SHOW POOLS;       # Pool status
SHOW CLIENTS;     # Connected clients
SHOW SERVERS;     # Backend connections
SHOW CONFIG;      # Current configuration
```

#### Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| "prepared statement does not exist" | Transaction/statement pool mode | Switch to `session` mode |
| Connection timeouts | Pool exhausted | Increase `default_pool_size` or `max_db_connections` |
| "too many connections" | Client limit reached | Increase `max_client_conn` |
| Slow queries | Pool contention | Check `SHOW POOLS` for waiting clients |

#### MySQL Alternative: ProxySQL

For MySQL, use [ProxySQL](https://proxysql.com/) instead of PgBouncer. Similar concept, different configuration.

```bash
docker run -d --name proxysql -p 6033:6033 -p 6032:6032 proxysql/proxysql
```

### 5️⃣ Horizontal Scaling

Shard data across multiple databases. Handle latency and consistency challenges.

⚡ Requires code updates for replica awareness and conflict resolution.

### 6️⃣ Caching & Async

- Add **caching layers** for frequent reads
- Use **queues and batch processing** for writes
- Accept **eventual consistency** for massive scale

---

## Code Structure

```python
databases/
├── manager.py                   # DatabaseManager (recommended entry point)
├── factory.py                   # DatabaseFactory
├── config/
│   └── database_config.py       # DatabaseConfig
├── connections/
│   ├── async_connection.py      # AsyncConnection (abstract)
│   ├── sync_connection.py       # SyncConnection (abstract)
│   └── connection.py            # Connection base
├── database/
│   └── connection_manager.py    # ConnectionManager (abstract)
├── pools/
│   ├── connection_pool.py       # ConnectionPool (abstract)
│   └── pool_manager.py          # PoolManager (abstract)
├── entity/
│   └── mixins/
│       ├── async_mixin.py       # EntityAsyncMixin
│       ├── sync_mixin.py        # EntitySyncMixin
│       └── utils_mixin.py       # EntityUtilsMixin
├── generators/
│   └── generators.py            # SqlGenerator (abstract)
├── backends/
│   ├── postgres/                # PostgreSQL implementation
│   ├── mysql/                   # MySQL implementation
│   └── sqlite/                  # SQLite implementation
└── utils/
    ├── caching.py               # StatementCache
    └── decorators.py            # @auto_transaction
```

### Inheritance

```python
DatabaseManager                          # High-level entry point
    └── uses ConnectionManager

ConnectionInterface (ABC)
    ├── Connection
    │       ├── AsyncConnection (ABC) ────┬── PostgresAsyncConnection
    │       │                             ├── MysqlAsyncConnection 
    │       │                             └── SqliteAsyncConnection
    │       └── SyncConnection (ABC) ──────┬── PostgresSyncConnection
    │                                      ├── MysqlSyncConnection
    │                                      └── SqliteSyncConnection
    ├── EntityAsyncMixin
    │       └── (mixed into AsyncConnection implementations)
    └── EntitySyncMixin
            └── (mixed into SyncConnection implementations)

ConnectionManager (ABC)
    ├── PostgresDatabase
    ├── MySqlDatabase
    └── SqliteDatabase

ConnectionPool (ABC)
    ├── PostgresConnectionPool
    ├── MySqlConnectionPool
    └── SqliteConnectionPool

PoolManager (ABC)
    ├── PostgresPoolManager
    ├── MySqlPoolManager
    └── SqlitePoolManager

SqlGenerator (ABC)
    ├── PostgresSqlGenerator
    ├── MySqlSqlGenerator
    └── SqliteSqlGenerator

SqlEntityGenerator (ABC)
    └── (implemented by SqlGenerator implementations)

EntityUtilsMixin 
    ├── EntityAsyncMixin
    └── EntitySyncMixin
```

---

## Resilience Features

The system includes comprehensive resilience patterns:

### Circuit Breakers

Automatic protection against cascading failures:

```python
# Circuit breakers are applied automatically to all database operations
# Opens after 5 failures, recovers after 30 seconds
async with DatabaseManager.connect("postgres", database="mydb") as conn:
    result = await conn.execute("SELECT * FROM users")  # Protected by circuit breaker
```

### Retry with Backoff

Automatic retry for transient failures:

```python
# Database operations automatically retry on connection failures
# with exponential backoff (3 retries, up to 10s delay)
```

### Timeout Control

Multiple levels of timeout protection:

```python
# Query-specific timeout
result = await conn.execute("SELECT * FROM big_table", timeout=30.0)

# Connection acquisition timeout (from config)
# Pool creation timeout (from config)
# Individual connection timeout (from config)
```

### Performance Monitoring

Built-in slow query detection and profiling:

```python
# Queries taking longer than 2 seconds are automatically logged
# with full argument and timing information
```

---

## Adding a New Backend

To add a new backend (say Oracle), you need to implement a few classes:

**Steps overview:**
1. `SqlGenerator`: `convert_query_to_native`, `get_timeout_sql`
2. `ConnectionPool`: `__init__`, `acquire`, `release`, `close`, `_test_connection`, `min_size`, `max_size`, `size`, `in_use`, `idle`
3. `PoolManager`: `_create_pool`
4. `AsyncConnection`: `__init__`, `sql_generator`, `_prepare_statement_async`, `_execute_statement_async`, `in_transaction`, `begin_transaction`, `commit_transaction`, `rollback_transaction`, `close`, `get_version_details`
5. `SyncConnection`: `__init__`, `sql_generator`, `_prepare_statement_sync`, `_execute_statement_sync`, `in_transaction`, `begin_transaction`, `commit_transaction`, `rollback_transaction`, `close`, `get_version_details`
6. `ConnectionManager`: `_create_sync_connection`, `_wrap_async_connection`, `_wrap_sync_connection`

**Notes:** 
- In the connection classes, the `sql_generator` property should return an instance of the class defined in Step 1.
- To add the Entity Framework, add `SqlEntityGenerator` to the inheritance of Step 1 and implement all entity SQL methods (`get_upsert_sql`, etc.).
- Add `EntityAsyncMixin` and `EntitySyncMixin` to the connection classes (Steps 4 and 5).

```python
import {oracle_async_driver} as async_driver
import {oracle_sync_driver} as sync_driver

# STEP 1: SQL Generator
class OracleSqlGenerator(SqlGenerator, SqlEntityGenerator):
    def escape_identifier(self, identifier: str) -> str:      
        return f'"{identifier}"'  # Oracle uses double quotes

    def _convert_parameters(self, sql: str, params: Optional[Tuple] = None) -> Tuple[str, Any]:
        new_sql = sql  # Replace "??" as "?" and "?" as Oracle placeholder (e.g. ":1")
        return new_sql, params

    # Entity specific SQL generation:
    def get_upsert_sql(self, entity_name: str, fields: List[str]) -> str:
        pass  # Implement Oracle MERGE statement

    # ... and all other Entity specific SQL generation methods


# STEP 2: Connection Pool
class OracleConnectionPool(ConnectionPool):
    def __init__(self, pool):
        self._pool = pool  # The async Oracle driver pool, created by _create_pool
    
    async def acquire(self, timeout: Optional[float] = None) -> Any:
        timeout = timeout if timeout is not None else self._timeout
        try:
            return await asyncio.wait_for(self._pool.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out waiting for Oracle connection after {timeout}s")

    async def release(self, connection: Any) -> None:
        await self._pool.release(connection)

    async def close(self, timeout: Optional[float] = None) -> None:
        await self._pool.close()

    async def _test_connection(self, connection):
        await connection.execute("SELECT 1 FROM DUAL")
    
    @property
    def min_size(self) -> int:
        return self._pool.min
    
    @property
    def max_size(self) -> int:
        return self._pool.max
    
    @property
    def size(self) -> int:       
        return self._pool.size
    
    @property
    def in_use(self) -> int:      
        return self._pool.size - self._pool.freesize
    
    @property
    def idle(self) -> int:     
        return self._pool.freesize


# STEP 3: Pool Manager
class OraclePoolManager(PoolManager):
    async def _create_pool(self, config: DatabaseConfig) -> OracleConnectionPool:  
        min_size, max_size = self._calculate_pool_size()
        raw_pool = await asyncio.wait_for(
            async_driver.create_pool(
                min=min_size, 
                max=max_size,
                user=config.user(),
                password=config.password(),
                dsn=f"{config.host()}:{config.port()}/{config.database()}",
                timeout=config.connection_creation_timeout
            ),
            timeout=config.pool_creation_timeout
        )
        return OracleConnectionPool(raw_pool)


# STEP 4: Async Connection
class OracleAsyncConnection(AsyncConnection, EntityAsyncMixin):
    def __init__(self, conn, config: DatabaseConfig): 
        super().__init__(conn, config)  
        self._sql_generator = None

    @property
    def sql_generator(self) -> SqlGenerator:
        if not self._sql_generator:
            self._sql_generator = OracleSqlGenerator()
        return self._sql_generator

    async def _prepare_statement_async(self, native_sql: str) -> Any:
        pass  # Implement Oracle statement preparation

    async def _execute_statement_async(self, statement: Any, params=None) -> Any:
        pass  # Implement Oracle statement execution
    
    def in_transaction(self) -> bool:
        pass  # Check Oracle transaction state

    async def begin_transaction(self):
        await self._conn.execute("BEGIN")

    async def commit_transaction(self):
        await self._conn.commit()

    async def rollback_transaction(self):
        await self._conn.rollback()

    async def close(self):
        await self._conn.close()

    async def get_version_details(self) -> Dict[str, str]:
        return {'db_server_version': 'TODO', 'db_driver': 'TODO'}


# STEP 5: Sync Connection
class OracleSyncConnection(SyncConnection, EntitySyncMixin):
    # Similar to the Async version, but synchronous
    pass


# STEP 6: Database (Connection Manager)
class OracleDatabase(ConnectionManager):
    def __init__(self, config: DatabaseConfig): 
        super().__init__(config)  
        self._pool_manager = None

    @property
    def pool_manager(self):    
        if not self._pool_manager:
            self._pool_manager = OraclePoolManager(self.config)
        return self._pool_manager

    def _create_sync_connection(self, config: DatabaseConfig):     
        return sync_driver.connect(
            user=config.user(),
            password=config.password(),
            dsn=f"{config.host()}:{config.port()}/{config.database()}"
        )

    def _wrap_async_connection(self, raw_conn, config: DatabaseConfig):
        return OracleAsyncConnection(raw_conn, config)

    def _wrap_sync_connection(self, raw_conn, config: DatabaseConfig):
        return OracleSyncConnection(raw_conn, config)


# STEP 7: Register in Factory
class DatabaseFactory:
    @staticmethod
    def create_database(db_type: str, db_config: DatabaseConfig):
        if db_type.lower() == 'oracle':
            return OracleDatabase(db_config)
        # ... existing backends


# STEP 8: Register default port
DatabaseManager.DEFAULT_PORTS["oracle"] = 1521


# Then use it!
async with DatabaseManager.connect("oracle", database="ORCL", host="db.example.com", port=1521) as conn:
    await conn.save_entity("users", {"name": "Alice"})
```

---

## 📖 Public API

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DatabaseManager`

High-level entry point for database operations. Handles configuration, connection acquisition, and pool lifecycle.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| <code style="background-color:gainsboro">@classmethod</code> | `connect` | `db_type: str`, `**kwargs` | `DatabaseManager` | Factory | Create a connection context. Use with `async with` or `with`. |
| <code style="background-color:gainsboro">@classmethod</code> | `from_config` | `db_type: str`, `config: DatabaseConfig` | `DatabaseManager` | Factory | Create from existing DatabaseConfig. |
| <code style="background-color:gainsboro">@classmethod</code> | `close_all` | `timeout: float = 60.0` | `None` | Pool Management | Close all connection pools (call at app shutdown). |
| <code style="background-color:gainsboro">@classmethod</code> | `close_pool` | `config_hash: str`, `timeout: float = 30.0` | `None` | Pool Management | Close a specific pool by config hash. |
| <code style="background-color:gainsboro">@classmethod</code> | `get_pool_metrics` | `config_hash: Optional[str] = None` | `Dict[str, Any]` | Diagnostics | Get metrics for specific or all pools. |
| | `config` | | `DatabaseConfig` | Property | The underlying DatabaseConfig. |
| | `hash` | | `str` | Property | Config hash for pool identification. |

</details>

<br>

<details>
<summary><strong>Context Manager Protocols</strong></summary>

| Protocol | Returns | Description |
|----------|---------|-------------|
| `async with DatabaseManager.connect(...) as conn` | `AsyncConnection` | Async context manager - releases connection on exit |
| `with DatabaseManager.connect(...) as conn` | `SyncConnection` | Sync context manager - releases connection on exit |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `db_type: str`, `config: Optional[DatabaseConfig] = None`, `database: str = None`, `host: str = "localhost"`, `port: int = None`, `user: str = None`, `password: str = None`, `alias: str = None`, `env: str = "prod"`, `connection_acquisition_timeout: float = 10.0`, `pool_creation_timeout: float = 30.0`, `query_execution_timeout: float = 60.0`, `connection_creation_timeout: float = 15.0` | | Initialization | Initialize with connection parameters or existing config. |
| | `__aenter__` | | `AsyncConnection` | Async Protocol | Get async connection from pool. |
| | `__aexit__` | `exc_type`, `exc_val`, `exc_tb` | `None` | Async Protocol | Release async connection to pool. |
| | `__enter__` | | `SyncConnection` | Sync Protocol | Get sync connection. |
| | `__exit__` | `exc_type`, `exc_val`, `exc_tb` | `None` | Sync Protocol | Release sync connection. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AsyncConnection`

Abstract base class for asynchronous database connections with entity operations.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| <code style="background-color:lightpink">@async_method</code> | `execute` | `sql: str`, `params: Optional[tuple] = None`, `timeout: Optional[float] = None`, `tags: Optional[Dict[str, Any]] = None` | `List[Tuple]` | SQL Execution | Execute a SQL query with ? placeholders. |
| <code style="background-color:lightpink">@async_method</code> | `executemany` | `sql: str`, `param_list: List[tuple]`, `timeout: Optional[float] = None`, `tags: Optional[Dict[str, Any]] = None` | `List[Tuple]` | SQL Execution | Execute SQL with multiple parameter sets. |
| <code style="background-color:gainsboro">@asynccontextmanager</code> | `transaction` | | `AsyncIterator[AsyncConnection]` | Transaction | Context manager for explicit transaction control with auto-commit/rollback. |
| <code style="background-color:gainsboro">@abstractmethod</code> | `in_transaction` | | `bool` | Transaction | Return True if connection is in an active transaction. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@abstractmethod</code> | `begin_transaction` | | `None` | Transaction | Begin a database transaction. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@abstractmethod</code> | `commit_transaction` | | `None` | Transaction | Commit the current transaction. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@abstractmethod</code> | `rollback_transaction` | | `None` | Transaction | Roll back the current transaction. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@abstractmethod</code> | `close` | | `None` | Connection | Close the database connection. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@abstractmethod</code> | `get_version_details` | | `Dict[str, str]` | Diagnostic | Returns {'db_server_version', 'db_driver'}. |
| | `register_serializer` | `type_name: str`, `serializer_func: Callable`, `deserializer_func: Callable` | `None` | Serialization | Register custom serialization functions for non-standard types. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `get_entity` | `entity_name: str`, `entity_id: str`, `include_deleted: bool = False`, `deserialize: bool = False` | `Optional[Dict[str, Any]]` | Entity | Fetch an entity by ID. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `save_entity` | `entity_name: str`, `entity: Dict[str, Any]`, `user_id: Optional[str] = None`, `comment: Optional[str] = None`, `timeout: Optional[float] = 60.0` | `Dict[str, Any]` | Entity | Save an entity (create or update). |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `save_entities` | `entity_name: str`, `entities: List[Dict[str, Any]]`, `user_id: Optional[str] = None`, `comment: Optional[str] = None`, `timeout: Optional[float] = 60.0` | `List[Dict[str, Any]]` | Entity | Save multiple entities in bulk. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `delete_entity` | `entity_name: str`, `entity_id: str`, `user_id: Optional[str] = None`, `permanent: bool = False` | `bool` | Entity | Delete an entity (soft or permanent). |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `restore_entity` | `entity_name: str`, `entity_id: str`, `user_id: Optional[str] = None` | `bool` | Entity | Restore a soft-deleted entity. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `find_entities` | `entity_name: str`, `where_clause: Optional[str] = None`, `params: Optional[Tuple] = None`, `order_by: Optional[str] = None`, `limit: Optional[int] = None`, `offset: Optional[int] = None`, `include_deleted: bool = False`, `deserialize: bool = False` | `List[Dict[str, Any]]` | Entity | Find entities matching criteria. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `count_entities` | `entity_name: str`, `where_clause: Optional[str] = None`, `params: Optional[Tuple] = None`, `include_deleted: bool = False` | `int` | Entity | Count entities matching criteria. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `get_entity_history` | `entity_name: str`, `entity_id: str`, `deserialize: bool = False` | `List[Dict[str, Any]]` | Entity | Get complete history of an entity. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:lightgreen">@auto_transaction</code> | `get_entity_by_version` | `entity_name: str`, `entity_id: str`, `version: int`, `deserialize: bool = False` | `Optional[Dict[str, Any]]` | Entity | Get a specific version of an entity. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `conn: Any`, `config: DatabaseConfig` | | Initialization | Initializes async connection with tracking information. |
| | `_get_raw_connection` | | `Any` | Connection | Return the underlying database connection. |
| | `_mark_active` | | | Connection | Mark the connection as active (used recently). |
| | `_is_idle` | `timeout_seconds: int=1800` | `bool` | Connection | Check if connection has been idle too long (default 30 mins). |
| | `_mark_leaked` | | | Connection | Mark this connection as leaked. |
| <code style="background-color:gainsboro">@property</code> | `_is_leaked` | | `bool` | Connection | Check if connection has been marked as leaked. |
| <code style="background-color:lightpink">@async_method</code> | `_get_field_names` | `entity_name: str`, `is_history: bool = False` | `List[str]` | Entity | Get field names for an entity table. |
| <code style="background-color:lightpink">@async_method</code> | `_ensure_entity_schema` | `entity_name: str`, `sample_entity: Optional[Dict[str, Any]] = None` | `None` | Entity | Ensure entity tables and metadata exist. |
| <code style="background-color:lightpink">@async_method</code> | `_update_entity_metadata` | `entity_name: str`, `entity: Dict[str, Any]` | `None` | Entity | Update metadata and add missing columns. |
| <code style="background-color:lightpink">@async_method</code> | `_get_entity_metadata` | `entity_name: str`, `use_cache: bool = True` | `Dict[str, str]` | Entity | Get metadata mapping field names to types. |
| <code style="background-color:lightpink">@async_method</code> | `_add_to_history` | `entity_name: str`, `entity: Dict[str, Any]`, `user_id: Optional[str] = None`, `comment: Optional[str] = None` | `None` | Entity | Add entry to history table with version tracking. |
| <code style="background-color:lightpink">@async_method</code> | `_check_column_exists` | `table_name: str`, `column_name: str` | `bool` | Entity | Check if a column exists in a table. |

</details>

<br>

#### Current implementations: `PostgresAsyncConnection`, `MysqlAsyncConnection`, `SqliteAsyncConnection`

**Notes:**

* An Entity is a dictionary of any serializable values
* The `@auto_transaction` decorator ensures operations run within a transaction
* Schema creation and metadata updates happen automatically
* Entity values are serialized to database types; use `deserialize=True` to convert back
* The `SyncConnection` class exposes the same public methods (without async/await)

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DatabaseConfig`

Configuration object for database connections with comprehensive timeout settings.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `config` | | `Dict[str, Any]` | Configuration | Returns configuration as dictionary. |
| | `database` | | `str` | Configuration | Returns the database name. |
| | `alias` | | `str` | Configuration | Returns the connection alias. |
| | `host` | | `str` | Configuration | Returns the database host. |
| | `port` | | `int` | Configuration | Returns the database port. |
| | `user` | | `str` | Configuration | Returns the database user. |
| | `password` | | `str` | Configuration | Returns the database password. |
| | `env` | | `str` | Configuration | Returns the environment ('prod', 'dev', 'test', etc.). |
| | `hash` | | `str` | Configuration | Returns a stable hash key (excludes password). |
| | `connection_acquisition_timeout` | | `float` | Timeout | Timeout for acquiring connections from pool. |
| | `pool_creation_timeout` | | `float` | Timeout | Timeout for pool initialization. |
| | `query_execution_timeout` | | `float` | Timeout | Default timeout for SQL queries. |
| | `connection_creation_timeout` | | `float` | Timeout | Timeout for creating individual connections. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `database: str`, `host: str="localhost"`, `port: int=5432`, `user: str=None`, `password: str=None`, `alias: str=None`, `env: str='prod'`, `connection_acquisition_timeout: float=10.0`, `pool_creation_timeout: float=30.0`, `query_execution_timeout: float=60.0`, `connection_creation_timeout: float=15.0` | | Initialization | Initialize with connection parameters and timeouts. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `PoolManager`

Manages the lifecycle of asynchronous connection pools with monitoring and leak detection.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `alias` | | `str` | Configuration | Returns the friendly name of this pool manager. |
| | `hash` | | `str` | Configuration | Returns the unique hash for this pool manager. |
| | `get_pool_status` | | `Dict[str, Any]` | Diagnostic | Gets pool status including metrics, sizes, and health. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@classmethod</code> | `health_check_all_pools` | | `Dict[str, bool]` | Diagnostic | Checks health of all connection pools. |
| <code style="background-color:gainsboro">@classmethod</code> | `get_pool_metrics` | `config_hash: Optional[str] = None` | `Dict` | Metrics | Get metrics for specific or all pools. |
| <code style="background-color:lightpink">@async_method</code> <code style="background-color:gainsboro">@classmethod</code> | `close_pool` | `config_hash: Optional[str] = None`, `timeout: Optional[float] = 60` | `None` | Lifecycle | Close one or all shared connection pools. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DatabaseFactory`

Factory for creating database instances (used internally by DatabaseManager).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| <code style="background-color:gainsboro">@staticmethod</code> | `create_database` | `db_type: str`, `db_config: DatabaseConfig` | `ConnectionManager` | Factory | Create database instance (PostgreSQL, MySQL, SQLite). |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `ConnectionManager`

Manages sync and async database connection lifecycles (used internally by DatabaseManager).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `is_environment_async` | | `bool` | Environment | Determines if running in an async environment. |
| | `get_sync_connection` | | `SyncConnection` | Connection | Returns a synchronized database connection. |
| | `release_sync_connection` | | `None` | Connection | Releases the cached synchronous connection. |
| <code style="background-color:gainsboro">@contextmanager</code> | `sync_connection` | | `Iterator[SyncConnection]` | Connection | Context manager for sync connection usage. |
| <code style="background-color:lightpink">@async_method</code> | `get_async_connection` | | `AsyncConnection` | Connection | Acquires an async connection from the pool. |
| <code style="background-color:lightpink">@async_method</code> | `release_async_connection` | `async_conn: AsyncConnection` | `None` | Connection | Releases an async connection to the pool. |
| <code style="background-color:gainsboro">@asynccontextmanager</code> | `async_connection` | | `Iterator[AsyncConnection]` | Connection | Context manager for async connection usage. |

</details>

<br>

#### Current implementations: `PostgresDatabase`, `MySqlDatabase`, `SqliteDatabase`

</div>