# Multi-User Participation Module

A generic pattern for managing participants (users) on shared resources with role-based permissions and visibility controls.

## Features

- **Role-based permissions** with customizable permission sets
- **Visibility controls** for multi-party scenarios (mediation, collaboration)
- **Entity framework integration** - auto-creates tables, no migrations needed
- **Permission checking** with clear exceptions
- **Works with PostgreSQL, MySQL, SQLite** via your database abstraction layer

## Installation

Already part of shared_lib. Uses your existing database module.

```python
from shared_lib.multiuser import (
    Participant,
    ParticipantRole,
    EntityParticipantStore,
    ParticipantsMixin,
    # Exceptions
    ParticipantExistsError,
    ParticipantNotFoundError,
    PermissionDeniedError,
)
```

## Quick Start

### Option 1: Use Store Directly

```python
from shared_lib.multiuser import EntityParticipantStore, Participant, ParticipantRole

# Create store with connection getter (recommended)
store = EntityParticipantStore(
    get_connection=lambda: pool.get_connection(),
    resource_name="thread_participants"
)

# Add participant
await store.add(Participant(
    resource_id=thread_id,
    user_id=user_id,
    role=ParticipantRole.OWNER,
))

# Check permission
can_write = await store.check_permission(thread_id, user_id, Permission.WRITE)

# Get all participants
participants = await store.get_for_resource(thread_id)
```

### Option 2: Use Mixin

```python
from shared_lib.multiuser import ParticipantsMixin, ParticipantRole, Permission

class ThreadService(ParticipantsMixin):
    def __init__(self, get_connection):
        self.init_participant_store(
            get_connection=get_connection,
            resource_name="thread_participants"
        )

# Usage
service = ThreadService(get_connection=lambda: pool.get_connection())

await service.add_participant(user_id, ParticipantRole.OWNER, resource_id=thread_id)
await service.add_participant(other_user_id, ParticipantRole.MEMBER, resource_id=thread_id)

if await service.can_access(user_id, Permission.WRITE, resource_id=thread_id):
    # Allow action
    ...
```

## Error Handling

The module raises specific exceptions - catch them to handle errors appropriately:

```python
from shared_lib.multiuser import (
    ParticipantExistsError,
    ParticipantNotFoundError,
    PermissionDeniedError,
)

# Adding duplicate participant
try:
    await store.add(Participant(resource_id=thread_id, user_id=user_id, role=ParticipantRole.MEMBER))
except ParticipantExistsError:
    # User is already a participant
    pass

# Updating non-existent participant
try:
    await store.update(participant)
except ParticipantNotFoundError:
    # User is not a participant of this resource
    pass

# Permission check with decorator
from shared_lib.multiuser import require_permission

class ThreadService(ParticipantsMixin):
    @require_permission(Permission.WRITE)
    async def send_message(self, resource_id: str, user_id: str, content: str):
        # Only runs if user has WRITE permission
        # Raises PermissionDeniedError if not
        ...

# Manual permission check
if not await store.check_permission(thread_id, user_id, Permission.WRITE):
    raise PermissionDeniedError(user_id, Permission.WRITE, thread_id)
```

### Exception Reference

| Exception | Raised When | Attributes |
|-----------|-------------|------------|
| `ParticipantExistsError` | Adding user who is already a participant | `resource_id`, `user_id` |
| `ParticipantNotFoundError` | Updating/removing non-existent participant | `resource_id`, `user_id` |
| `PermissionDeniedError` | User lacks required permission | `user_id`, `permission`, `resource_id` |

**Note:** `check_permission()` returns `False` (not an exception) when permission is denied. Use `require_permission` decorator or raise `PermissionDeniedError` manually if you want exceptions.

## Roles and Permissions

### Built-in Roles

| Role | READ | WRITE | DELETE | MANAGE_PARTICIPANTS | VIEW_OTHERS_DATA |
|------|------|-------|--------|---------------------|------------------|
| OWNER | ✅ | ✅ | ✅ | ✅ | ✅ |
| ADMIN | ✅ | ✅ | ❌ | ✅ | ✅ |
| MEMBER | ✅ | ✅ | ❌ | ❌ | ❌ |
| VIEWER | ✅ | ❌ | ❌ | ❌ | ❌ |
| MEDIATOR | ✅ | ✅ | ❌ | ❌ | ✅ |
| PARTY_A/B | ✅ | ✅ | ❌ | ❌ | ❌ |
| OBSERVER | ✅ | ❌ | ❌ | ❌ | ✅ |

### Override Permissions

```python
# Give a viewer write access
await store.add(Participant(
    resource_id=thread_id,
    user_id=user_id,
    role=ParticipantRole.VIEWER,
    can_write=True,  # Override
))
```

