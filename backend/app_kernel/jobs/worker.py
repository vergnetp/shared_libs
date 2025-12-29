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
        
        async def wrapper(entity: Dict[str, Any]) -> Any:
            # Fail fast if task no longer registered
            if not registry.has(task_name):
                raise UnknownTaskError(f"Task '{task_name}' is not registered")
            
            # Extract job metadata
            payload = entity.get("payload", entity)
            
            # Build context (no domain assumptions - just pass through metadata)
            ctx = JobContext(
                job_id=entity.get("operation_id", "unknown"),
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
