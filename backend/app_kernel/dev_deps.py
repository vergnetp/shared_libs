"""
Dev Dependencies - Auto-start Redis/Postgres/MySQL via Docker.

Automatically starts containers when:
- URL points to localhost
- Service isn't already running  
- Docker is available

Remote URLs are ignored (production-safe).

Container names:
- appkernel-redis
- appkernel-postgres
- appkernel-mysql

Containers persist between app restarts.

Redis Fallback Chain:
1. Explicit REDIS_URL set and reachable → use it
2. Localhost Redis running → use it  
3. Docker available → start container
4. Fallback to fakeredis (in-memory)
"""

import asyncio
import logging
import shutil
import socket
import subprocess
from typing import Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_docker_running_cache = None

def _is_docker_available() -> bool:
    """Check if Docker is available (binary exists)."""
    return shutil.which("docker") is not None


def _is_docker_running_cached() -> bool:
    """
    Check if Docker daemon is responding. Cached for the process lifetime
    to avoid repeated slow subprocess calls on Windows when Docker Desktop is off.
    """
    global _docker_running_cache
    if _docker_running_cache is not None:
        return _docker_running_cache
    _docker_running_cache = _is_docker_available() and _is_docker_running()
    return _docker_running_cache


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _parse_db_url(url: str) -> dict:
    """Parse database URL into components."""
    parsed = urlparse(url)
    return {
        "type": parsed.scheme.replace("postgresql", "postgres"),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
        "database": parsed.path.lstrip("/") or "app",
    }


