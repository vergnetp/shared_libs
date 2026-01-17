"""
Stream Context - Serializable context for background workers.

StreamContext is the main interface for emitting events from background tasks.
It carries the channel_id and debugging metadata, and provides convenient
methods for emitting events.

Design:
- Serializable to/from dict (can be passed through job queue)
- Carries debugging context (workspace/project/env/service)
- Optionally persists events to OpenSearch
- Thread-safe (uses SyncStreamChannel internally)

Usage:
    # In FastAPI route
    ctx = StreamContext.create(
        workspace_id=user.id,
        project="myapp",
        env="prod",
        service="api",
    )
    
    queue_manager.enqueue(
        entity={"stream_ctx": ctx.to_dict(), "config": ...},
        processor=deploy_task,
    )
    
    return await sse_response(ctx.channel_id)
    
    # In background worker
    def deploy_task(entity: dict):
        ctx = StreamContext.from_dict(entity["stream_ctx"])
        
        ctx.log("Starting deployment...")
        ctx.progress(10, step="building")
        # ... do work ...
        ctx.complete(success=True, deployment_id="abc")
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Callable
from datetime import datetime

from .events import StreamEvent, EventType
from .channels import SyncStreamChannel, get_sync_channel


@dataclass
class StreamContext:
    """
    Serializable context for streaming from background workers.
    
    Attributes:
        channel_id: Unique channel identifier for Pub/Sub
        workspace_id: Tenant/user ID (for debugging/logging)
        project: Project name (for debugging/logging)
        env: Environment (prod, uat, dev, etc.)
        service: Service name (for debugging/logging)
        persist_events: Whether to persist events to storage (OpenSearch)
        created_at: When the context was created
        extra: Additional custom metadata
    """
    channel_id: str
    workspace_id: Optional[str] = None
    project: Optional[str] = None
    env: Optional[str] = None
    service: Optional[str] = None
    persist_events: bool = False
    created_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    
    # Internal state (not serialized)
    _channel: Optional[SyncStreamChannel] = field(default=None, repr=False)
    _storage: Optional[Any] = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat() + "Z"
    
    @classmethod
    def create(
        cls,
        workspace_id: Optional[str] = None,
        project: Optional[str] = None,
        env: Optional[str] = None,
        service: Optional[str] = None,
        persist_events: bool = False,
        channel_id: Optional[str] = None,
        **extra,
    ) -> 'StreamContext':
        """
        Create a new StreamContext with auto-generated channel_id.
        
        Args:
            workspace_id: Tenant/user ID
            project: Project name
            env: Environment name
            service: Service name
            persist_events: Whether to persist to storage
            channel_id: Optional explicit channel_id (auto-generated if not provided)
            **extra: Additional metadata
            
        Returns:
            New StreamContext instance
        """
        return cls(
            channel_id=channel_id or str(uuid.uuid4()),
            workspace_id=workspace_id,
            project=project,
            env=env,
            service=service,
            persist_events=persist_events,
            extra=extra,
        )
    
    # =========================================================================
    # Serialization
    # =========================================================================
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to dictionary for passing through job queue.
        
        Excludes internal state (_channel, _storage, _closed).
        """
        return {
            "channel_id": self.channel_id,
            "workspace_id": self.workspace_id,
            "project": self.project,
            "env": self.env,
            "service": self.service,
            "persist_events": self.persist_events,
            "created_at": self.created_at,
            "extra": self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StreamContext':
        """
        Deserialize from dictionary.
        
        Used in background workers to reconstruct context.
        """
        return cls(
            channel_id=data.get("channel_id", str(uuid.uuid4())),
            workspace_id=data.get("workspace_id"),
            project=data.get("project"),
            env=data.get("env"),
            service=data.get("service"),
            persist_events=data.get("persist_events", False),
            created_at=data.get("created_at"),
            extra=data.get("extra", {}),
        )
    
    # =========================================================================
    # Properties
    # =========================================================================
    
    @property
    def debug_context(self) -> Dict[str, Any]:
        """Get debugging context dict (injected into all events)."""
        ctx = {}
        if self.workspace_id:
            ctx["workspace_id"] = self.workspace_id
        if self.project:
            ctx["project"] = self.project
        if self.env:
            ctx["env"] = self.env
        if self.service:
            ctx["service"] = self.service
        if self.extra:
            ctx.update(self.extra)
        return ctx
    
    @property
    def namespace(self) -> str:
        """Get namespace string (workspace_project_env_service)."""
        parts = []
        if self.workspace_id:
            parts.append(str(self.workspace_id)[:8])
        if self.project:
            parts.append(self.project)
        if self.env:
            parts.append(self.env)
        if self.service:
            parts.append(self.service)
        return "_".join(parts) if parts else self.channel_id[:8]
    
    @property
    def is_closed(self) -> bool:
        """Whether complete() has been called."""
        return self._closed
    
    # =========================================================================
    # Channel Management
    # =========================================================================
    
    def _get_channel(self) -> SyncStreamChannel:
        """Get or create the channel for publishing."""
        if self._channel is None:
            self._channel = get_sync_channel()
        return self._channel
    
    def _get_storage(self):
        """Get or create the event storage (if persist_events=True)."""
        if self._storage is None and self.persist_events:
            try:
                from .storage import get_event_storage
                self._storage = get_event_storage()
            except Exception:
                # Storage not configured, disable persistence
                self.persist_events = False
        return self._storage
    
    # =========================================================================
    # Event Emission
    # =========================================================================
    
    def emit(self, event_type: str, **data) -> None:
        """
        Emit a raw event.
        
        Args:
            event_type: Event type string
            **data: Event payload
        """
        if self._closed and event_type != EventType.DONE.value:
            return  # Ignore events after close (except done)
        
        event = StreamEvent(
            type=event_type,
            channel_id=self.channel_id,
            data=data,
            context=self.debug_context,
        )
        
        # Publish to Redis Pub/Sub
        channel = self._get_channel()
        channel.publish(event)
        
        # Optionally persist to storage
        if self.persist_events:
            storage = self._get_storage()
            if storage:
                try:
                    storage.store(event)
                except Exception:
                    pass  # Don't fail on storage errors
    
    def log(self, message: str, level: str = "info") -> None:
        """
        Emit a log event.
        
        Shows in SSE stream AND optionally persisted to OpenSearch.
        
        Args:
            message: Log message
            level: Log level (debug, info, warning, error)
        """
        self.emit(EventType.LOG.value, message=message, level=level)
    
    def progress(
        self,
        percent: int,
        step: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        """
        Emit a progress event.
        
        Args:
            percent: Progress percentage (0-100)
            step: Current step name
            message: Optional progress message
        """
        data = {"progress": percent}
        if step:
            data["step"] = step
        if message:
            data["message"] = message
        self.emit(EventType.PROGRESS.value, **data)
    
    def error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Emit an error event (doesn't close stream).
        
        Use this for recoverable errors. Use complete(success=False) for fatal errors.
        
        Args:
            message: Error message
            details: Optional error details
        """
        data = {"message": message}
        if details:
            data["details"] = details
        self.emit(EventType.ERROR.value, **data)
    
    def data(self, payload: Dict[str, Any]) -> None:
        """
        Emit a generic data event.
        
        Args:
            payload: Data payload
        """
        self.emit(EventType.DATA.value, **payload)
    
    def complete(
        self,
        success: bool,
        error: Optional[str] = None,
        **result,
    ) -> None:
        """
        Emit completion event and close the stream.
        
        This MUST be called at the end of every stream to signal completion.
        After this, no more events can be emitted.
        
        Args:
            success: Whether the operation succeeded
            error: Error message (if not success)
            **result: Result data to include
        """
        data = {"success": success}
        if error:
            data["error"] = error
        data.update(result)
        
        self._closed = True
        self.emit(EventType.DONE.value, **data)
    
    # =========================================================================
    # Deployment-specific helpers
    # =========================================================================
    
    def deploy_start(self, target: str, server_count: int) -> None:
        """Emit deployment start event."""
        self.emit("deploy_start", target=target, servers=server_count)
        self.log(f"🚀 Starting deployment: {target} to {server_count} server(s)")
    
    def server_ready(self, ip: str, name: Optional[str] = None) -> None:
        """Emit server ready event."""
        data = {"ip": ip}
        if name:
            data["name"] = name
        self.emit("server_ready", **data)
    
    def deploy_success(
        self,
        ip: str,
        container_name: str,
        url: Optional[str] = None,
    ) -> None:
        """Emit successful deployment to a server."""
        data = {"ip": ip, "container": container_name}
        if url:
            data["url"] = url
        self.emit("deploy_success", **data)
        self.log(f"✅ [{ip}] Deployed {container_name}")
    
    def deploy_failure(self, ip: str, error: str) -> None:
        """Emit failed deployment to a server."""
        self.emit("deploy_failure", ip=ip, error=error)
        self.log(f"❌ [{ip}] Failed: {error}")
    
    # =========================================================================
    # Representation
    # =========================================================================
    
    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return f"StreamContext({self.namespace}, {self.channel_id[:8]}..., {status})"


# Convenience alias
Context = StreamContext
