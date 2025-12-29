"""
app_kernel.jobs - Job queue primitives.

This module provides:
- JobRegistry: Interface for registering task processors
- JobClient: Enqueue wrapper for apps
- Worker management: Start/stop workers

The kernel provides mechanisms; apps provide task implementations.

Usage:
    from app_kernel.jobs import JobRegistry, get_job_client, start_workers
    
    # Create registry and register tasks
    registry = JobRegistry()
    
    @registry.task("process_document")
    async def process_document(payload, ctx):
        ...
    
    # Pass to kernel
    init_app_kernel(app, settings, registry)
    
    # Enqueue work
    client = get_job_client()
    await client.enqueue("process_document", {"doc_id": "123"})
    
    # Start workers
    await start_workers()
"""

from .registry import JobRegistry, JobContext, ProcessorFunc
from .client import (
    JobClient,
    EnqueueResult,
    init_job_client,
    get_job_client,
)
from .worker import (
    JobWorkerManager,
    UnknownTaskError,
    init_worker_manager,
    get_worker_manager,
    start_workers,
    stop_workers,
)

__all__ = [
    # Registry
    "JobRegistry",
    "JobContext",
    "ProcessorFunc",
    
    # Client
    "JobClient",
    "EnqueueResult",
    "init_job_client",
    "get_job_client",
    
    # Worker
    "JobWorkerManager",
    "UnknownTaskError",
    "init_worker_manager",
    "get_worker_manager",
    "start_workers",
    "stop_workers",
]
