"""
Multi-user participation pattern for shared resources.

Provides a generic way to manage participants (users) on any resource
(threads, documents, projects, workspaces, etc.) with role-based
permissions and visibility controls.

Usage:
    from shared_lib.multiuser import (
        Participant,
        ParticipantRole,
        ParticipantStore,
        EntityParticipantStore,
        ParticipantsMixin,
    )
    
    # Create store with connection getter
    store = EntityParticipantStore(
        get_connection=lambda: pool.get_connection(),
        resource_name="thread_participants"
    )
    
    # Add participant
    await store.add(Participant(
        resource_id=thread_id,
        user_id=user_id,
        role=ParticipantRole.OWNER
    ))
    
    # Or use mixin
    class ThreadService(ParticipantsMixin):
        def __init__(self, get_connection):
            self.init_participant_store(
                get_connection=get_connection,
                resource_name="thread_participants"
            )
"""

from .types import (
    ParticipantRole,
    Participant,
    Permission,
    VisibilityLevel,
)

from .base import (
    ParticipantStore,
    ParticipantExistsError,
    ParticipantNotFoundError,
    PermissionDeniedError,
)

from .postgres import EntityParticipantStore

from .mixin import ParticipantsMixin, require_permission

# Backwards compatibility
PostgresParticipantStore = EntityParticipantStore

__all__ = [
    # Types
    "ParticipantRole",
    "Participant",
    "Permission",
    "VisibilityLevel",
    # Store
    "ParticipantStore",
    "EntityParticipantStore",
    "PostgresParticipantStore",  # Alias
    # Exceptions
    "ParticipantExistsError",
    "ParticipantNotFoundError",
    "PermissionDeniedError",
    # Mixin
    "ParticipantsMixin",
    "require_permission",
]
