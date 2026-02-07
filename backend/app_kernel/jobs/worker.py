"""
Job worker - Worker loop that uses the registry.

This wraps the underlying QueueWorker and dispatches work
to registered processors via the JobRegistry.

The kernel:
- Defines the worker interface
- Calls into the registry
- Never knows what tasks do
- Fails fast on unknown task names

IMPORTANT: Workers run as separate processes, not inside FastAPI.
The kernel provides the worker code; deployment decides how to run it.

Usage (separate worker process):
    # worker_main.py
    import asyncio
    from app_kernel.jobs import get_worker_manager
    
    async def main():
        manager = get_worker_manager()
        await manager.start()
        # Block until shutdown signal
        await asyncio.Event().wait()
    
    asyncio.run(main())
"""
from typing import Optional, Dict, Any
import asyncio

from .registry import JobRegistry, JobContext


class UnknownTaskError(Exception):
    """Raised when attempting to dispatch to an unregistered task."""
    pass


class JobWorkerManager:
    """
    Manager for job workers.
    
    Wraps the underlying queue worker and provides startup/shutdown.
    """
    
    def __init__(
        self,
        queue_worker = None,
        registry: Optional[JobRegistry] = None,
        queue_config = None
    ):
        """
        Initialize worker manager.
        
        Args:
            queue_worker: Underlying QueueWorker instance
            registry: Job registry for dispatching
            queue_config: Queue configuration
        """
        self._queue_worker = queue_worker
        self._registry = registry
        self._queue_config = queue_config
        self._running = False
    
    async def start(self):
        """Start the workers."""
        if self._running:
            return
        
        if not self._queue_worker:
            raise RuntimeError("Worker not initialized. Call init_app_kernel() first.")
        
        # Register the dispatch function with the queue config's callable registry
        if self._queue_config and self._registry:
            self._register_processors()
        
        await self._queue_worker.start()
        self._running = True
    
    async def stop(self):
        """Stop the workers gracefully."""
        if not self._running:
            return
        
        if self._queue_worker:
            await self._queue_worker.stop()
        
        self._running = False
    
    def _register_processors(self):
        """Register all task processors with the queue config."""
        if not self._queue_config or not self._registry:
            return
        
        # Register each task as a callable
        for task_name in self._registry:
            processor = self._registry.get(task_name)
            if processor:
                # Create a wrapper that builds JobContext
                wrapper = self._create_processor_wrapper(task_name, processor)
                self._queue_config.callables.register(wrapper, name=task_name)
    
    def _create_processor_wrapper(self, task_name: str, processor):
        """Create a wrapper that handles context creation."""
        registry = self._registry  # Capture reference
        
        async def wrapper(operation_id, payload_or_entity=None, *args, **kwargs) -> Any:
            # Fail fast if task no longer registered
            if not registry.has(task_name):
                raise UnknownTaskError(f"Task '{task_name}' is not registered")
            
            # queue_worker calls: processor(operation_id, payload)
            # where operation_id is a string UUID and payload is the entity dict
            
            # If called with two args (operation_id, payload), use the second arg as entity
            if payload_or_entity is not None:
                entity = payload_or_entity
            else:
                # Fallback: old calling convention where first arg is the entity
                entity = operation_id
                operation_id = "unknown"
            
            # Handle entity that might be JSON string (from Redis serialization)
            if isinstance(entity, str):
                import json
                try:
                    entity = json.loads(entity)
                except json.JSONDecodeError:
                    # If it's not valid JSON, wrap it as payload
                    entity = {"payload": entity}
            
            # Ensure entity is a dict
            if not isinstance(entity, dict):
                entity = {"payload": entity}
            
            # Extract job metadata
            payload = entity.get("payload", entity)
            
            # Also handle payload that might be JSON string
            if isinstance(payload, str):
                import json
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass  # Keep as string if not valid JSON
            
            # Build context - prefer operation_id from args, fall back to entity
            job_id = operation_id if operation_id != "unknown" else entity.get("operation_id", "unknown")
            
            ctx = JobContext(
                job_id=job_id,
                task_name=task_name,
                attempt=entity.get("attempts", 0) + 1,
                max_attempts=entity.get("max_attempts", 3),
                user_id=entity.get("user_id"),
                metadata=entity.get("metadata", {})
            )
            
            # Call the processor
            if asyncio.iscoroutinefunction(processor):
                return await processor(payload, ctx)
            else:
                # Run sync processor in thread
                return await asyncio.to_thread(processor, payload, ctx)
        
        # Preserve the name for the queue system
        wrapper.__name__ = task_name
        wrapper.__module__ = "app_kernel.jobs"
        
        return wrapper
    
    @property
    def is_running(self) -> bool:
        return self._running


# Module-level instance
_worker_manager: Optional[JobWorkerManager] = None


def init_worker_manager(
    queue_worker,
    registry: JobRegistry,
    queue_config
):
    """Initialize the worker manager. Called by init_app_kernel()."""
    global _worker_manager
    _worker_manager = JobWorkerManager(queue_worker, registry, queue_config)


