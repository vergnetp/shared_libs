"""Simple job queue with optional Redis backend."""

import json
import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    """A queued job."""
    id: str
    name: str
    payload: dict
    status: JobStatus = JobStatus.PENDING
    priority: int = 0  # Higher = more urgent
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Any] = None
    retries: int = 0
    max_retries: int = 3
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "payload": self.payload,
            "status": self.status.value,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "result": self.result,
            "retries": self.retries,
            "max_retries": self.max_retries,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(
            id=data["id"],
            name=data["name"],
            payload=data["payload"],
            status=JobStatus(data["status"]),
            priority=data.get("priority", 0),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            error=data.get("error"),
            result=data.get("result"),
            retries=data.get("retries", 0),
            max_retries=data.get("max_retries", 3),
        )


class JobQueueBackend(ABC):
    """Abstract backend for job queue storage."""
    
    @abstractmethod
    async def push(self, job: Job):
        """Add job to queue."""
        pass
    
    @abstractmethod
    async def pop(self) -> Optional[Job]:
        """Get next job from queue (removes from queue)."""
        pass
    
    @abstractmethod
    async def peek(self) -> Optional[Job]:
        """Get next job without removing."""
        pass
    
    @abstractmethod
    async def update(self, job: Job):
        """Update job status."""
        pass
    
    @abstractmethod
    async def get(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        pass
    
    @abstractmethod
    async def size(self) -> int:
        """Get queue size."""
        pass


class InMemoryQueueBackend(JobQueueBackend):
    """
    In-memory job queue.
    
    Good for development/testing. State lost on restart.
    """
    
    def __init__(self):
        self._queue: list[Job] = []
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
    
    async def push(self, job: Job):
        async with self._lock:
            self._jobs[job.id] = job
            self._queue.append(job)
            # Sort by priority (descending) then created_at (ascending)
            self._queue.sort(key=lambda j: (-j.priority, j.created_at))
    
    async def pop(self) -> Optional[Job]:
        async with self._lock:
            # Find first pending job
            for i, job in enumerate(self._queue):
                if job.status == JobStatus.PENDING:
                    job.status = JobStatus.PROCESSING
                    job.started_at = datetime.utcnow()
                    return job
            return None
    
    async def peek(self) -> Optional[Job]:
        async with self._lock:
            for job in self._queue:
                if job.status == JobStatus.PENDING:
                    return job
            return None
    
    async def update(self, job: Job):
        async with self._lock:
            self._jobs[job.id] = job
    
    async def get(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)
    
    async def size(self) -> int:
        async with self._lock:
            return sum(1 for j in self._queue if j.status == JobStatus.PENDING)


class RedisQueueBackend(JobQueueBackend):
    """
    Redis-backed job queue.
    
    Uses your queue module's QueueRedisConfig.
    
    Example:
        from processing.queue import QueueRedisConfig
        
        redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
        backend = RedisQueueBackend(redis_config)
        queue = JobQueue(backend=backend)
    """
    
    def __init__(self, redis_config: Any, queue_name: str = "ai_agents"):
        self._config = redis_config
        self._client = None
        self._queue_name = queue_name
    
    async def _ensure_client(self):
        if self._client is None:
            if hasattr(self._config, 'client') and self._config.client:
                self._client = self._config.client
            elif hasattr(self._config, 'url') and self._config.url:
                import redis.asyncio as aioredis
                self._client = aioredis.from_url(self._config.url)
            else:
                raise ValueError("Redis config must have url or client")
        return self._client
    
    def _queue_key(self) -> str:
        return f"jobqueue:{self._queue_name}"
    
    def _job_key(self, job_id: str) -> str:
        return f"job:{self._queue_name}:{job_id}"
    
    async def push(self, job: Job):
        client = await self._ensure_client()
        
        # Store job data
        await client.set(self._job_key(job.id), json.dumps(job.to_dict()))
        
        # Add to sorted set (score = -priority * 1e12 + timestamp for ordering)
        score = -job.priority * 1e12 + job.created_at.timestamp()
        await client.zadd(self._queue_key(), {job.id: score})
    
    async def pop(self) -> Optional[Job]:
        client = await self._ensure_client()
        
        # Get first pending job
        job_ids = await client.zrange(self._queue_key(), 0, 0)
        if not job_ids:
            return None
        
        job_id = job_ids[0]
        if isinstance(job_id, bytes):
            job_id = job_id.decode()
        
        # Get job data
        job_data = await client.get(self._job_key(job_id))
        if not job_data:
            # Job data missing, remove from queue
            await client.zrem(self._queue_key(), job_id)
            return await self.pop()  # Try next
        
        if isinstance(job_data, bytes):
            job_data = job_data.decode()
        
        job = Job.from_dict(json.loads(job_data))
        
        if job.status != JobStatus.PENDING:
            # Already processing/completed, remove and try next
            await client.zrem(self._queue_key(), job_id)
            return await self.pop()
        
        # Update status
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.utcnow()
        await client.set(self._job_key(job.id), json.dumps(job.to_dict()))
        
        # Remove from pending queue
        await client.zrem(self._queue_key(), job_id)
        
        return job
    
    async def peek(self) -> Optional[Job]:
        client = await self._ensure_client()
        
        job_ids = await client.zrange(self._queue_key(), 0, 0)
        if not job_ids:
            return None
        
        job_id = job_ids[0]
        if isinstance(job_id, bytes):
            job_id = job_id.decode()
        
        return await self.get(job_id)
    
    async def update(self, job: Job):
        client = await self._ensure_client()
        await client.set(self._job_key(job.id), json.dumps(job.to_dict()))
    
    async def get(self, job_id: str) -> Optional[Job]:
        client = await self._ensure_client()
        job_data = await client.get(self._job_key(job_id))
        
        if not job_data:
            return None
        
        if isinstance(job_data, bytes):
            job_data = job_data.decode()
        
        return Job.from_dict(json.loads(job_data))
    
    async def size(self) -> int:
        client = await self._ensure_client()
        return await client.zcard(self._queue_key())


class JobQueue:
    """
    Simple job queue for background processing.
    
    Example (in-memory):
        queue = JobQueue()
        
        # Register handlers
        @queue.handler("summarize")
        async def handle_summarize(payload):
            return await generate_summary(payload["thread_id"])
        
        # Enqueue jobs
        job_id = await queue.enqueue("summarize", {"thread_id": "abc"})
        
        # Start worker
        await queue.start_worker()
    
    Example (Redis):
        from processing.queue import QueueRedisConfig
        
        redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
        queue = JobQueue(backend=RedisQueueBackend(redis_config))
    """
    
    def __init__(self, backend: JobQueueBackend = None):
        self._backend = backend or InMemoryQueueBackend()
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
    
    def handler(self, name: str):
        """Decorator to register a job handler."""
        def decorator(func: Callable):
            self._handlers[name] = func
            return func
        return decorator
    
    def register_handler(self, name: str, func: Callable):
        """Register a job handler."""
        self._handlers[name] = func
    
    async def enqueue(
        self,
        name: str,
        payload: dict,
        priority: int = 0,
        max_retries: int = 3,
    ) -> str:
        """
        Add a job to the queue.
        
        Args:
            name: Handler name
            payload: Job data
            priority: Higher = more urgent (default 0)
            max_retries: Max retry attempts (default 3)
            
        Returns:
            Job ID
        """
        job = Job(
            id=str(uuid.uuid4()),
            name=name,
            payload=payload,
            priority=priority,
            max_retries=max_retries,
        )
        await self._backend.push(job)
        return job.id
    
    async def get_status(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        return await self._backend.get(job_id)
    
    async def _process_job(self, job: Job):
        """Process a single job."""
        handler = self._handlers.get(job.name)
        
        if not handler:
            job.status = JobStatus.FAILED
            job.error = f"No handler for job type: {job.name}"
            job.completed_at = datetime.utcnow()
            await self._backend.update(job)
            return
        
        try:
            result = await handler(job.payload)
            job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.utcnow()
        except Exception as e:
            job.retries += 1
            if job.retries >= job.max_retries:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.completed_at = datetime.utcnow()
            else:
                # Re-queue for retry
                job.status = JobStatus.PENDING
                job.started_at = None
                await self._backend.push(job)
                return
        
        await self._backend.update(job)
    
    async def _worker_loop(self, poll_interval: float = 1.0):
        """Background worker loop."""
        while self._running:
            job = await self._backend.pop()
            
            if job:
                await self._process_job(job)
            else:
                await asyncio.sleep(poll_interval)
    
    async def start_worker(self, poll_interval: float = 1.0):
        """Start background worker."""
        if self._running:
            return
        
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(poll_interval))
    
    async def stop_worker(self):
        """Stop background worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
    
    async def process_one(self) -> bool:
        """
        Process one job synchronously (for testing/debugging).
        
        Returns:
            True if a job was processed, False if queue empty
        """
        job = await self._backend.pop()
        if job:
            await self._process_job(job)
            return True
        return False
    
    @property
    def queue_size(self) -> int:
        """Get current queue size (sync wrapper)."""
        return asyncio.get_event_loop().run_until_complete(self._backend.size())
