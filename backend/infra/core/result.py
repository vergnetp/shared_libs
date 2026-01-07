"""
Result types for deployment operations.

Simple dataclasses for operation results - no complex logic.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime


class Status(Enum):
    """Operation status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Result:
    """Base result for any operation."""
    success: bool
    message: str = ""
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, message: str = "OK", **data) -> 'Result':
        return cls(success=True, message=message, data=data)
    
    @classmethod
    def fail(cls, error: str, **data) -> 'Result':
        return cls(success=False, error=error, data=data)


@dataclass
class ContainerResult(Result):
    """Result of container operation."""
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    server_ip: Optional[str] = None
    port: Optional[int] = None


@dataclass 
class BuildResult(Result):
    """Result of image build."""
    image_name: Optional[str] = None
    image_tag: Optional[str] = None
    pushed: bool = False
    build_time_seconds: Optional[float] = None


@dataclass
class DeployResult(Result):
    """Result of deployment operation."""
    services: Dict[str, ContainerResult] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    def add_service(self, name: str, result: ContainerResult):
        self.services[name] = result
        if not result.success:
            self.success = False
            self.error = self.error or result.error


@dataclass
class ServerResult(Result):
    """Result of server operation."""
    server_id: Optional[str] = None
    ip: Optional[str] = None
    zone: Optional[str] = None
    status: Optional[str] = None