def get_worker_manager() -> JobWorkerManager:
    """Get the worker manager."""
    if _worker_manager is None:
        raise RuntimeError("Worker manager not initialized. Call init_app_kernel() first.")
    return _worker_manager


async def start_workers():
    """
    Start the job workers.
    
    NOTE: This is typically called from a dedicated worker process,
    not from FastAPI startup. The kernel provides this code;
    your deployment decides how to run workers.
    """
    manager = get_worker_manager()
    await manager.start()


async def stop_workers():
    """
    Stop the job workers gracefully.
    
    NOTE: Called from worker process shutdown, not FastAPI.
    """
    manager = get_worker_manager()
    await manager.stop()


def _create_json_deserializing_wrapper(task_name: str, processor):
    """
    Create a wrapper that handles JSON deserialization from Redis.
    
    The job_queue library stores entities as JSON strings in Redis.
    This wrapper ensures the entity/payload are properly deserialized
    before being passed to the processor.
    
    NOTE: queue_worker calls processors as `processor(operation_id, payload)`
    where operation_id is a string and payload is the extracted entity data.
    This wrapper handles that calling convention.
    """
    import json
    
    async def wrapper(operation_id, payload_or_entity=None, *args, **kwargs) -> Any:
        # queue_worker calls: processor(operation_id, payload)
        # where operation_id is a string UUID and payload is the entity dict
        
        # If called with two args (operation_id, payload), use the second arg as entity
        if payload_or_entity is not None:
            entity = payload_or_entity
        else:
            # Fallback: old calling convention where first arg is the entity
            entity = operation_id
            operation_id = "unknown"
        
        # Handle entity that might be JSON string (from Redis serialization)
        if isinstance(entity, str):
            try:
                entity = json.loads(entity)
            except json.JSONDecodeError:
                entity = {"payload": entity}
        
        # Ensure entity is a dict
        if not isinstance(entity, dict):
            entity = {"payload": entity}
        
        # Extract payload - entity from JobClient has {"payload": ..., "task_name": ...}
        payload = entity.get("payload", entity)
        
        # Handle payload that might be JSON string (double serialization)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                pass  # Keep as string if not valid JSON
        
        # Build context - prefer operation_id from args, fall back to entity
        job_id = operation_id if operation_id != "unknown" else entity.get("operation_id", "unknown")
        
        ctx = JobContext(
            job_id=job_id,
            task_name=task_name,
            attempt=entity.get("attempts", 0) + 1,
            max_attempts=entity.get("max_attempts", 3),
            user_id=entity.get("user_id"),
            metadata=entity.get("metadata", {})
        )
        
        # Rename for clarity
        data = payload
        
        # Call the processor with db connection
        from ..db import db_context
        
        async with db_context() as db:
            if asyncio.iscoroutinefunction(processor):
                return await processor(data, ctx, db)
            else:
                return await asyncio.to_thread(processor, data, ctx, db)
    
    # Preserve name for queue system
    wrapper.__name__ = task_name
    wrapper.__module__ = "app_kernel.jobs"
    return wrapper


