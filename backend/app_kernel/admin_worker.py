"""
Admin Worker - Consumes audit and metering events from Redis, writes to database.

Two modes:
1. Embedded: runs as asyncio task inside FastAPI (use run_embedded)
2. Standalone: runs as separate process (use main / run_worker)

Usage (standalone):
    python -m app_kernel.admin_worker --redis-url redis://localhost:6379 --database-url postgresql://...
"""

import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_audit_event(db, event: dict) -> None:
    """Process an audit event and write to db."""
    await db.save_entity("kernel_audit_logs", {
        "id": str(uuid.uuid4()),
        "app": event.get("app"),
        "entity": event.get("entity"),
        "entity_id": event.get("entity_id"),
        "action": event.get("action"),
        "changes": json.dumps(event.get("changes")) if event.get("changes") else None,
        "old_snapshot": json.dumps(event.get("old_snapshot")) if event.get("old_snapshot") else None,
        "new_snapshot": json.dumps(event.get("new_snapshot")) if event.get("new_snapshot") else None,
        "user_id": event.get("user_id"),
        "request_id": event.get("request_id"),
        "timestamp": event.get("timestamp", _now_iso()),
    })


async def process_metering_event(db, event: dict) -> None:
    """Process a metering event and update usage_summary."""
    app = event.get("app")
    user_id = event.get("user_id")
    workspace_id = event.get("workspace_id")
    period = event.get("period")
    
    if event.get("type") == "request":
        await _increment_metric(db, app, workspace_id, user_id, period, "requests", 1)
        await _increment_metric(db, app, workspace_id, user_id, period, "latency_ms_total", event.get("latency_ms", 0))
        await _increment_metric(db, app, workspace_id, user_id, period, "bytes_in", event.get("bytes_in", 0))
        await _increment_metric(db, app, workspace_id, user_id, period, "bytes_out", event.get("bytes_out", 0))
        
        endpoint = event.get("endpoint", "")
        method = event.get("method", "")
        if endpoint:
            endpoint_key = f"endpoint:{method}:{endpoint}"
            await _increment_metric(db, app, workspace_id, user_id, period, endpoint_key, 1)
    
    elif event.get("type") == "custom":
        metrics = event.get("metrics", {})
        for metric, value in metrics.items():
            if value:
                await _increment_metric(db, app, workspace_id, user_id, period, metric, value)


async def _increment_metric(
    db,
    app: str,
    workspace_id: Optional[str],
    user_id: Optional[str],
    period: str,
    metric: str,
    value: int,
) -> None:
    """Increment a metric in usage_summary table."""
    where = "[app] = ? AND [period] = ? AND [metric] = ?"
    params = [app, period, metric]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    else:
        where += " AND [workspace_id] IS NULL"
    
    if user_id:
        where += " AND [user_id] = ?"
        params.append(user_id)
    else:
        where += " AND [user_id] IS NULL"
    
    results = await db.find_entities(
        "kernel_usage_summary",
        where_clause=where,
        params=tuple(params),
        limit=1,
    )
    
    now = _now_iso()
    
    if results:
        await db.save_entity("kernel_usage_summary", {
            "id": results[0]["id"],
            "value": (results[0].get("value") or 0) + value,
        })
    else:
        await db.save_entity("kernel_usage_summary", {
            "id": str(uuid.uuid4()),
            "app": app,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "period": period,
            "metric": metric,
            "value": value,
        })


async def _process_batch(db, redis_client, batch_size: int = 100) -> int:
    """Process a batch of events. Returns number processed."""
    processed = 0
    
    # Process audit events
    for _ in range(batch_size):
        event_data = await redis_client.rpop("admin:audit_events")
        if not event_data:
            break
        try:
            event = json.loads(event_data)
            await process_audit_event(db, event)
            processed += 1
        except Exception as e:
            logger.debug(f"Audit event error: {e}")
    
    # Process metering events
    for _ in range(batch_size):
        event_data = await redis_client.rpop("admin:metering_events")
        if not event_data:
            break
        try:
            event = json.loads(event_data)
            await process_metering_event(db, event)
            processed += 1
        except Exception as e:
            logger.debug(f"Metering event error: {e}")
    
    return processed


async def run_embedded(
    redis_url: str,
    app_name: str,
    logger,
    batch_size: int = 100,
    poll_interval: float = 0.5,
):
    """
    Run admin worker as embedded asyncio task (inside FastAPI).
    
    Uses app's existing db connection via raw_db_context.
    """
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis library not installed, admin worker disabled")
        return
    
    from .db import raw_db_context
    
    redis_client = aioredis.from_url(redis_url)
    
    try:
        while True:
            async with raw_db_context() as db:
                processed = await _process_batch(db, redis_client, batch_size)
            
            if processed == 0:
                await asyncio.sleep(poll_interval)
    
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.close()


async def run_worker(
    redis_url: str,
    database_url: str,
    batch_size: int = 100,
    poll_interval: float = 0.1,
):
    """
    Run admin worker as standalone process.
    
    Initializes its own db connection from database_url.
    """
    import redis.asyncio as aioredis
    from .db import init_db_session, raw_db_context
    from .bootstrap import _parse_database_url
    
    # Initialize db
    db_config = _parse_database_url(database_url)
    if db_config["type"] == "sqlite":
        init_db_session(database_name=db_config["name"], database_type="sqlite")
    else:
        init_db_session(
            database_name=db_config["name"],
            database_type=db_config["type"],
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
        )
    
    redis_client = aioredis.from_url(redis_url)
    logger.info(f"Admin worker started - Redis: {redis_url}, DB: {database_url}")
    
    try:
        while True:
            async with raw_db_context() as db:
                processed = await _process_batch(db, redis_client, batch_size)
            
            if processed == 0:
                await asyncio.sleep(poll_interval)
    
    finally:
        await redis_client.close()
        from .db import close_db
        await close_db()


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Admin worker for audit and metering")
    parser.add_argument("--redis-url", required=True, help="Redis URL")
    parser.add_argument("--database-url", required=True, help="Database URL")
    parser.add_argument("--batch-size", type=int, default=100, help="Events per batch")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Poll interval in seconds")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(loop)))
    
    try:
        loop.run_until_complete(run_worker(
            redis_url=args.redis_url,
            database_url=args.database_url,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval,
        ))
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


async def _shutdown(loop):
    """Graceful shutdown handler."""
    logger.info("Shutting down admin worker...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


if __name__ == "__main__":
    main()
