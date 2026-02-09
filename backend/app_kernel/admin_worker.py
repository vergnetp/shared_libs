"""
Admin Worker - Consumes audit and metering events from Redis, writes to database.

Runs as a separate process. Handles:
- audit_logs (entity changes)
- usage_metrics (API call counts)

Usage:
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


async def process_audit_event(admin_db, event: dict) -> None:
    """Process an audit event and write to admin_db."""
    await admin_db.save_entity("kernel_audit_logs", {
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
        "created_at": _now_iso(),
    })


async def process_metering_event(admin_db, event: dict) -> None:
    """Process a metering event and update usage_summary in admin_db."""
    app = event.get("app")
    user_id = event.get("user_id")
    workspace_id = event.get("workspace_id")
    period = event.get("period")
    
    if event.get("type") == "request":
        # Track request metrics
        await _increment_metric(admin_db, app, workspace_id, user_id, period, "requests", 1)
        await _increment_metric(admin_db, app, workspace_id, user_id, period, "latency_ms_total", event.get("latency_ms", 0))
        await _increment_metric(admin_db, app, workspace_id, user_id, period, "bytes_in", event.get("bytes_in", 0))
        await _increment_metric(admin_db, app, workspace_id, user_id, period, "bytes_out", event.get("bytes_out", 0))
        
        # Track by endpoint
        endpoint = event.get("endpoint", "")
        method = event.get("method", "")
        if endpoint:
            endpoint_key = f"endpoint:{method}:{endpoint}"
            await _increment_metric(admin_db, app, workspace_id, user_id, period, endpoint_key, 1)
    
    elif event.get("type") == "custom":
        # Track custom metrics
        metrics = event.get("metrics", {})
        for metric, value in metrics.items():
            if value:
                await _increment_metric(admin_db, app, workspace_id, user_id, period, metric, value)


async def _increment_metric(
    admin_db,
    app: str,
    workspace_id: Optional[str],
    user_id: Optional[str],
    period: str,
    metric: str,
    value: int,
) -> None:
    """Increment a metric in usage_summary table."""
    # Build unique lookup
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
    
    results = await admin_db.find_entities(
        "kernel_usage_summary",
        where_clause=where,
        params=tuple(params),
        limit=1,
    )
    
    now = _now_iso()
    
    if results:
        # Update existing
        await admin_db.save_entity("kernel_usage_summary", {
            "id": results[0]["id"],
            "value": (results[0].get("value") or 0) + value,
            "updated_at": now,
        })
    else:
        # Create new
        await admin_db.save_entity("kernel_usage_summary", {
            "id": str(uuid.uuid4()),
            "app": app,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "period": period,
            "metric": metric,
            "value": value,
            "created_at": now,
            "updated_at": now,
        })


async def run_worker(
    redis_url: str,
    admin_db_url: str,
    batch_size: int = 100,
    poll_interval: float = 0.1,
):
    """
    Run the admin worker.
    
    Consumes events from Redis queues and writes to admin_db.
    """
    import redis.asyncio as redis
    
    # Connect to Redis
    redis_client = redis.from_url(redis_url)
    
    # Connect to admin_db
    # TODO: Use databases library connection
    from databases import Database
    admin_db = Database(admin_db_url)
    await admin_db.connect()
    
    # Tables are created by AutoMigrator at app startup (kernel_audit_logs, kernel_usage_events etc.)
    
    logger.info(f"Admin worker started - Redis: {redis_url}, DB: {admin_db_url}")
    
    try:
        while True:
            processed = 0
            
            # Process audit events
            for _ in range(batch_size):
                event_data = await redis_client.rpop("admin:audit_events")
                if not event_data:
                    break
                try:
                    event = json.loads(event_data)
                    await process_audit_event(admin_db, event)
                    processed += 1
                except Exception as e:
                    logger.error(f"Failed to process audit event: {e}")
            
            # Process metering events
            for _ in range(batch_size):
                event_data = await redis_client.rpop("admin:metering_events")
                if not event_data:
                    break
                try:
                    event = json.loads(event_data)
                    await process_metering_event(admin_db, event)
                    processed += 1
                except Exception as e:
                    logger.error(f"Failed to process metering event: {e}")
            
            # Sleep if no events
            if processed == 0:
                await asyncio.sleep(poll_interval)
    
    finally:
        await admin_db.disconnect()
        await redis_client.close()


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
    
    # Handle graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(loop)))
    
    try:
        loop.run_until_complete(run_worker(
            redis_url=args.redis_url,
            admin_db_url=args.database_url,
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
