"""
Auth models - Domain-agnostic user identity primitives.

These models represent the core identity concepts used by the kernel.
Apps may extend these or map to their own domain models.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import uuid

UTC = timezone.utc


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class UserIdentity:
    """
    Core user identity for auth primitives.
    
    This is the minimal identity required by the kernel.
    Apps may extend this with additional domain-specific fields.
    """
    id: str = field(default_factory=_uuid)
    email: str = ""
    role: str = "user"  # 'admin' | 'user' - global role
    is_active: bool = True
    created_at: datetime = field(default_factory=_utcnow)
    
    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == "admin"
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserIdentity":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TokenPayload:
    """
    Decoded JWT token payload.
    
    Contains the essential claims from a verified token.
    """
    sub: str  # User ID
    email: str = ""
    role: str = "user"
    type: str = "access"  # 'access' | 'refresh'
    exp: Optional[datetime] = None
    iat: Optional[datetime] = None
    
    @property
    def user_id(self) -> str:
        return self.sub
    
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
    
    @property
    def is_refresh_token(self) -> bool:
        return self.type == "refresh"


@dataclass
class RequestContext:
    """
    Request-scoped context containing user identity and metadata.
    
    This is attached to each request and provides access to the
    authenticated user and request metadata.
    """
    user: Optional[UserIdentity] = None
    request_id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_utcnow)
    
    # Optional metadata
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    @property
    def is_authenticated(self) -> bool:
        return self.user is not None
    
    @property
    def user_id(self) -> Optional[str]:
        return self.user.id if self.user else None
    
    @property
    def is_admin(self) -> bool:
        return self.user.is_admin if self.user else False
