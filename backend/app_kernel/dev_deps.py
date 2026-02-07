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


def _is_docker_available() -> bool:
    """Check if Docker is available."""
    return shutil.which("docker") is not None


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


def get_async_redis_client(url: str = None):
    """
    Get an async Redis client for the given URL.
    
    Args:
        url: Redis URL, or None/fakeredis:// for in-memory fakeredis
    
    Returns:
        Async Redis client (real or fakeredis.aioredis)
    """
    if url is None or is_fake_redis_url(url):
        import fakeredis.aioredis
        return fakeredis.aioredis.FakeRedis(decode_responses=False)
    else:
        import redis.asyncio as aioredis
        return aioredis.from_url(url)


def is_fake_redis_url(url: str) -> bool:
    """Check if URL indicates fakeredis."""
    return url is None or url == REDIS_FAKE_URL or url.startswith("fakeredis://")


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
        
        # URL provided but not reachable - only try Docker for localhost
        if host not in ("localhost", "127.0.0.1"):
            # Remote URL not reachable - fall through to fakeredis
            logger.warning(f"Redis at {host}:{port} not reachable, using fakeredis")
    
    # 2. Check localhost:6379 (default)
    if _is_port_open("localhost", 6379):
        return True, "Redis found on localhost:6379", "redis://localhost:6379"
    
    # 3. Try Docker
    if _is_docker_available():
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
# PostgreSQL
# =============================================================================

POSTGRES_CONTAINER_NAME = "appkernel-postgres"


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
    
    if not _is_docker_available():
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
# MySQL
# =============================================================================

MYSQL_CONTAINER_NAME = "appkernel-mysql"


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
    
    if not _is_docker_available():
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
) -> dict:
    """
    Ensure all dev dependencies are available.
    
    Args:
        database_url: DATABASE_URL (postgres/mysql will be auto-started)
        redis_url: REDIS_URL (will be auto-started or fall back to fakeredis)
    
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
    
    # Database
    if database_url:
        db_type = _parse_db_url(database_url)["type"]
        
        if db_type in ("postgres", "postgresql"):
            success, msg = await ensure_postgres(database_url)
            results["postgres"] = {"success": success, "message": msg}
        elif db_type == "mysql":
            success, msg = await ensure_mysql(database_url)
            results["mysql"] = {"success": success, "message": msg}
        else:
            results["database"] = {"success": True, "message": "SQLite doesn't need Docker"}
        
        if results.get("postgres") or results.get("mysql"):
            key = "postgres" if "postgres" in results else "mysql"
            if results[key]["success"]:
                logger.info(f"✓ {results[key]['message']}")
            else:
                logger.warning(f"✗ Database: {results[key]['message']}")
    
    return results
