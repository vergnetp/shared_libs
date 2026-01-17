"""
Stream Events - Typed event structures for streaming.

Events carry both the payload and context metadata for debugging/logging.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum


class EventType(str, Enum):
    """Standard event types."""
    LOG = "log"
    PROGRESS = "progress"
    DATA = "data"
    ERROR = "error"
    DONE = "done"
    PING = "ping"
    
    # Domain-specific (deployments)
    SERVER_READY = "server_ready"
    DEPLOY_START = "deploy_start"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILURE = "deploy_failure"


@dataclass
class StreamEvent:
    """
    A single streaming event.
    
    Carries both the payload and rich context for debugging/logging.
    Serializable to JSON for Redis Pub/Sub and SSE.
    
    Attributes:
        type: Event type (log, progress, data, error, done, etc.)
        channel_id: Channel this event belongs to
        data: Event payload (arbitrary dict)
        timestamp: When the event was created
        context: Debugging context (workspace/project/env/service)
        event_id: Unique event ID (auto-generated)
    """
    type: str
    channel_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    event_id: Optional[str] = None
    
    def __post_init__(self):
        """Set defaults for timestamp and event_id."""
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"
        if self.event_id is None:
            import uuid
            self.event_id = str(uuid.uuid4())[:8]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "type": self.type,
            "channel_id": self.channel_id,
            "data": self.data,
            "timestamp": self.timestamp,
            "context": self.context,
            "event_id": self.event_id,
        }
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), default=str)
    
    def to_sse(self) -> str:
        """
        Format as Server-Sent Event string.
        
        Returns:
            SSE-formatted string: "data: {...}\n\n"
        """
        # Flatten for SSE (include type in payload)
        payload = {"type": self.type, **self.data}
        if self.context:
            payload["_context"] = self.context
        return f"data: {json.dumps(payload, default=str)}\n\n"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StreamEvent':
        """Create from dictionary."""
        return cls(
            type=data.get("type", "data"),
            channel_id=data.get("channel_id", ""),
            data=data.get("data", {}),
            timestamp=data.get("timestamp"),
            context=data.get("context", {}),
            event_id=data.get("event_id"),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> 'StreamEvent':
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    # =========================================================================
    # Factory methods for common events
    # =========================================================================
    
    @classmethod
    def log(
        cls,
        channel_id: str,
        message: str,
        level: str = "info",
        context: Optional[Dict[str, Any]] = None,
    ) -> 'StreamEvent':
        """Create a log event."""
        return cls(
            type=EventType.LOG.value,
            channel_id=channel_id,
            data={"message": message, "level": level},
            context=context or {},
        )
    
    @classmethod
    def progress(
        cls,
        channel_id: str,
        percent: int,
        step: Optional[str] = None,
        message: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> 'StreamEvent':
        """Create a progress event."""
        data = {"progress": percent}
        if step:
            data["step"] = step
        if message:
            data["message"] = message
        return cls(
            type=EventType.PROGRESS.value,
            channel_id=channel_id,
            data=data,
            context=context or {},
        )
    
    @classmethod
    def error(
        cls,
        channel_id: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> 'StreamEvent':
        """Create an error event (doesn't close stream)."""
        data = {"message": message}
        if details:
            data["details"] = details
        return cls(
            type=EventType.ERROR.value,
            channel_id=channel_id,
            data=data,
            context=context or {},
        )
    
    @classmethod
    def done(
        cls,
        channel_id: str,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> 'StreamEvent':
        """Create a completion event (closes stream)."""
        data = {"success": success}
        if result:
            data.update(result)
        if error:
            data["error"] = error
        return cls(
            type=EventType.DONE.value,
            channel_id=channel_id,
            data=data,
            context=context or {},
        )
    
    @classmethod
    def ping(cls, channel_id: str) -> 'StreamEvent':
        """Create a keepalive ping event."""
        return cls(
            type=EventType.PING.value,
            channel_id=channel_id,
            data={},
        )
    
    @classmethod
    def data(
        cls,
        channel_id: str,
        payload: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> 'StreamEvent':
        """Create a generic data event."""
        return cls(
            type=EventType.DATA.value,
            channel_id=channel_id,
            data=payload,
            context=context or {},
        )