async def run_worker(
    tasks: Dict[str, Any],
    redis_url: str = None,
    key_prefix: str = None,
    worker_count: int = None,
    init_app = None,
    shutdown_app = None,
    log_level: str = None,
    manifest_path: str = None,
):
    """
    Run a worker process with the given tasks.
    
    This is the main entrypoint for worker processes. Handles all boilerplate:
    - Signal handling (graceful shutdown)
    - Logging setup
    - Queue configuration
    - Worker lifecycle
    - Auto-adds kernel integration tasks (e.g., store_request_metrics) if manifest provided
    
    Usage:
        # worker.py
        from ..jobs import run_worker
        from .workers.documents import ingest_document
        from .workers.chat import process_chat
        
        async def init():
            await init_dependencies(get_settings())
        
        async def shutdown():
            await shutdown_dependencies()
        
        if __name__ == "__main__":
            import asyncio
            asyncio.run(run_worker(
                tasks={
                    "document_ingest": ingest_document,
                    "chat_response": process_chat,
                },
                manifest_path="manifest.yaml",  # Auto-adds kernel integration tasks
                init_app=init,
                shutdown_app=shutdown,
            ))
    
    Args:
        tasks: Dict mapping task names to processor functions
        redis_url: Redis URL (default: REDIS_URL env var)
        key_prefix: Redis key prefix (default: REDIS_KEY_PREFIX env var or "app:")
        worker_count: Number of worker threads (default: 3)
        init_app: Optional async function to initialize app dependencies
        shutdown_app: Optional async function to cleanup app dependencies
        log_level: Logging level (default: INFO)
    """
    import sys
    import signal
    
    # Validate required args
    if not redis_url:
        raise ValueError("redis_url is required")
    
    # Defaults
    key_prefix = key_prefix or "queue:"
    worker_count = worker_count or 3
    log_level = log_level or "INFO"
    
    # Use backend.log module
    try:
        from ...log import info, error, warning, debug, critical, init_logger
        
        # Initialize the logger
        init_logger(service_name="worker", min_level=log_level, quiet_init=True)
        
        # Create wrapper object for job_queue (expects logger.info(), etc.)
        class LoggerWrapper:
            """Wraps module-level log functions as object methods."""
            @staticmethod
            def info(msg, **kwargs):
                info(msg, **kwargs)
            
            @staticmethod
            def error(msg, **kwargs):
                error(msg, **kwargs)
            
            @staticmethod
            def warning(msg, **kwargs):
                warning(msg, **kwargs)
            
            @staticmethod
            def debug(msg, **kwargs):
                debug(msg, **kwargs)
            
            @staticmethod
            def critical(msg, **kwargs):
                critical(msg, **kwargs)
        
        logger = LoggerWrapper()
    except ImportError:
        # Fallback to stdlib if log module not available
        import logging
        logging.basicConfig(
            level=getattr(logging, log_level),
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )
        _stdlib_logger = logging.getLogger("worker")
        
        # Wrap stdlib logger to handle kwargs
        class StdlibLoggerWrapper:
            def __init__(self, logger):
                self._logger = logger
            
            def _format(self, msg, **kwargs):
                if kwargs:
                    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
                    return f"{msg} [{extra}]"
                return msg
            
            def info(self, msg, **kwargs):
                self._logger.info(self._format(msg, **kwargs))
            
            def error(self, msg, **kwargs):
                self._logger.error(self._format(msg, **kwargs))
            
            def warning(self, msg, **kwargs):
                self._logger.warning(self._format(msg, **kwargs))
            
            def debug(self, msg, **kwargs):
                self._logger.debug(self._format(msg, **kwargs))
            
            def critical(self, msg, **kwargs):
                self._logger.critical(self._format(msg, **kwargs))
        
        logger = StdlibLoggerWrapper(_stdlib_logger)
    
    # Auto-add kernel integration tasks based on manifest
    all_tasks = dict(tasks)  # Copy to avoid mutating input
    if manifest_path:
        try:
            import yaml
            from pathlib import Path
            manifest_file = Path(manifest_path)
            if manifest_file.exists():
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f)
                
                # Check if request_metrics is enabled
                observability = manifest.get("observability", {})
                request_metrics = observability.get("request_metrics", {})
                if request_metrics.get("enabled", False):
                    from ..observability.request_metrics import store_request_metrics
                    all_tasks["store_request_metrics"] = store_request_metrics
                    logger.info("Added kernel task: store_request_metrics")
        except Exception as e:
            logger.warning(f"Could not load manifest for integration tasks: {e}")
    
    # Use all_tasks instead of tasks from here on
    tasks = all_tasks
    
    if not redis_url:
        logger.error("REDIS_URL environment variable required")
        sys.exit(1)
    
    logger.info(f"Starting worker with {worker_count} threads")
    logger.info(f"Redis: {redis_url}")
    logger.info(f"Key prefix: {key_prefix}")
    logger.info(f"Tasks: {list(tasks.keys())}")
    
    # Initialize app dependencies
    if init_app:
        await init_app()
    
    # Import job_queue components
    try:
        from ...job_queue import QueueWorker, QueueConfig
        from ...job_queue.config import (
            QueueRedisConfig,
            QueueWorkerConfig,
            QueueRetryConfig,
            QueueLoggingConfig,
        )
    except ImportError:
        try:
            from shared_libs.backend.job_queue import QueueWorker, QueueConfig
            from shared_libs.backend.job_queue.config import (
                QueueRedisConfig,
                QueueWorkerConfig,
                QueueRetryConfig,
                QueueLoggingConfig,
            )
        except ImportError:
            logger.error("job_queue module not available")
            sys.exit(1)
    
    # Create queue configuration
    config = QueueConfig(
        redis=QueueRedisConfig(url=redis_url, key_prefix=key_prefix),
        worker=QueueWorkerConfig(
            worker_count=worker_count,
            work_timeout=300,  # 5 minutes per task
        ),
        retry=QueueRetryConfig.exponential(
            max_attempts=3,
            min_delay=5.0,
            max_delay=300.0,
        ),
        logging=QueueLoggingConfig(logger=logger),
    )
    
    logger.info(f"Queue config initialized: redis={redis_url}, key_prefix={key_prefix}, workers={worker_count}")
    
    # Register processors with JSON deserialization wrapper
    for task_name, processor in tasks.items():
        wrapped = _create_json_deserializing_wrapper(task_name, processor)
        config.callables.register(wrapped, name=task_name)
    
    # Create worker
    worker = QueueWorker(config=config)
    
    # Handle shutdown gracefully
    shutdown_event = asyncio.Event()
    
    def handle_signal(sig):
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()
    
    # Signal handlers
    loop = asyncio.get_event_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))
    else:
        def windows_handler(signum, frame):
            handle_signal(signum)
        signal.signal(signal.SIGINT, windows_handler)
        signal.signal(signal.SIGTERM, windows_handler)
    
    try:
        logger.info("Worker starting...")
        await worker.start()
        logger.info("Worker started, processing jobs...")
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        raise
    finally:
        logger.info("Worker stopping...")
        await worker.stop()
        if shutdown_app:
            await shutdown_app()
        logger.info("Worker stopped")
