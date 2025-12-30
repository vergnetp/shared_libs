"""
Job definitions for agent_service.

Uses app_kernel's JobRegistry for task registration.
Task processors live in workers/ module.

USAGE:
    from agent_service.jobs import registry, Tasks, enqueue_job
    
    # Enqueue document ingestion
    job_id = await enqueue_job(
        task=Tasks.DOCUMENT_INGEST,
        payload={"document_id": doc_id, "file_path": path},
        user=current_user,
        workspace_id=workspace_id,
    )
"""

from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass

from backend.app_kernel.jobs import JobRegistry, JobContext as KernelJobContext, get_job_client


# =============================================================================
# Task Definitions
# =============================================================================

class Tasks(str, Enum):
    """
    Standard task types.
    
    IMPLEMENTED:
    - DOCUMENT_INGEST: Process uploaded document (extract, chunk, embed)
    - DOCUMENT_REINDEX: Reindex existing document
    - SUMMARIZATION: Summarize thread messages
    - MEMORY_COMPACTION: Compact old messages into summary
    - CHAT_RESPONSE: Process chat message and generate response
    
    NOT YET IMPLEMENTED (defined for future use):
    - CONTENT_MODERATION
    - SUBMISSION_REVIEW
    - WEBHOOK_DELIVERY
    - EMAIL_SEND
    - METRICS_AGGREGATION
    - USAGE_REPORT
    """
    # Chat processing [IMPLEMENTED]
    CHAT_RESPONSE = "chat_response"
    
    # Document processing [IMPLEMENTED]
    DOCUMENT_INGEST = "document_ingest"
    DOCUMENT_REINDEX = "document_reindex"
    
    # Memory management [IMPLEMENTED]
    SUMMARIZATION = "summarization"
    MEMORY_COMPACTION = "memory_compaction"
    
    # Moderation [NOT YET IMPLEMENTED]
    CONTENT_MODERATION = "content_moderation"
    SUBMISSION_REVIEW = "submission_review"
    
    # Notifications [NOT YET IMPLEMENTED]
    WEBHOOK_DELIVERY = "webhook_delivery"
    EMAIL_SEND = "email_send"
    
    # Metrics [NOT YET IMPLEMENTED]
    METRICS_AGGREGATION = "metrics_aggregation"
    USAGE_REPORT = "usage_report"


class ActorType(str, Enum):
    """Who initiated the job."""
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
    WORKER = "worker"


class JobStatus(str, Enum):
    """Job lifecycle status."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =============================================================================
# Job Context (app-specific, extends kernel context)
# =============================================================================

@dataclass
class AgentJobContext:
    """
    App-specific job context.
    
    Extends kernel's JobContext with domain-specific fields.
    Workers use this to re-check scope before writing results.
    """
    workspace_id: Optional[str]
    user_id: str
    actor_type: ActorType
    
    # For audit trail
    request_id: Optional[str] = None
    thread_id: Optional[str] = None
    agent_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "actor_type": self.actor_type.value,
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "agent_id": self.agent_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentJobContext":
        return cls(
            workspace_id=data.get("workspace_id"),
            user_id=data["user_id"],
            actor_type=ActorType(data.get("actor_type", "user")),
            request_id=data.get("request_id"),
            thread_id=data.get("thread_id"),
            agent_id=data.get("agent_id"),
        )
    
    @classmethod
    def from_kernel_context(cls, ctx: KernelJobContext) -> "AgentJobContext":
        """Create from kernel's JobContext."""
        metadata = ctx.metadata or {}
        return cls(
            workspace_id=metadata.get("workspace_id"),
            user_id=ctx.user_id or "system",
            actor_type=ActorType(metadata.get("actor_type", "user")),
            request_id=metadata.get("request_id"),
            thread_id=metadata.get("thread_id"),
            agent_id=metadata.get("agent_id"),
        )


# =============================================================================
# Job Registry (app registers processors here)
# =============================================================================

# Create the registry - processors are registered below
registry = JobRegistry()


# =============================================================================
# Helper: Enqueue Jobs
# =============================================================================

async def enqueue_job(
    task: Tasks,
    payload: Dict[str, Any],
    *,
    user_id: str,
    workspace_id: str = None,
    request_id: str = None,
    thread_id: str = None,
    agent_id: str = None,
    priority: str = "normal",
) -> Optional[str]:
    """
    Enqueue a job with required context.
    
    Returns job_id if successful, None if job queue not available.
    """
    try:
        client = get_job_client()
    except RuntimeError:
        # Job queue not initialized (Redis not available)
        return None
    
    # Build metadata with app-specific context
    metadata = {
        "workspace_id": workspace_id,
        "actor_type": ActorType.USER.value,
        "request_id": request_id,
        "thread_id": thread_id,
        "agent_id": agent_id,
    }
    
    result = await client.enqueue(
        task_name=task.value,
        payload=payload,
        priority=priority,
        user_id=user_id,
        metadata=metadata,
    )
    
    return result.job_id if result else None


# =============================================================================
# Register Task Processors
# =============================================================================

# Import and register processors (deferred to avoid circular imports)
def _register_processors():
    """Register all task processors with the registry."""
    from .workers.documents import ingest_document, reindex_document
    from .workers.memory import summarize_thread, compact_memory
    from .workers.chat import process_chat
    
    registry.register(Tasks.DOCUMENT_INGEST.value, ingest_document)
    registry.register(Tasks.DOCUMENT_REINDEX.value, reindex_document)
    registry.register(Tasks.SUMMARIZATION.value, summarize_thread)
    registry.register(Tasks.MEMORY_COMPACTION.value, compact_memory)
    registry.register(Tasks.CHAT_RESPONSE.value, process_chat)


# Register on import
try:
    _register_processors()
except ImportError:
    # Workers not yet available (during initial setup)
    pass
