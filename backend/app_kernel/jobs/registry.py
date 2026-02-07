"""
Job registry interface.

Defines the protocol for task registration. Apps provide the mapping
from task names to processor functions; the kernel uses this to
dispatch work.

IMPORTANT: 
- The kernel NEVER registers tasks or knows task names.
- It only calls into the registry provided by the app.
- Registry metadata (timeout, max_attempts) is ADVISORY only.
- The kernel is NOT a scheduler - it dispatches and fails fast.

Usage:
    # In your app
    from app_kernel.jobs import JobRegistry, JobContext
    
    registry = JobRegistry()
    
    @registry.task("process_document")
    async def process_document(data: dict, ctx: JobContext, db) -> dict:
        # data: the dict you enqueued
        # ctx: job metadata (job_id, attempt, max_attempts, etc.)
        # db: database connection (ready to use)
        doc = await db.find_entity("documents", data["doc_id"])
        return {"status": "done"}
    
    # Or register manually
    registry.register("send_email", send_email_handler)
    
    # Pass to create_service
    app = create_service(
        name="my-app",
        tasks={"send_email": send_email_handler},
        ...
    )
"""
from typing import Callable, Dict, Any, Optional, Protocol, Union, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
import asyncio
import functools

UTC = timezone.utc


@dataclass
class JobContext:
    """
    Context/metadata passed to job handlers.
    
    Job handlers receive: (data, ctx, db)
    - data: The dict you enqueued
    - ctx: This JobContext with job metadata
    - db: Database connection (from db_context pool)
    """
    job_id: str
    task_name: str
    attempt: int = 1
    max_attempts: int = 3
    enqueued_at: Optional[datetime] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    # Optional user context (if job was enqueued with user info)
    user_id: Optional[str] = None
    
    # Additional metadata from enqueue
    metadata: Dict[str, Any] = field(default_factory=dict)


# Type for processor functions
# Signature: (data, ctx, db) -> result
ProcessorFunc = Callable[[Dict[str, Any], JobContext, Any], Union[Any, Awaitable[Any]]]


class JobRegistry:
    """
    Registry mapping task names to processor functions.
    
    Apps create this and pass it to init_app_kernel().
    The kernel uses it to dispatch work but never modifies it.
    """
    
    def __init__(self):
        self._processors: Dict[str, ProcessorFunc] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
    
    def register(
        self,
        name: str,
        processor: ProcessorFunc,
        *,
        timeout: Optional[float] = None,
        max_attempts: Optional[int] = None,
        description: Optional[str] = None
    ) -> None:
        """
        Register a task processor.
        
        Args:
            name: Unique task name
            processor: Function that processes the task
            timeout: Advisory timeout hint (not enforced by kernel)
            max_attempts: Advisory retry hint (not enforced by kernel)
            description: Optional description for docs
        
        NOTE: timeout and max_attempts are advisory metadata only.
        The kernel does not schedule or enforce these values.
        """
        if name in self._processors:
            raise ValueError(f"Task '{name}' is already registered")
        
        self._processors[name] = processor
        self._metadata[name] = {
            "timeout": timeout,
            "max_attempts": max_attempts,
            "description": description,
            "is_async": asyncio.iscoroutinefunction(processor)
        }
    
    def task(
        self,
        name: str,
        *,
        timeout: Optional[float] = None,
        max_attempts: Optional[int] = None,
        description: Optional[str] = None
    ) -> Callable[[ProcessorFunc], ProcessorFunc]:
        """
        Decorator for registering a task processor.
        
        Usage:
            @registry.task("process_document")
            async def process_document(payload, ctx):
                ...
        """
        def decorator(func: ProcessorFunc) -> ProcessorFunc:
            self.register(
                name, 
                func,
                timeout=timeout,
                max_attempts=max_attempts,
                description=description
            )
            return func
        return decorator
    
    def get(self, name: str) -> Optional[ProcessorFunc]:
        """Get a processor by name."""
        return self._processors.get(name)
    
    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a task."""
        return self._metadata.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a task is registered."""
        return name in self._processors
    
    @property
    def tasks(self) -> Dict[str, ProcessorFunc]:
        """Get all registered tasks (read-only view)."""
        return dict(self._processors)
    
    def __contains__(self, name: str) -> bool:
        return self.has(name)
    
    def __len__(self) -> int:
        return len(self._processors)
    
    def __iter__(self):
        return iter(self._processors)
