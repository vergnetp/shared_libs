"""
Core types for multi-user participation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ParticipantRole(str, Enum):
    """
    Standard roles for participants.
    Extend with custom roles as needed.
    """
    OWNER = "owner"          # Full control, can delete entity
    ADMIN = "admin"          # Can manage participants
    MEMBER = "member"        # Can read and write
    VIEWER = "viewer"        # Read-only access
    
    # For mediation/multi-party scenarios
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    MEDIATOR = "mediator"
    OBSERVER = "observer"


class Permission(str, Enum):
    """Granular permissions that can be checked."""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    MANAGE_PARTICIPANTS = "manage_participants"
    VIEW_OTHERS_DATA = "view_others_data"


class VisibilityLevel(str, Enum):
    """How visible a participant's data is to others."""
    PRIVATE = "private"      # Only visible to self and admins
    PARTICIPANTS = "participants"  # Visible to all participants
    PUBLIC = "public"        # Visible to anyone


# Default role permissions
ROLE_PERMISSIONS: dict[ParticipantRole, set[Permission]] = {
    ParticipantRole.OWNER: {
        Permission.READ,
        Permission.WRITE,
        Permission.DELETE,
        Permission.MANAGE_PARTICIPANTS,
        Permission.VIEW_OTHERS_DATA,
    },
    ParticipantRole.ADMIN: {
        Permission.READ,
        Permission.WRITE,
        Permission.MANAGE_PARTICIPANTS,
        Permission.VIEW_OTHERS_DATA,
    },
    ParticipantRole.MEMBER: {
        Permission.READ,
        Permission.WRITE,
    },
    ParticipantRole.VIEWER: {
        Permission.READ,
    },
    ParticipantRole.MEDIATOR: {
        Permission.READ,
        Permission.WRITE,
        Permission.VIEW_OTHERS_DATA,
    },
    ParticipantRole.PARTY_A: {
        Permission.READ,
        Permission.WRITE,
    },
    ParticipantRole.PARTY_B: {
        Permission.READ,
        Permission.WRITE,
    },
    ParticipantRole.OBSERVER: {
        Permission.READ,
        Permission.VIEW_OTHERS_DATA,
    },
}


@dataclass
class Participant:
    """
    Represents a user's participation in a resource.
    
    Attributes:
        resource_id: ID of the resource (thread, document, project, etc.)
        user_id: ID of the participating user
        role: Role determining base permissions
        display_name: Optional display name (for anonymization)
        visibility: How visible this participant's data is to others
        custom_permissions: Override default role permissions
        metadata: Arbitrary additional data
        joined_at: When the participant joined
        invited_by: User ID of who invited this participant
    """
    resource_id: str
    user_id: str
    role: ParticipantRole = ParticipantRole.MEMBER
    
    # Display
    display_name: Optional[str] = None
    
    # Visibility
    visibility: VisibilityLevel = VisibilityLevel.PRIVATE
    
    # Permission overrides (None = use role defaults)
    can_read: Optional[bool] = None
    can_write: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_manage_participants: Optional[bool] = None
    can_view_others_data: Optional[bool] = None
    
    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    joined_at: Optional[datetime] = None
    invited_by: Optional[str] = None
    
    # Internal
    id: Optional[str] = None
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if participant has a specific permission."""
        # Check explicit overrides first
        override_map = {
            Permission.READ: self.can_read,
            Permission.WRITE: self.can_write,
            Permission.DELETE: self.can_delete,
            Permission.MANAGE_PARTICIPANTS: self.can_manage_participants,
            Permission.VIEW_OTHERS_DATA: self.can_view_others_data,
        }
        
        override = override_map.get(permission)
        if override is not None:
            return override
        
        # Fall back to role defaults
        role_perms = ROLE_PERMISSIONS.get(self.role, set())
        return permission in role_perms
    
    def can_see_participant(self, other: "Participant") -> bool:
        """Check if this participant can see another participant's data."""
        # Can always see own data
        if self.user_id == other.user_id:
            return True
        
        # Check if we have permission to view others' data
        if not self.has_permission(Permission.VIEW_OTHERS_DATA):
            return False
        
        # Check other's visibility setting
        if other.visibility == VisibilityLevel.PRIVATE:
            # Only admins/owners can see private
            return self.role in (ParticipantRole.OWNER, ParticipantRole.ADMIN)
        elif other.visibility == VisibilityLevel.PARTICIPANTS:
            return True
        elif other.visibility == VisibilityLevel.PUBLIC:
            return True
        
        return False
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "user_id": self.user_id,
            "role": self.role.value if isinstance(self.role, ParticipantRole) else self.role,
            "display_name": self.display_name,
            "visibility": self.visibility.value if isinstance(self.visibility, VisibilityLevel) else self.visibility,
            "can_read": self.can_read,
            "can_write": self.can_write,
            "can_delete": self.can_delete,
            "can_manage_participants": self.can_manage_participants,
            "can_view_others_data": self.can_view_others_data,
            "metadata": self.metadata,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
            "invited_by": self.invited_by,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Participant":
        """Create from dictionary."""
        role = data.get("role")
        if isinstance(role, str):
            role = ParticipantRole(role)
        
        visibility = data.get("visibility")
        if isinstance(visibility, str):
            visibility = VisibilityLevel(visibility)
        
        joined_at = data.get("joined_at")
        if isinstance(joined_at, str):
            joined_at = datetime.fromisoformat(joined_at)
        
        return cls(
            id=data.get("id"),
            resource_id=data["resource_id"],
            user_id=data["user_id"],
            role=role or ParticipantRole.MEMBER,
            display_name=data.get("display_name"),
            visibility=visibility or VisibilityLevel.PRIVATE,
            can_read=data.get("can_read"),
            can_write=data.get("can_write"),
            can_delete=data.get("can_delete"),
            can_manage_participants=data.get("can_manage_participants"),
            can_view_others_data=data.get("can_view_others_data"),
            metadata=data.get("metadata") or {},
            joined_at=joined_at,
            invited_by=data.get("invited_by"),
        )