## Visibility Levels

Control what other participants can see:

```python
from shared_lib.multiuser import VisibilityLevel

# Private - only visible to self and admins
await service.add_participant(user_id, visibility=VisibilityLevel.PRIVATE, resource_id=eid)

# Participants - visible to all participants
await service.add_participant(user_id, visibility=VisibilityLevel.PARTICIPANTS, resource_id=eid)

# Public - visible to anyone
await service.add_participant(user_id, visibility=VisibilityLevel.PUBLIC, resource_id=eid)
```

### Multi-Party Example (Mediation)

```python
# Add parties with anonymized names
await service.add_participant(
    alice_id,
    role=ParticipantRole.PARTY_A,
    display_name="Party A",
    visibility=VisibilityLevel.PARTICIPANTS,
    resource_id=thread_id,
)

await service.add_participant(
    bob_id,
    role=ParticipantRole.PARTY_B,
    display_name="Party B",
    visibility=VisibilityLevel.PARTICIPANTS,
    resource_id=thread_id,
)

# Add mediator who can see both parties' data
await service.add_participant(
    mediator_id,
    role=ParticipantRole.MEDIATOR,
    resource_id=thread_id,
)

# Get what mediator can see
visible = await service.get_visible_participants(mediator_id, resource_id=thread_id)
# Returns: [Party A, Party B, Mediator]
```

## Database Schema

The Entity framework auto-creates tables. The schema will be:

```
{resource_name} (e.g., thread_participants)
├── id (UUID, auto-generated)
├── resource_id (string, indexed)
├── user_id (string, indexed)
├── role (string)
├── display_name (string, nullable)
├── visibility (string)
├── can_read (bool, nullable)
├── can_write (bool, nullable)
├── can_delete (bool, nullable)
├── can_manage_participants (bool, nullable)
├── can_view_others_data (bool, nullable)
├── metadata (dict/JSON)
├── invited_by (string, nullable)
├── created_at (timestamp, auto)
├── updated_at (timestamp, auto)
└── deleted_at (timestamp, for soft delete)
```

Plus automatic history table: `{resource_name}_history`

## Advanced Usage

### Permission Decorator

```python
from shared_lib.multiuser import require_permission, Permission

class ThreadService(ParticipantsMixin):
    def __init__(self, get_connection):
        self.init_participant_store(get_connection=get_connection, resource_name="thread_participants")
    
    @require_permission(Permission.WRITE)
    async def send_message(self, resource_id: str, user_id: str, content: str):
        # Only runs if user has WRITE permission
        ...
```

### Ownership Transfer

```python
await service.transfer_ownership(
    from_user_id=current_owner_id,
    to_user_id=new_owner_id,
    resource_id=thread_id,
)
# Current owner becomes ADMIN, new owner becomes OWNER
```

### Different Entity Types

```python
# Thread participants
thread_store = EntityParticipantStore(
    get_connection=get_conn,
    resource_name="thread_participants"
)

# Document participants  
doc_store = EntityParticipantStore(
    get_connection=get_conn,
    resource_name="document_participants"
)

# Project participants
project_store = EntityParticipantStore(
    get_connection=get_conn,
    resource_name="project_participants"
)
```

## API Reference

### `Participant`

| Field | Type | Description |
|-------|------|-------------|
| resource_id | str | ID of the resource (thread, doc, project, etc.) |
| user_id | str | ID of the user |
| role | ParticipantRole | Role determining permissions |
| display_name | str | Optional display name |
| visibility | VisibilityLevel | How visible to others |
| can_* | bool | Permission overrides |
| metadata | dict | Arbitrary data |
| joined_at | datetime | When joined |
| invited_by | str | Who invited |

### `EntityParticipantStore`

| Method | Description |
|--------|-------------|
| `add(participant)` | Add a participant |
| `remove(resource_id, user_id)` | Remove a participant |
| `get(resource_id, user_id)` | Get specific participant |
| `get_for_resource(resource_id)` | Get all participants |
| `get_for_user(user_id)` | Get user's participations |
| `update(participant)` | Update participant |
| `check_permission(resource_id, user_id, permission)` | Check permission |

### `ParticipantsMixin`

| Method | Description |
|--------|-------------|
| `init_participant_store(...)` | Initialize the store |
| `add_participant(user_id, role, resource_id?, ...)` | Add participant |
| `remove_participant(user_id, resource_id?)` | Remove participant |
| `get_participants(resource_id?)` | Get all participants |
| `can_access(user_id, permission, resource_id?)` | Check permission |
| `get_visible_participants(user_id, resource_id?)` | Get visible to user |
| `transfer_ownership(from, to, resource_id?)` | Transfer ownership |
