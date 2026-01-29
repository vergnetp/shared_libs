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
"""

import asyncio
import logging
import os
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
    except:
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
    except:
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
    except:
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
    except:
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


async def ensure_redis(redis_url: str) -> Tuple[bool, str]:
    """
    Ensure Redis is available, starting a container if needed.
    
    Returns:
        (success, message)
    """
    if not redis_url:
        return True, "Redis not configured"
    
    config = _parse_redis_url(redis_url)
    host = config["host"]
    port = config["port"]
    
    # Check if already reachable
    if _is_port_open(host, port):
        return True, f"Redis already running at {host}:{port}"
    
    # Only auto-start for localhost
    if host not in ("localhost", "127.0.0.1"):
        return False, f"Redis at {host}:{port} not reachable (not localhost, won't auto-start)"
    
    if not _is_docker_available():
        return False, "Redis not reachable and Docker not available"
    
    # Check if container exists
    if _container_exists(REDIS_CONTAINER_NAME):
        if not _container_running(REDIS_CONTAINER_NAME):
            logger.info(f"Starting existing Redis container: {REDIS_CONTAINER_NAME}")
            if not _start_container(REDIS_CONTAINER_NAME):
                return False, "Failed to start existing Redis container"
    else:
        # Create new container
        logger.info(f"Creating Redis container: {REDIS_CONTAINER_NAME}")
        args = [
            "-d",
            "--name", REDIS_CONTAINER_NAME,
            "-p", f"{port}:6379",
            "redis:7-alpine",
        ]
        if not _run_container(args):
            return False, "Failed to create Redis container"
    
    # Wait for Redis to be ready
    if await _wait_for_port(host, port, timeout=15):
        return True, f"Redis started at {host}:{port}"
    else:
        return False, "Redis container started but not responding"


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
        redis_url: REDIS_URL (will be auto-started if needed)
    
    Returns:
        Dict with status of each dependency
    """
    results = {}
    
    # Redis
    if redis_url:
        success, msg = await ensure_redis(redis_url)
        results["redis"] = {"success": success, "message": msg}
        if success:
            logger.info(f"✓ {msg}")
        else:
            logger.warning(f"✗ Redis: {msg}")
    
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
            results["database"] = {"success": True, "message": f"SQLite doesn't need Docker"}
        
        if results.get("postgres") or results.get("mysql"):
            key = "postgres" if "postgres" in results else "mysql"
            if results[key]["success"]:
                logger.info(f"✓ {results[key]['message']}")
            else:
                logger.warning(f"✗ Database: {results[key]['message']}")
    
    return results
