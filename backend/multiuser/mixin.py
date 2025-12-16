"""
Mixin for adding participant support to entity-based models.

Usage:
    from shared_lib.multiuser import ParticipantsMixin, EntityParticipantStore
    
    # Option 1: Use mixin with any entity service
    class ThreadService(ParticipantsMixin):
        def __init__(self, get_connection):
            self.init_participant_store(
                get_connection=get_connection,
                resource_name="thread_participants"
            )
    
    # Option 2: Use as standalone helper
    mixin = ParticipantsMixin()
    mixin.init_participant_store(get_connection=get_conn, resource_name="doc_participants")
    await mixin.add_participant(resource_id, user_id, ParticipantRole.MEMBER)
"""

from typing import Optional, TYPE_CHECKING, Callable, Awaitable, Any

from .types import Participant, ParticipantRole, Permission, VisibilityLevel

if TYPE_CHECKING:
    from .base import ParticipantStore


class ParticipantsMixin:
    """
    Mixin that adds participant management methods.
    
    Can be used with:
    1. Any class that has an 'id' attribute (entity ID)
    2. As a standalone helper by passing resource_id to methods
    
    Call init_participant_store() before using participant methods.
    """
    
    _participant_store: Optional["ParticipantStore"] = None
    
    def init_participant_store(
        self,
        connection: Any = None,
        get_connection: Callable[[], Awaitable[Any]] = None,
        resource_name: str = "participants",
    ):
        """
        Initialize the participant store.
        
        Args:
            connection: Direct database connection
            get_connection: Async callable that returns a connection (recommended)
            resource_name: Name of the entity table (e.g., "thread_participants")
        """
        from .postgres import EntityParticipantStore
        
        self._participant_store = EntityParticipantStore(
            connection=connection,
            get_connection=get_connection,
            resource_name=resource_name,
        )
    
    def _get_store(self, store: Optional["ParticipantStore"] = None) -> "ParticipantStore":
        """Get the participant store, using provided or instance-level."""
        s = store or self._participant_store
        if s is None:
            raise RuntimeError(
                "ParticipantStore not initialized. "
                "Call init_participant_store() first or pass store= argument."
            )
        return s
    
    def _get_resource_id(self, resource_id: str = None) -> str:
        """Get the entity ID from argument or self.id."""
        if resource_id is not None:
            return str(resource_id)
        
        # Try to get from self
        eid = getattr(self, "id", None)
        if eid is None:
            raise ValueError("resource_id must be provided or object must have 'id' attribute")
        return str(eid)
    
    async def add_participant(
        self,
        user_id: str,
        role: ParticipantRole = ParticipantRole.MEMBER,
        *,
        resource_id: str = None,
        display_name: str = None,
        visibility: VisibilityLevel = VisibilityLevel.PRIVATE,
        invited_by: str = None,
        metadata: dict = None,
        store: "ParticipantStore" = None,
    ) -> Participant:
        """
        Add a participant to a resource.
        
        Args:
            user_id: User ID to add
            role: Role for the participant
            resource_id: Entity ID (uses self.id if not provided)
            display_name: Optional display name
            visibility: Visibility level for this participant's data
            invited_by: User ID of who invited this participant
            metadata: Additional metadata
            store: Optional store override
            
        Returns:
            Created Participant
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        
        participant = Participant(
            resource_id=eid,
            user_id=user_id,
            role=role,
            display_name=display_name,
            visibility=visibility,
            invited_by=invited_by,
            metadata=metadata or {},
        )
        
        return await s.add(participant)
    
    async def remove_participant(
        self,
        user_id: str,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> bool:
        """
        Remove a participant from a resource.
        
        Args:
            user_id: User ID to remove
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            True if removed, False if not found
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.remove(eid, user_id)
    
    async def get_participant(
        self,
        user_id: str,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> Optional[Participant]:
        """
        Get a specific participant.
        
        Args:
            user_id: User ID to get
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            Participant if found, None otherwise
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.get(eid, user_id)
    
    async def get_participants(
        self,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> list[Participant]:
        """
        Get all participants for a resource.
        
        Args:
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            List of participants
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.get_for_resource(eid)
    
    async def is_participant(
        self,
        user_id: str,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> bool:
        """
        Check if a user is a participant.
        
        Args:
            user_id: User ID to check
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            True if user is a participant
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.is_participant(eid, user_id)
    
    async def can_access(
        self,
        user_id: str,
        permission: Permission = Permission.READ,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> bool:
        """
        Check if a user has permission on a resource.
        
        Args:
            user_id: User ID to check
            permission: Permission to check
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            True if user has permission
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.check_permission(eid, user_id, permission)
    
    async def update_participant(
        self,
        user_id: str,
        *,
        resource_id: str = None,
        role: ParticipantRole = None,
        display_name: str = None,
        visibility: VisibilityLevel = None,
        metadata: dict = None,
        store: "ParticipantStore" = None,
    ) -> Participant:
        """
        Update a participant's attributes.
        
        Args:
            user_id: User ID to update
            resource_id: Entity ID (uses self.id if not provided)
            role: New role (optional)
            display_name: New display name (optional)
            visibility: New visibility (optional)
            metadata: New metadata (optional, replaces existing)
            store: Optional store override
            
        Returns:
            Updated Participant
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        
        # Get existing
        participant = await s.get(eid, user_id)
        if not participant:
            from .base import ParticipantNotFoundError
            raise ParticipantNotFoundError(eid, user_id)
        
        # Update fields if provided
        if role is not None:
            participant.role = role
        if display_name is not None:
            participant.display_name = display_name
        if visibility is not None:
            participant.visibility = visibility
        if metadata is not None:
            participant.metadata = metadata
        
        return await s.update(participant)
    
    async def get_visible_participants(
        self,
        requesting_user_id: str,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> list[Participant]:
        """
        Get participants visible to a specific user.
        
        Args:
            requesting_user_id: User who is requesting
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            List of visible participants
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        
        all_participants = await s.get_for_resource(eid)
        requester = next((p for p in all_participants if p.user_id == requesting_user_id), None)
        
        if not requester:
            return []  # Not a participant, can't see anyone
        
        return [p for p in all_participants if requester.can_see_participant(p)]
    
    async def transfer_ownership(
        self,
        from_user_id: str,
        to_user_id: str,
        resource_id: str = None,
        store: "ParticipantStore" = None,
    ) -> bool:
        """
        Transfer ownership from one user to another.
        
        Args:
            from_user_id: Current owner
            to_user_id: New owner
            resource_id: Entity ID (uses self.id if not provided)
            store: Optional store override
            
        Returns:
            True if transferred successfully
        """
        s = self._get_store(store)
        eid = self._get_resource_id(resource_id)
        return await s.transfer_ownership(eid, from_user_id, to_user_id)


def require_permission(permission: Permission):
    """
    Decorator to check permission before executing a method.
    
    Usage:
        class ThreadService(ParticipantsMixin):
            @require_permission(Permission.WRITE)
            async def send_message(self, resource_id: str, user_id: str, content: str):
                # Only runs if user has WRITE permission
                ...
    
    Note: Method must have resource_id and user_id parameters.
    """
    def decorator(func):
        async def wrapper(self, *args, **kwargs):
            from .base import PermissionDeniedError
            
            # Extract resource_id and user_id from args/kwargs
            resource_id = kwargs.get("resource_id") or (args[0] if args else None)
            user_id = kwargs.get("user_id") or (args[1] if len(args) > 1 else None)
            
            if not resource_id or not user_id:
                raise ValueError("resource_id and user_id required for permission check")
            
            # Get store from self
            store = getattr(self, "_participant_store", None)
            if store is None:
                raise RuntimeError("Service must have _participant_store initialized")
            
            # Check permission
            has_perm = await store.check_permission(resource_id, user_id, permission)
            if not has_perm:
                raise PermissionDeniedError(user_id, permission, resource_id)
            
            return await func(self, *args, **kwargs)
        
        return wrapper
    return decorator
