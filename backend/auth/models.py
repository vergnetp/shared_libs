"""
Auth models - User, Role, RoleAssignment, Session
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
UTC = timezone.utc
from typing import Optional
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class User:
    id: str = field(default_factory=_uuid)
    email: str = ""
    password_hash: Optional[str] = None
    name: Optional[str] = None
    role: str = "user"  # 'admin' | 'user' - global role
    metadata: dict = field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    deleted_at: Optional[datetime] = None
    
    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == "admin"
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "User":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Role:
    """A role with permissions."""
    id: str = field(default_factory=_uuid)
    name: str = ""
    permissions: list[str] = field(default_factory=list)
    description: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Role":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RoleAssignment:
    """Assigns a role to a user, optionally scoped to a resource."""
    id: str = field(default_factory=_uuid)
    user_id: str = ""
    role_id: str = ""
    resource_type: str = ""
    resource_id: Optional[str] = None
    granted_by: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "RoleAssignment":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Session:
    """User session for token-based auth."""
    id: str = field(default_factory=_uuid)
    user_id: str = ""
    token_hash: str = ""
    expires_at: datetime = field(default_factory=_utcnow)
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
