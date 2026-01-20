from __future__ import annotations
"""Security audit logging for injection attempts."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Callable
from enum import Enum


class ThreatType(str, Enum):
    INJECTION = "injection"
    ROLE_OVERRIDE = "role_override"
    DATA_EXFIL = "data_exfiltration"
    JAILBREAK = "jailbreak"
    UNKNOWN = "unknown"


class DetectionMethod(str, Enum):
    LLM_GUARD = "llm_guard"
    EMBEDDING = "embedding"
    PATTERN = "pattern"
    MANUAL = "manual"


@dataclass
class SecurityEvent:
    """A single security event."""
    timestamp: datetime
    threat_type: ThreatType
    detection_method: DetectionMethod
    content_hash: str  # SHA256 of blocked content (for dedup, not storing raw)
    content_preview: str  # First 100 chars
    user_id: Optional[str]
    blocked: bool
    confidence: float  # 0.0 - 1.0
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "threat_type": self.threat_type.value,
            "detection_method": self.detection_method.value,
            "content_hash": self.content_hash,
            "content_preview": self.content_preview,
            "user_id": self.user_id,
            "blocked": self.blocked,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class SecurityAuditLog:
    """
    Audit log for security events.
    
    Usage:
        log = SecurityAuditLog()
        log.record_blocked("injection", "llm_guard", content, user_id="user123")
        
        # Get report
        report = log.get_report()
        print(f"Blocked {report['total_blocked']} attempts")
        
        # Set callback for real-time alerts
        log.on_event = lambda e: send_slack_alert(e)
    """
    
    def __init__(self, max_events: int = 10000):
        self.events: List[SecurityEvent] = []
        self.max_events = max_events
        self.on_event: Optional[Callable[[SecurityEvent], None]] = None
        
        # Quick stats
        self._blocked_count = 0
        self._by_type: dict[str, int] = {}
        self._by_method: dict[str, int] = {}
        self._unique_hashes: set[str] = set()
    
    def record(
        self,
        threat_type: str,
        detection_method: str,
        content: str,
        blocked: bool = True,
        user_id: str = None,
        confidence: float = 1.0,
        **metadata,
    ) -> SecurityEvent:
        """Record a security event."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        preview = content[:100] + "..." if len(content) > 100 else content
        
        event = SecurityEvent(
            timestamp=datetime.now(),
            threat_type=ThreatType(threat_type) if threat_type in ThreatType.__members__.values() else ThreatType.UNKNOWN,
            detection_method=DetectionMethod(detection_method) if detection_method in DetectionMethod.__members__.values() else DetectionMethod.MANUAL,
            content_hash=content_hash,
            content_preview=preview,
            user_id=user_id,
            blocked=blocked,
            confidence=confidence,
            metadata=metadata,
        )
        
        # Store event
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events.pop(0)  # Remove oldest
        
        # Update stats
        if blocked:
            self._blocked_count += 1
        self._by_type[event.threat_type.value] = self._by_type.get(event.threat_type.value, 0) + 1
        self._by_method[event.detection_method.value] = self._by_method.get(event.detection_method.value, 0) + 1
        self._unique_hashes.add(content_hash)
        
        # Callback
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass  # Don't let callback errors break main flow
        
        return event
    
    def record_blocked(
        self,
        threat_type: str,
        detection_method: str,
        content: str,
        user_id: str = None,
        **metadata,
    ) -> SecurityEvent:
        """Convenience method for blocked attempts."""
        return self.record(
            threat_type=threat_type,
            detection_method=detection_method,
            content=content,
            blocked=True,
            user_id=user_id,
            **metadata,
        )
    
    def get_report(self, since: datetime = None) -> dict:
        """Generate security report."""
        events = self.events
        if since:
            events = [e for e in events if e.timestamp >= since]
        
        return {
            "total_events": len(events),
            "total_blocked": sum(1 for e in events if e.blocked),
            "unique_attacks": len(set(e.content_hash for e in events)),
            "by_threat_type": self._count_by(events, lambda e: e.threat_type.value),
            "by_detection_method": self._count_by(events, lambda e: e.detection_method.value),
            "by_user": self._count_by(events, lambda e: e.user_id or "anonymous"),
            "recent_events": [e.to_dict() for e in events[-10:]],
            "time_range": {
                "start": events[0].timestamp.isoformat() if events else None,
                "end": events[-1].timestamp.isoformat() if events else None,
            },
        }
    
    def _count_by(self, events: List[SecurityEvent], key_fn: Callable) -> dict:
        counts = {}
        for e in events:
            key = key_fn(e)
            counts[key] = counts.get(key, 0) + 1
        return counts
    
    def get_events(
        self,
        threat_type: str = None,
        user_id: str = None,
        since: datetime = None,
        limit: int = 100,
    ) -> List[SecurityEvent]:
        """Query events with filters."""
        events = self.events
        
        if threat_type:
            events = [e for e in events if e.threat_type.value == threat_type]
        if user_id:
            events = [e for e in events if e.user_id == user_id]
        if since:
            events = [e for e in events if e.timestamp >= since]
        
        return events[-limit:]
    
    def clear(self):
        """Clear all events."""
        self.events.clear()
        self._blocked_count = 0
        self._by_type.clear()
        self._by_method.clear()
        self._unique_hashes.clear()
    
    def export_json(self) -> str:
        """Export all events as JSON."""
        return json.dumps([e.to_dict() for e in self.events], indent=2)


# Global default log (can be replaced)
_default_log: Optional[SecurityAuditLog] = None


def get_security_log() -> SecurityAuditLog:
    """Get or create the default security log."""
    global _default_log
    if _default_log is None:
        _default_log = SecurityAuditLog()
    return _default_log


def set_security_log(log: SecurityAuditLog):
    """Set the default security log."""
    global _default_log
    _default_log = log