def _parse_redis_url(url: str) -> dict:
    """Parse Redis URL into components."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "password": parsed.password,
    }


def _container_exists(name: str) -> bool:
    """Check if a Docker container exists (running or stopped)."""
    try:
        result = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _container_running(name: str) -> bool:
    """Check if a Docker container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def _start_container(name: str) -> bool:
    """Start an existing container."""
    try:
        result = subprocess.run(
            ["docker", "start", name],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_container(args: list) -> bool:
    """Run a new container."""
    try:
        result = subprocess.run(
            ["docker", "run"] + args,
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"Failed to start container: {e}")
        return False


async def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for a port to become available."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if _is_port_open(host, port):
            return True
        await asyncio.sleep(0.5)
    return False


# =============================================================================
# Redis
# =============================================================================

REDIS_CONTAINER_NAME = "appkernel-redis"

# Special URL indicating fakeredis mode
REDIS_FAKE_URL = "fakeredis://"

# Singleton fakeredis instances (all callers share one in-memory store, like real Redis)
_fakeredis_async_instance = None
_fakeredis_sync_instance = None


def get_async_redis_client(url: str = None):
    """
    Get an async Redis client for the given URL.
    
    .. deprecated:: Use ``from app_kernel import get_redis`` instead.
        This function is kept for backward compatibility. New code should
        use the shared singleton from ``app_kernel.redis``.
    """
    from .redis import client as _rc
    
    # If redis module already initialized by bootstrap, return shared client
    if _rc._initialized:
        return _rc.get_redis()
    
    # Standalone call (e.g. before bootstrap) — initialize and return
    _rc.init_redis(url)
    return _rc.get_redis()


def get_sync_redis_client(url: str = None, decode_responses: bool = True):
    """
    Get a sync Redis client for the given URL.
    
    .. deprecated:: Use ``from app_kernel import get_sync_redis`` instead.
    """
    from .redis import client as _rc
    
    if _rc._initialized:
        return _rc.get_sync_redis()
    
    _rc.init_redis(url)
    return _rc.get_sync_redis()


def is_fake_redis_url(url: str) -> bool:
    """Check if URL indicates fakeredis."""
    from .redis.client import is_fake_url
    return is_fake_url(url)


async def ensure_redis(redis_url: Optional[str] = None) -> Tuple[bool, str, str]:
    """
    Ensure Redis is available using fallback chain.
    
    Fallback chain:
    1. Explicit URL set and reachable → use it
    2. Localhost:6379 running → use it
    3. Docker available → start container
    4. Fallback to fakeredis (in-memory)
    
    Args:
        redis_url: Optional explicit Redis URL
    
    Returns:
        (success, message, actual_url)
        - actual_url is the URL to use (may differ from input)
        - actual_url = "fakeredis://" means use fakeredis
    """
    # 1. Explicit URL provided
    if redis_url and not is_fake_redis_url(redis_url):
        config = _parse_redis_url(redis_url)
        host, port = config["host"], config["port"]
        
        if _is_port_open(host, port):
            return True, f"Redis connected at {host}:{port}", redis_url
        
        # URL provided but not reachable
        if host not in ("localhost", "127.0.0.1"):
            # Remote host — can't help, fall to fakeredis
            logger.warning(f"Redis at {host}:{port} not reachable, using fakeredis")
            return True, "Using fakeredis (in-memory, single-instance)", REDIS_FAKE_URL
        
        if port != 6379:
            # Localhost but non-standard port — Docker won't help (it uses 6379)
            logger.info(f"Redis at localhost:{port} not reachable, using fakeredis")
            return True, "Using fakeredis (in-memory, single-instance)", REDIS_FAKE_URL
    
    # 2. Check localhost:6379 (default)
    if _is_port_open("localhost", 6379):
        return True, "Redis found on localhost:6379", "redis://localhost:6379"
    
    # 3. Try Docker (only if daemon is actually responding)
    if _is_docker_running_cached():
        if _container_exists(REDIS_CONTAINER_NAME):
            if not _container_running(REDIS_CONTAINER_NAME):
                logger.info(f"Starting existing Redis container: {REDIS_CONTAINER_NAME}")
                if _start_container(REDIS_CONTAINER_NAME):
                    if await _wait_for_port("localhost", 6379, timeout=15):
                        return True, "Redis started (existing container)", "redis://localhost:6379"
            else:
                # Container running but port not open? Wait a bit
                if await _wait_for_port("localhost", 6379, timeout=5):
                    return True, "Redis container already running", "redis://localhost:6379"
        else:
            # Create new container
            logger.info(f"Creating Redis container: {REDIS_CONTAINER_NAME}")
            args = [
                "-d",
                "--name", REDIS_CONTAINER_NAME,
                "-p", "6379:6379",
                "redis:7-alpine",
            ]
            if _run_container(args):
                if await _wait_for_port("localhost", 6379, timeout=15):
                    return True, "Redis started (new container)", "redis://localhost:6379"
    
    # 4. Fallback to fakeredis
    return True, "Using fakeredis (in-memory, single-instance)", REDIS_FAKE_URL


# =============================================================================
# Unified Database (auto-resolve in non-prod, strict in prod)
# =============================================================================

# Container names for all database types
POSTGRES_CONTAINER_NAME = "appkernel-postgres"
MYSQL_CONTAINER_NAME = "appkernel-mysql"
MONGODB_CONTAINER_NAME = "appkernel-mongodb"

# Default connection params for auto-spawned containers
_DB_DEFAULTS = {
    "postgresql": {"port": 5432, "user": "postgres", "password": "postgres", "database": "app", "image": "postgres:16-alpine", "container": POSTGRES_CONTAINER_NAME},
    "mysql": {"port": 3306, "user": "root", "password": "root", "database": "app", "image": "mysql:8", "container": MYSQL_CONTAINER_NAME},
    "mongodb": {"port": 27017, "user": "", "password": "", "database": "app", "image": "mongo:7", "container": MONGODB_CONTAINER_NAME},
}


def _is_docker_running() -> bool:
    """Check if Docker daemon is actually responding (not just installed)."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _try_start_docker() -> bool:
    """
    Attempt to start the Docker daemon.
    
    Tries platform-specific approaches:
    - Windows: Start-Service docker / start "Docker Desktop"
    - Linux: systemctl start docker / service docker start
    - macOS: open Docker.app
    """
    import platform
    system = platform.system()
    
    commands = []
    if system == "Windows":
        commands = [
            ["powershell", "-Command", "Start-Service docker"],
            ["powershell", "-Command", 'Start-Process "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"'],
        ]
    elif system == "Darwin":
        commands = [
            ["open", "-a", "Docker"],
        ]
    else:  # Linux
        commands = [
            ["sudo", "systemctl", "start", "docker"],
            ["sudo", "service", "docker", "start"],
        ]
    
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode == 0:
                logger.info(f"Docker daemon start requested via: {' '.join(cmd)}")
                return True
        except Exception:
            continue
    
    return False


async def _ensure_docker() -> bool:
    """
    Ensure Docker is available and running.
    
    1. Check if daemon is responding → done
    2. Binary exists but daemon not running → try to start it
    3. Not installed → return False
    """
    if _is_docker_running_cached():
        return True
    
    if not _is_docker_running_cached():
        return False
    
    # Binary exists, daemon not running — try to start
    logger.info("Docker installed but not running, attempting to start...")
    if _try_start_docker():
        # Wait for daemon to be ready (Docker Desktop can take a while)
        for i in range(30):
            await asyncio.sleep(2)
            if _is_docker_running():
                global _docker_running_cache
                _docker_running_cache = True
                logger.info("Docker daemon started successfully")
                return True
        logger.warning("Docker daemon did not start within 60s")
    
    return False


def _parse_db_shorthand(url: str) -> Optional[str]:
    """
    Detect shorthand database type (just the engine name, no ://).
    
    Examples:
        "postgresql" → "postgresql"
        "mysql" → "mysql"  
        "mongodb" → "mongodb"
        "sqlite" → "sqlite"
        "postgresql://..." → None (not shorthand)
    
    Returns db type string or None if it's a full URL.
    """
    clean = url.strip().lower()
    if "://" in clean:
        return None
    if clean in ("postgresql", "postgres", "pg"):
        return "postgresql"
    if clean in ("mysql", "mariadb"):
        return "mysql"
    if clean in ("mongodb", "mongo"):
        return "mongodb"
    if clean in ("sqlite", "sqlite3"):
        return "sqlite"
    return None


async def _spawn_db_container(db_type: str, defaults: dict) -> Tuple[bool, str, str]:
    """
    Spawn a database container via Docker.
    
    Returns:
        (success, message, connection_url)
    """
    port = defaults["port"]
    container_name = defaults["container"]
    
    # Already running on localhost?
    if _is_port_open("localhost", port):
        url = _build_db_url(db_type, defaults)
        return True, f"{db_type} already running on localhost:{port}", url
    
    # Need Docker
    if not await _ensure_docker():
        return False, f"{db_type} not running and Docker unavailable", ""
    
    # Check existing container
    if _container_exists(container_name):
        if not _container_running(container_name):
            logger.info(f"Starting existing {db_type} container: {container_name}")
            if not _start_container(container_name):
                return False, f"Failed to start existing {db_type} container", ""
        # else: already running, just wait for port
    else:
        # Create new container
        logger.info(f"Creating {db_type} container: {container_name}")
        args = _build_container_args(db_type, defaults)
        if not _run_container(args):
            return False, f"Failed to create {db_type} container", ""
    
    # Wait for port
    timeout = 60 if db_type == "mysql" else 30
    if await _wait_for_port("localhost", port, timeout=timeout):
        # Extra wait for DB to accept connections
        await asyncio.sleep(3 if db_type == "mysql" else 2)
        url = _build_db_url(db_type, defaults)
        return True, f"{db_type} ready at localhost:{port}", url
    
    return False, f"{db_type} container started but not responding on port {port}", ""


def _build_db_url(db_type: str, defaults: dict) -> str:
    """Build connection URL from defaults."""
    user = defaults["user"]
    password = defaults["password"]
    port = defaults["port"]
    database = defaults["database"]
    
    if db_type == "mongodb":
        if user:
            return f"mongodb://{user}:{password}@localhost:{port}/{database}"
        return f"mongodb://localhost:{port}/{database}"
    
    scheme = db_type if db_type != "postgres" else "postgresql"
    return f"{scheme}://{user}:{password}@localhost:{port}/{database}"


def _build_container_args(db_type: str, defaults: dict) -> list:
    """Build docker run arguments for a database container."""
    port = defaults["port"]
    container_name = defaults["container"]
    image = defaults["image"]
    user = defaults["user"]
    password = defaults["password"]
    database = defaults["database"]
    
    args = [
        "-d",
        "--name", container_name,
        "-p", f"{port}:{port}",
        "-v", f"{container_name}-data:/var/lib/{'postgresql/data' if 'postgres' in db_type else 'mysql' if db_type == 'mysql' else 'data/db'}",
    ]
    
    if db_type == "postgresql":
        args += [
            "-e", f"POSTGRES_USER={user}",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", f"POSTGRES_DB={database}",
        ]
    elif db_type == "mysql":
        args += [
            "-e", f"MYSQL_ROOT_PASSWORD={password}",
            "-e", f"MYSQL_DATABASE={database}",
        ]
    elif db_type == "mongodb":
        if user:
            args += [
                "-e", f"MONGO_INITDB_ROOT_USERNAME={user}",
                "-e", f"MONGO_INITDB_ROOT_PASSWORD={password}",
            ]
    
    args.append(image)
    return args


async def ensure_database(
    database_url: Optional[str] = None,
    service_name: str = "app",
) -> Tuple[bool, str, str]:
    """
    Ensure database is available.
    
    Non-prod (ENV != 'prod'):
      - Empty/None → auto-create SQLite ./data/{service_name}.db
      - "sqlite" → auto-create SQLite
      - "postgresql"/"mysql"/"mongodb" → spawn Docker container, build URL
      - sqlite:///path → use as-is
      - postgresql://localhost:... → spawn Docker if not running
      - postgresql://remote:... → connect as-is
    
    Prod (ENV == 'prod'):
      - Empty/None → raise error
      - Any URL → connect as-is, no auto-spawning
    
    Args:
        database_url: Database URL, shorthand type, or None
        service_name: Service name (for auto-generated SQLite path)
    
    Returns:
        (success, message, resolved_url)
    
    Raises:
        RuntimeError: In prod with no database_url
    """
    from .env_checks import is_prod
    
    prod = is_prod()
    
    # --- PROD: strict mode ---
    if prod:
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required in production. "
                "Set ENV=dev for auto-provisioning or provide a database URL."
            )
        # Just validate the URL parses, no auto-spawn
        return True, f"Using configured database", database_url
    
    # --- NON-PROD: auto-provision mode ---
    
    # Empty → default to SQLite
    if not database_url:
        from pathlib import Path
        data_dir = Path("./data")
        data_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = data_dir / f"{service_name}.db"
        url = f"sqlite:///{sqlite_path}"
        return True, f"Auto-created SQLite: {sqlite_path}", url
    
    # Check for shorthand (just the engine name)
    shorthand = _parse_db_shorthand(database_url)
    if shorthand:
        if shorthand == "sqlite":
            from pathlib import Path
            data_dir = Path("./data")
            data_dir.mkdir(parents=True, exist_ok=True)
            sqlite_path = data_dir / f"{service_name}.db"
            url = f"sqlite:///{sqlite_path}"
            return True, f"Using SQLite: {sqlite_path}", url
        
        if shorthand not in _DB_DEFAULTS:
            return False, f"Unknown database type: {shorthand}", ""
        
        defaults = _DB_DEFAULTS[shorthand].copy()
        defaults["database"] = service_name.replace("-", "_")
        
        success, msg, url = await _spawn_db_container(shorthand, defaults)
        if success:
            return True, msg, url
        
        # Fallback to SQLite
        from pathlib import Path
        data_dir = Path("./data")
        data_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = data_dir / f"{service_name}.db"
        fallback_url = f"sqlite:///{sqlite_path}"
        logger.warning(f"{msg} — falling back to SQLite: {sqlite_path}")
        return True, f"Fallback to SQLite ({msg})", fallback_url
    
    # Full URL — parse it
    config = _parse_db_url(database_url)
    db_type = config["type"]
    host = config["host"]
    port = config["port"]
    
    # SQLite URL → use as-is
    if db_type == "sqlite":
        return True, f"Using SQLite: {config['name']}", database_url
    
    # Remote host → use as-is (no auto-spawn)
    if host not in ("localhost", "127.0.0.1"):
        if _is_port_open(host, port or 5432):
            return True, f"{db_type} connected at {host}:{port}", database_url
        return False, f"{db_type} at {host}:{port} not reachable", database_url
    
    # Localhost URL → auto-spawn if not running
    if _is_port_open(host, port):
        return True, f"{db_type} already running at {host}:{port}", database_url
    
    # Localhost but not running — try Docker
    normalized = db_type.replace("postgres", "postgresql")
    if normalized in _DB_DEFAULTS:
        defaults = _DB_DEFAULTS[normalized].copy()
        # Override defaults with values from URL
        defaults["port"] = port or defaults["port"]
        defaults["user"] = config.get("user") or defaults["user"]
        defaults["password"] = config.get("password") or defaults["password"]
        defaults["database"] = config.get("database") or defaults["database"]
        
        success, msg, _ = await _spawn_db_container(normalized, defaults)
        if success:
            return True, msg, database_url  # Keep original URL
        
        # Fallback to SQLite
        from pathlib import Path
        data_dir = Path("./data")
        data_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = data_dir / f"{service_name}.db"
        fallback_url = f"sqlite:///{sqlite_path}"
        logger.warning(f"{msg} — falling back to SQLite: {sqlite_path}")
        return True, f"Fallback to SQLite ({msg})", fallback_url
    
    return False, f"Cannot auto-provision {db_type}", database_url


# =============================================================================
# PostgreSQL (legacy — kept for direct calls, ensure_database is preferred)
# =============================================================================


async def ensure_postgres(db_url: str) -> Tuple[bool, str]:
    """
    Ensure PostgreSQL is available, starting a container if needed.
    
    Returns:
        (success, message)
    """
    if not db_url:
        return True, "Database not configured"
    
    config = _parse_db_url(db_url)
    
    if config["type"] not in ("postgres", "postgresql"):
        return True, f"Database type is {config['type']}, not postgres"
    
    host = config["host"]
    port = config["port"]
    
    # Check if already reachable
    if _is_port_open(host, port):
        return True, f"PostgreSQL already running at {host}:{port}"
    
    # Only auto-start for localhost
    if host not in ("localhost", "127.0.0.1"):
        return False, f"PostgreSQL at {host}:{port} not reachable (not localhost, won't auto-start)"
    
    if not _is_docker_running_cached():
        return False, "PostgreSQL not reachable and Docker not available"
    
    # Check if container exists
    if _container_exists(POSTGRES_CONTAINER_NAME):
        if not _container_running(POSTGRES_CONTAINER_NAME):
            logger.info(f"Starting existing PostgreSQL container: {POSTGRES_CONTAINER_NAME}")
            if not _start_container(POSTGRES_CONTAINER_NAME):
                return False, "Failed to start existing PostgreSQL container"
    else:
        # Create new container
        logger.info(f"Creating PostgreSQL container: {POSTGRES_CONTAINER_NAME}")
        args = [
            "-d",
            "--name", POSTGRES_CONTAINER_NAME,
            "-p", f"{port}:5432",
            "-e", f"POSTGRES_USER={config['user']}",
            "-e", f"POSTGRES_PASSWORD={config['password']}",
            "-e", f"POSTGRES_DB={config['database']}",
            "postgres:16-alpine",
        ]
        if not _run_container(args):
            return False, "Failed to create PostgreSQL container"
    
    # Wait for Postgres to be ready (takes longer than Redis)
    if await _wait_for_port(host, port, timeout=30):
        # Extra wait for Postgres to actually accept connections
        await asyncio.sleep(2)
        return True, f"PostgreSQL started at {host}:{port}"
    else:
        return False, "PostgreSQL container started but not responding"


# =============================================================================
# MySQL (legacy — kept for direct calls, ensure_database is preferred)
# =============================================================================


async def ensure_mysql(db_url: str) -> Tuple[bool, str]:
    """
    Ensure MySQL is available, starting a container if needed.
    
    Returns:
        (success, message)
    """
    if not db_url:
        return True, "Database not configured"
    
    config = _parse_db_url(db_url)
    
    if config["type"] != "mysql":
        return True, f"Database type is {config['type']}, not mysql"
    
    host = config["host"]
    port = config["port"] or 3306
    
    # Check if already reachable
    if _is_port_open(host, port):
        return True, f"MySQL already running at {host}:{port}"
    
    # Only auto-start for localhost
    if host not in ("localhost", "127.0.0.1"):
        return False, f"MySQL at {host}:{port} not reachable (not localhost, won't auto-start)"
    
    if not _is_docker_running_cached():
        return False, "MySQL not reachable and Docker not available"
    
    # Check if container exists
    if _container_exists(MYSQL_CONTAINER_NAME):
        if not _container_running(MYSQL_CONTAINER_NAME):
            logger.info(f"Starting existing MySQL container: {MYSQL_CONTAINER_NAME}")
            if not _start_container(MYSQL_CONTAINER_NAME):
                return False, "Failed to start existing MySQL container"
    else:
        # Create new container
        logger.info(f"Creating MySQL container: {MYSQL_CONTAINER_NAME}")
        args = [
            "-d",
            "--name", MYSQL_CONTAINER_NAME,
            "-p", f"{port}:3306",
            "-e", f"MYSQL_ROOT_PASSWORD={config['password']}",
            "-e", f"MYSQL_USER={config['user']}",
            "-e", f"MYSQL_PASSWORD={config['password']}",
            "-e", f"MYSQL_DATABASE={config['database']}",
            "mysql:8",
        ]
        if not _run_container(args):
            return False, "Failed to create MySQL container"
    
    # Wait for MySQL to be ready (takes longer)
    if await _wait_for_port(host, port, timeout=60):
        # Extra wait for MySQL to actually accept connections
        await asyncio.sleep(5)
        return True, f"MySQL started at {host}:{port}"
    else:
        return False, "MySQL container started but not responding"


# =============================================================================
# Main Entry Point
# =============================================================================

async def ensure_dev_deps(
    database_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    service_name: str = "app",
) -> dict:
    """
    Ensure all dev dependencies are available.
    
    Args:
        database_url: DATABASE_URL (auto-provisioned in non-prod)
        redis_url: REDIS_URL (will be auto-started or fall back to fakeredis)
        service_name: Service name (for auto-generated SQLite paths)
    
    Returns:
        Dict with status of each dependency, including resolved URLs
    """
    results = {}
    
    # Redis (always succeeds - has fakeredis fallback)
    success, msg, actual_url = await ensure_redis(redis_url)
    results["redis"] = {
        "success": success,
        "message": msg,
        "url": actual_url,
        "is_fake": is_fake_redis_url(actual_url),
    }
    if is_fake_redis_url(actual_url):
        logger.info(f"⚡ {msg}")
    else:
        logger.info(f"✓ {msg}")
    
    # Database (auto-provision in non-prod, strict in prod)
    success, msg, resolved_db_url = await ensure_database(
        database_url, service_name=service_name
    )
    results["database"] = {
        "success": success,
        "message": msg,
        "url": resolved_db_url,
        "upgraded": resolved_db_url != database_url,
    }
    if success:
        logger.info(f"✓ {msg}")
    else:
        logger.warning(f"✗ Database: {msg}")
    
    return results
