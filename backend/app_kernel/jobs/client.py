"""
Job client - Enqueue wrapper for applications.

This is the interface apps use to enqueue jobs. It wraps the
underlying job_queue module and provides a simpler API.

Usage:
    from app_kernel.jobs import job_client
    
    # Enqueue a job
    job_id = await job_client.enqueue(
        "process_document",
        {"document_id": "123", "action": "ocr"},
        user_id="user-456"
    )
    
    # Enqueue with priority
    job_id = await job_client.enqueue(
        "send_notification",
        {"user_id": "789", "message": "Hello"},
        priority="high"
    )
"""
from typing import Any, Dict, Optional, Callable
from dataclasses import dataclass
import asyncio
import uuid
import time

from .registry import JobRegistry


@dataclass
class EnqueueResult:
    """Result of enqueueing a job."""
    job_id: str
    task_name: str
    status: str = "queued"
    queue_name: Optional[str] = None


class JobClient:
    """
    Client for enqueueing jobs.
    
    Initialized by init_app_kernel() with the underlying queue manager.
    Apps use this to enqueue work without touching the queue directly.
    """
    
    def __init__(
        self,
        queue_manager = None,
        registry: Optional[JobRegistry] = None
    ):
        """
        Initialize job client.
        
        Args:
            queue_manager: Underlying QueueManager instance
            registry: Job registry for validation
        """
        self._queue_manager = queue_manager
        self._registry = registry
        self._initialized = queue_manager is not None
    
    def _ensure_initialized(self):
        """Raise if not initialized."""
        if not self._initialized:
            raise RuntimeError(
                "JobClient not initialized. Call init_app_kernel() first."
            )
    
    async def enqueue(
        self,
        task_name: str,
        payload: Dict[str, Any],
        *,
        job_id: Optional[str] = None,
        priority: str = "normal",
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
        max_attempts: Optional[int] = None,
        delay_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        on_success: Optional[str] = None,
        on_failure: Optional[str] = None,
    ) -> EnqueueResult:
        """
        Enqueue a job for processing.
        
        Args:
            task_name: Name of the registered task
            payload: Data to pass to the processor
            job_id: Optional custom job ID (auto-generated if not provided)
            priority: "high", "normal", or "low"
            user_id: Optional user ID for context
            timeout: Optional timeout override
            max_attempts: Optional max attempts override
            delay_seconds: Optional delay before processing
            metadata: Optional additional metadata
            on_success: Optional task to run on success
            on_failure: Optional task to run on failure
        
        Returns:
            EnqueueResult with job_id and status
        
        Raises:
            ValueError: If task_name is not registered
            RuntimeError: If client not initialized
        """
        self._ensure_initialized()
        
        # Validate task is registered (if we have a registry)
        if self._registry and not self._registry.has(task_name):
            raise ValueError(f"Task '{task_name}' is not registered")
        
        # Build the entity
        entity = {
            "payload": payload,
            "task_name": task_name,
            "enqueued_at": time.time(),
        }
        
        if user_id:
            entity["user_id"] = user_id
        
        if metadata:
            entity["metadata"] = metadata
        
        if delay_seconds:
            entity["delay_until"] = time.time() + delay_seconds
        
        # Get task metadata for defaults
        task_meta = {}
        if self._registry:
            task_meta = self._registry.get_metadata(task_name) or {}
        
        # Build retry config
        retry_config = {}
        if max_attempts is not None:
            retry_config["max_attempts"] = max_attempts
        elif task_meta.get("max_attempts"):
            retry_config["max_attempts"] = task_meta["max_attempts"]
        
        if timeout is not None:
            retry_config["timeout"] = timeout
        elif task_meta.get("timeout"):
            retry_config["timeout"] = task_meta["timeout"]
        
        # Enqueue via queue manager (runs sync, so wrap in thread)
        result = await asyncio.to_thread(
            self._queue_manager.enqueue,
            entity=entity,
            processor=task_name,
            queue_name=task_name,
            priority=priority,
            operation_id=job_id,
            retry_config=retry_config if retry_config else None,
            on_success=on_success,
            on_failure=on_failure,
        )
        
        return EnqueueResult(
            job_id=result.get("operation_id", job_id or str(uuid.uuid4())),
            task_name=task_name,
            status=result.get("status", "queued"),
            queue_name=task_name
        )
    
    async def enqueue_many(
        self,
        task_name: str,
        payloads: list[Dict[str, Any]],
        *,
        priority: str = "normal",
        user_id: Optional[str] = None,
    ) -> list[EnqueueResult]:
        """
        Enqueue multiple jobs efficiently.
        
        Args:
            task_name: Name of the registered task
            payloads: List of payloads to enqueue
            priority: Priority for all jobs
            user_id: Optional user ID for context
        
        Returns:
            List of EnqueueResult for each job
        """
        self._ensure_initialized()
        
        if self._registry and not self._registry.has(task_name):
            raise ValueError(f"Task '{task_name}' is not registered")
        
        # Build entities
        entities = []
        for payload in payloads:
            entity = {
                "payload": payload,
                "task_name": task_name,
                "enqueued_at": time.time(),
            }
            if user_id:
                entity["user_id"] = user_id
            entities.append(entity)
        
        # Batch enqueue
        results = await asyncio.to_thread(
            self._queue_manager.enqueue_batch,
            entities=entities,
            processor=task_name,
            queue_name=task_name,
            priority=priority,
        )
        
        return [
            EnqueueResult(
                job_id=r.get("operation_id", str(uuid.uuid4())),
                task_name=task_name,
                status=r.get("status", "queued"),
                queue_name=task_name
            )
            for r in results
        ]
    
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get status of all queues."""
        self._ensure_initialized()
        return await asyncio.to_thread(self._queue_manager.get_queue_status)


# Module-level instance, initialized by init_app_kernel()
_job_client: Optional[JobClient] = None


def init_job_client(queue_manager, registry: Optional[JobRegistry] = None):
    """Initialize the job client. Called by init_app_kernel()."""
    global _job_client
    _job_client = JobClient(queue_manager, registry)


def get_job_client() -> JobClient:
    """Get the initialized job client."""
    if _job_client is None:
        raise RuntimeError("Job client not initialized. Call init_app_kernel() first.")
    return _job_client


# Convenience alias
job_client = property(lambda self: get_job_client())
