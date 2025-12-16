"""
Abstract base class for participant storage.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .types import Participant, Permission


class ParticipantStore(ABC):
    """
    Abstract interface for participant storage.
    
    Implement this for your storage backend (PostgreSQL, MongoDB, etc.)
    """
    
    @abstractmethod
    async def add(self, participant: Participant) -> Participant:
        """
        Add a participant to a resource.
        
        Args:
            participant: Participant to add
            
        Returns:
            Created participant with ID populated
            
        Raises:
            ParticipantExistsError: If user is already a participant
        """
        ...
    
    @abstractmethod
    async def remove(self, resource_id: str, user_id: str) -> bool:
        """
        Remove a participant from a resource.
        
        Args:
            resource_id: Entity ID
            user_id: User ID to remove
            
        Returns:
            True if removed, False if not found
        """
        ...
    
    @abstractmethod
    async def get(self, resource_id: str, user_id: str) -> Optional[Participant]:
        """
        Get a specific participant.
        
        Args:
            resource_id: Entity ID
            user_id: User ID
            
        Returns:
            Participant if found, None otherwise
        """
        ...
    
    @abstractmethod
    async def get_for_resource(self, resource_id: str) -> list[Participant]:
        """
        Get all participants for a resource.
        
        Args:
            resource_id: Entity ID
            
        Returns:
            List of participants
        """
        ...
    
    @abstractmethod
    async def get_for_user(self, user_id: str) -> list[Participant]:
        """
        Get all entities a user participates in.
        
        Args:
            user_id: User ID
            
        Returns:
            List of participant records
        """
        ...
    
    @abstractmethod
    async def update(self, participant: Participant) -> Participant:
        """
        Update a participant's role, permissions, or metadata.
        
        Args:
            participant: Participant with updated fields
            
        Returns:
            Updated participant
            
        Raises:
            ParticipantNotFoundError: If participant doesn't exist
        """
        ...
    
    @abstractmethod
    async def check_permission(
        self,
        resource_id: str,
        user_id: str,
        permission: Permission,
    ) -> bool:
        """
        Check if a user has a specific permission on a resource.
        
        Args:
            resource_id: Entity ID
            user_id: User ID
            permission: Permission to check
            
        Returns:
            True if user has permission, False otherwise
        """
        ...
    
    async def is_participant(self, resource_id: str, user_id: str) -> bool:
        """
        Check if a user is a participant of an entity.
        
        Args:
            resource_id: Entity ID
            user_id: User ID
            
        Returns:
            True if user is a participant
        """
        participant = await self.get(resource_id, user_id)
        return participant is not None
    
    async def get_owners(self, resource_id: str) -> list[Participant]:
        """
        Get all owners of an entity.
        
        Args:
            resource_id: Entity ID
            
        Returns:
            List of owner participants
        """
        from .types import ParticipantRole
        
        participants = await self.get_for_resource(resource_id)
        return [p for p in participants if p.role == ParticipantRole.OWNER]
    
    async def transfer_ownership(
        self,
        resource_id: str,
        from_user_id: str,
        to_user_id: str,
    ) -> bool:
        """
        Transfer ownership from one user to another.
        
        Args:
            resource_id: Entity ID
            from_user_id: Current owner
            to_user_id: New owner
            
        Returns:
            True if transferred successfully
        """
        from .types import ParticipantRole
        
        # Get both participants
        from_participant = await self.get(resource_id, from_user_id)
        to_participant = await self.get(resource_id, to_user_id)
        
        if not from_participant or from_participant.role != ParticipantRole.OWNER:
            return False
        
        if not to_participant:
            return False
        
        # Update roles
        from_participant.role = ParticipantRole.ADMIN
        to_participant.role = ParticipantRole.OWNER
        
        await self.update(from_participant)
        await self.update(to_participant)
        
        return True


class ParticipantExistsError(Exception):
    """Raised when trying to add a participant that already exists."""
    
    def __init__(self, resource_id: str, user_id: str):
        self.resource_id = resource_id
        self.user_id = user_id
        super().__init__(f"User {user_id} is already a participant of {resource_id}")


class ParticipantNotFoundError(Exception):
    """Raised when a participant is not found."""
    
    def __init__(self, resource_id: str, user_id: str):
        self.resource_id = resource_id
        self.user_id = user_id
        super().__init__(f"User {user_id} is not a participant of {resource_id}")


class PermissionDeniedError(Exception):
    """Raised when a user doesn't have required permission."""
    
    def __init__(self, user_id: str, permission: Permission, resource_id: str = None):
        self.user_id = user_id
        self.permission = permission
        self.resource_id = resource_id
        msg = f"User {user_id} does not have {permission.value} permission"
        if resource_id:
            msg += f" on {resource_id}"
        super().__init__(msg)
