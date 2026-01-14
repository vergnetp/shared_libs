"""
Audit logging - Structured audit trail for security events.

Provides an interface for recording audit events like:
- Authentication events (login, logout, failed attempts)
- Authorization events (permission checks, access denials)
- Data mutations (create, update, delete)
- Admin actions

Usage:
    from app_kernel.observability import audit
    
    # Record an audit event
    await audit.log(
        action="user.login",
        actor_id=user.id,
        resource_type="session",
        resource_id=session.id,
        metadata={"ip": request.client.host}
    )
    
    # Query audit logs
    events = await audit.query(
        actor_id=user.id,
        action="user.login",
        after=datetime.now() - timedelta(days=7)
    )
"""
from typing import Optional, Dict, Any, List, Protocol, runtime_checkable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import uuid
import json

UTC = timezone.utc


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AuditEvent:
    """A single audit event."""
    id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_utcnow)
    
    # What happened
    action: str = ""  # e.g., "user.login", "document.update"
    status: str = "success"  # "success", "failure", "denied"
    
    # Who did it
    actor_id: Optional[str] = None
    actor_type: str = "user"  # "user", "system", "api_key"
    
    # What was affected
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # Context
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    # Additional data
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


@runtime_checkable
class AuditStore(Protocol):
    """
    Protocol for audit log storage.
    
    Apps implement this to store audit logs in their preferred backend
    (database, OpenSearch, etc.).
    """
    
    async def store(self, event: AuditEvent) -> None:
        """Store an audit event."""
        ...
    
    async def query(
        self,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Query audit events."""
        ...


class InMemoryAuditStore:
    """
    In-memory audit store for development/testing.
    
    NOT for production use.
    """
    
    def __init__(self, max_events: int = 10000):
        self._events: List[AuditEvent] = []
        self._max_events = max_events
    
    async def store(self, event: AuditEvent) -> None:
        self._events.append(event)
        
        # Trim if too many events
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
    
    async def query(
        self,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        results = []
        
        for event in reversed(self._events):
            # Apply filters
            if actor_id and event.actor_id != actor_id:
                continue
            if action and event.action != action:
                continue
            if resource_type and event.resource_type != resource_type:
                continue
            if resource_id and event.resource_id != resource_id:
                continue
            if after and event.timestamp < after:
                continue
            if before and event.timestamp > before:
                continue
            
            results.append(event)
            
            if len(results) >= limit:
                break
        
        return results


class AuditLogger:
    """
    Audit logger for recording security events.
    
    Uses a pluggable store for persistence.
    """
    
    def __init__(self, store: Optional[AuditStore] = None):
        self._store = store or InMemoryAuditStore()
    
    def set_store(self, store: AuditStore):
        """Set the audit store."""
        self._store = store
    
    async def log(
        self,
        action: str,
        *,
        status: str = "success",
        actor_id: Optional[str] = None,
        actor_type: str = "user",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        request_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AuditEvent:
        """
        Log an audit event.
        
        Args:
            action: Action identifier (e.g., "user.login")
            status: "success", "failure", or "denied"
            actor_id: ID of the actor (user, system)
            actor_type: Type of actor
            resource_type: Type of affected resource
            resource_id: ID of affected resource
            request_id: Request ID for correlation
            ip_address: Client IP
            user_agent: Client user agent
            metadata: Additional data
        
        Returns:
            The created AuditEvent
        """
        event = AuditEvent(
            action=action,
            status=status,
            actor_id=actor_id,
            actor_type=actor_type,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata or {}
        )
        
        await self._store.store(event)
        return event
    
    async def query(
        self,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Query audit events."""
        return await self._store.query(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            after=after,
            before=before,
            limit=limit
        )


# Module-level audit logger
_audit: Optional[AuditLogger] = None


def init_audit(store: Optional[AuditStore] = None) -> AuditLogger:
    """Initialize the audit logger."""
    global _audit
    _audit = AuditLogger(store)
    return _audit


def get_audit() -> AuditLogger:
    """Get the audit logger."""
    global _audit
    if _audit is None:
        _audit = AuditLogger()
    return _audit


# Convenience access
audit = property(lambda self: get_audit())
