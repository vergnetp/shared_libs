#!/usr/bin/env python3
"""
Deploy API Worker - Background job processor.

Runs deployment and rollback jobs from the queue.

Usage:
    python -m services.deploy_api.worker
    
Environment variables:
    REDIS_URL - Redis connection URL (required)
    DATABASE_PATH - SQLite database path
"""

import asyncio
import sys

from backend.app_kernel.jobs import run_worker
from backend.app_kernel import get_logger
from backend.app_kernel.db import init_db_session, close_db

from .config import get_settings
from .src.workers import TASKS


async def init_app():
    """Initialize database for worker processes."""
    settings = get_settings()
    settings.ensure_data_dir()
    
    # Use kernel's DB session (same as main app)
    init_db_session(
        database_name=settings.database_path,
        database_type=settings.database_type,
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password,
    )
    
    get_logger().info("Worker database initialized")


async def shutdown_app():
    """Cleanup database connections."""
    await close_db()
    get_logger().info("Worker database closed")


async def main():
    """Run the worker process."""
    settings = get_settings()
    logger = get_logger()
    
    if not settings.redis_url:
        logger.error("REDIS_URL not configured - workers require Redis")
        sys.exit(1)
    
    logger.info(f"Starting deploy-api worker with {len(TASKS)} tasks")
    logger.info(f"Tasks: {list(TASKS.keys())}")
    
    # Run worker with init/shutdown hooks
    await run_worker(
        tasks=TASKS,
        redis_url=settings.redis_url,
        init_app=init_app,
        shutdown_app=shutdown_app,
    )


if __name__ == "__main__":
    asyncio.run(main())
