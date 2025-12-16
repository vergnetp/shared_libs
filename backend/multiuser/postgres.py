"""
Database implementation of ParticipantStore using the Entity framework.

Works with any connection that implements the Entity mixin (PostgreSQL, MySQL, SQLite).
"""

from datetime import datetime
from typing import Optional, Any, Callable, Awaitable

from .base import (
    ParticipantStore,
    ParticipantExistsError,
    ParticipantNotFoundError,
)
from .types import Participant, Permission, ParticipantRole, VisibilityLevel


class EntityParticipantStore(ParticipantStore):
    """
    Entity framework implementation of ParticipantStore.
    
    Uses your existing database abstraction layer with automatic schema management.
    
    Usage:
        # With a connection getter (recommended for proper connection management)
        store = EntityParticipantStore(
            get_connection=lambda: pool.get_connection(),
            resource_name="thread_participants"
        )
        
        # Or with a direct connection (for simple cases)
        store = EntityParticipantStore(
            connection=conn,
            resource_name="thread_participants"
        )
    
    Entity schema (auto-created):
        - id: str (UUID, auto-generated)
        - resource_id: str (the thread/document/project ID)
        - user_id: str
        - role: str
        - display_name: str (optional)
        - visibility: str
        - can_read: bool (optional override)
        - can_write: bool (optional override)
        - can_delete: bool (optional override)
        - can_manage_participants: bool (optional override)
        - can_view_others_data: bool (optional override)
        - metadata: dict
        - invited_by: str (optional)
        - created_at, updated_at: auto-managed
    """
    
    def __init__(
        self,
        connection: Any = None,
        get_connection: Callable[[], Awaitable[Any]] = None,
        resource_name: str = "participants",
    ):
        """
        Initialize the store.
        
        Args:
            connection: Direct database connection (simple usage)
            get_connection: Async callable that returns a connection (recommended)
            resource_name: Name of the entity table (e.g., "thread_participants")
        """
        if connection is None and get_connection is None:
            raise ValueError("Either connection or get_connection must be provided")
        
        self._connection = connection
        self._get_connection = get_connection
        self.resource_name = resource_name
    
    async def _conn(self):
        """Get a database connection."""
        if self._get_connection:
            return await self._get_connection()
        return self._connection
    
    def _to_entity(self, participant: Participant) -> dict:
        """Convert Participant to entity dict."""
        return {
            "id": participant.id,
            "resource_id": participant.resource_id,
            "user_id": participant.user_id,
            "role": participant.role.value if isinstance(participant.role, ParticipantRole) else participant.role,
            "display_name": participant.display_name,
            "visibility": participant.visibility.value if isinstance(participant.visibility, VisibilityLevel) else participant.visibility,
            "can_read": participant.can_read,
            "can_write": participant.can_write,
            "can_delete": participant.can_delete,
            "can_manage_participants": participant.can_manage_participants,
            "can_view_others_data": participant.can_view_others_data,
            "metadata": participant.metadata or {},
            "invited_by": participant.invited_by,
        }
    
    def _from_entity(self, entity: dict) -> Participant:
        """Convert entity dict to Participant."""
        role = entity.get("role")
        if isinstance(role, str):
            try:
                role = ParticipantRole(role)
            except ValueError:
                role = ParticipantRole.MEMBER
        
        visibility = entity.get("visibility")
        if isinstance(visibility, str):
            try:
                visibility = VisibilityLevel(visibility)
            except ValueError:
                visibility = VisibilityLevel.PRIVATE
        
        # Handle metadata - might be string or dict depending on deserialize flag
        metadata = entity.get("metadata")
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        
        # Handle joined_at from created_at
        joined_at = entity.get("created_at")
        if isinstance(joined_at, str):
            try:
                joined_at = datetime.fromisoformat(joined_at)
            except:
                joined_at = None
        
        return Participant(
            id=entity.get("id"),
            resource_id=entity["resource_id"],
            user_id=entity["user_id"],
            role=role or ParticipantRole.MEMBER,
            display_name=entity.get("display_name"),
            visibility=visibility or VisibilityLevel.PRIVATE,
            can_read=entity.get("can_read"),
            can_write=entity.get("can_write"),
            can_delete=entity.get("can_delete"),
            can_manage_participants=entity.get("can_manage_participants"),
            can_view_others_data=entity.get("can_view_others_data"),
            metadata=metadata or {},
            joined_at=joined_at,
            invited_by=entity.get("invited_by"),
        )
    
    async def add(self, participant: Participant) -> Participant:
        """Add a participant to a resource."""
        conn = await self._conn()
        
        # Check if already exists
        existing = await self.get(participant.resource_id, participant.user_id)
        if existing:
            raise ParticipantExistsError(participant.resource_id, participant.user_id)
        
        # Convert to entity dict
        entity = self._to_entity(participant)
        
        # Remove None id so it gets auto-generated
        if entity.get("id") is None:
            del entity["id"]
        
        # Save entity
        saved = await conn.save_entity(self.resource_name, entity)
        
        return self._from_entity(saved)
    
    async def remove(self, resource_id: str, user_id: str) -> bool:
        """Remove a participant from a resource."""
        conn = await self._conn()
        
        # Find the participant
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[resource_id] = ? AND [user_id] = ?",
            params=(resource_id, user_id),
            include_deleted=False,
        )
        
        if not results:
            return False
        
        # Delete (soft delete via Entity framework)
        await conn.delete_entity(self.resource_name, results[0]["id"])
        return True
    
    async def get(self, resource_id: str, user_id: str) -> Optional[Participant]:
        """Get a specific participant."""
        conn = await self._conn()
        
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[resource_id] = ? AND [user_id] = ?",
            params=(resource_id, user_id),
            include_deleted=False,
            deserialize=True,
        )
        
        if not results:
            return None
        
        return self._from_entity(results[0])
    
    async def get_for_resource(self, resource_id: str) -> list[Participant]:
        """Get all participants for a resource."""
        conn = await self._conn()
        
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[resource_id] = ?",
            params=(resource_id,),
            order_by="created_at ASC",
            include_deleted=False,
            deserialize=True,
        )
        
        return [self._from_entity(r) for r in results]
    
    async def get_for_user(self, user_id: str) -> list[Participant]:
        """Get all entities a user participates in."""
        conn = await self._conn()
        
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[user_id] = ?",
            params=(user_id,),
            order_by="created_at DESC",
            include_deleted=False,
            deserialize=True,
        )
        
        return [self._from_entity(r) for r in results]
    
    async def update(self, participant: Participant) -> Participant:
        """Update a participant's role, permissions, or metadata."""
        conn = await self._conn()
        
        # Find existing by resource_id + user_id
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[resource_id] = ? AND [user_id] = ?",
            params=(participant.resource_id, participant.user_id),
            include_deleted=False,
        )
        
        if not results:
            raise ParticipantNotFoundError(participant.resource_id, participant.user_id)
        
        # Get existing ID
        existing_id = results[0]["id"]
        
        # Convert to entity and set ID
        entity = self._to_entity(participant)
        entity["id"] = existing_id
        
        # Save (upsert)
        saved = await conn.save_entity(self.resource_name, entity)
        
        return self._from_entity(saved)
    
    async def check_permission(
        self,
        resource_id: str,
        user_id: str,
        permission: Permission,
    ) -> bool:
        """Check if a user has a specific permission on a resource."""
        participant = await self.get(resource_id, user_id)
        
        if not participant:
            return False
        
        return participant.has_permission(permission)
    
    async def count_for_entity(self, resource_id: str) -> int:
        """Count participants for a resource."""
        conn = await self._conn()
        
        return await conn.count_entities(
            self.resource_name,
            where_clause="[resource_id] = ?",
            params=(resource_id,),
            include_deleted=False,
        )
    
    async def get_entities_for_user_with_role(
        self,
        user_id: str,
        role: ParticipantRole,
    ) -> list[str]:
        """Get all entity IDs where user has specific role."""
        conn = await self._conn()
        
        role_value = role.value if isinstance(role, ParticipantRole) else role
        
        results = await conn.find_entities(
            self.resource_name,
            where_clause="[user_id] = ? AND [role] = ?",
            params=(user_id, role_value),
            include_deleted=False,
        )
        
        return [r["resource_id"] for r in results]


# Backwards compatibility alias
PostgresParticipantStore = EntityParticipantStore

