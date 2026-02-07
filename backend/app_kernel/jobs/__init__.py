"""
app_kernel.jobs - Job queue primitives.

This module provides:
- JobRegistry: Interface for registering task processors
- JobClient: Enqueue wrapper for apps
- Worker management: Start/stop workers
- Job routes: Status/list/cancel endpoints

The kernel provides mechanisms; apps provide task implementations.

Handler signature: (data, ctx, db)
- data: The dict you enqueued
- ctx: JobContext with job metadata
- db: Database connection (ready to use)

Usage:
    from app_kernel.jobs import JobRegistry, JobContext, get_job_client
    
    # Define handler
    async def process_document(data, ctx, db):
        doc = await db.find_entity("documents", data["doc_id"])
        # process...
        return {"status": "done"}
    
    # Register in create_service
    app = create_service(
        tasks={"process_document": process_document},
        ...
    )
    
    # Enqueue work
    client = get_job_client()
    await client.enqueue("process_document", {"doc_id": "123"})
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
    run_worker,
)
from .router import create_jobs_router

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
    "run_worker",
    
    # Router
    "create_jobs_router",
]
