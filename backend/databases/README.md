# Schema-First Entity Framework

Database library with schema-first architecture. Define entities as dataclasses, get CRUD methods auto-generated.

## Quick Start

### 1. Define Entity Schemas

```python
# schemas.py
from dataclasses import dataclass
from typing import List, Optional
from databases import entity, entity_field

@entity(table="projects")
@dataclass
class Project:
    name: str
    tags: List[str] = entity_field(default=None)  # JSON auto-serialized
    workspace_id: str = entity_field(index=True)
    id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None
```

### 2. Use Your Entities (No Store Layer Needed!)

```python
import schemas  # Registers @entity classes

# CRUD is built into the entity class:
project = await Project.get(db, "123")
project = await Project.create(db, {"name": "test", "workspace_id": "ws1"})
project = await Project.update(db, "123", {"name": "new name"})
projects = await Project.find(db, where="workspace_id = ?", params=("ws1",))
await Project.delete(db, "123", permanent=True)
await Project.soft_delete(db, "123")

# Dict-like access works:
print(project["name"])  # or project.name
print(project.tags)     # List - auto-deserialized from JSON
```

### 3. Auto-Migrate on Startup

```python
from databases import DatabaseManager
from databases.migrations import AutoMigrator

async def startup():
    db = DatabaseManager("sqlite", database="app.db")
    
    async with db as conn:
        migrator = AutoMigrator(conn)
        await migrator.auto_migrate()  # Creates/updates tables
    
    return db
```

## What @entity Provides

The `@entity` decorator auto-adds these methods to your dataclass:

| Method | Description |
|--------|-------------|
| `Entity.get(db, id)` | Fetch by ID |
| `Entity.create(db, data)` | Insert with auto id/timestamps |
| `Entity.save(db, data)` | Alias for create |
| `Entity.update(db, id, data)` | Merge and save |
| `Entity.delete(db, id, permanent=False)` | Hard or soft delete |
| `Entity.soft_delete(db, id)` | Set deleted_at |
| `Entity.find(db, where=, params=, ...)` | Query with filters |
| `Entity.count(db, where=, params=)` | Count matching |
| `Entity.from_dict(data)` | Create instance from dict |

**Plus dict-like access:**
```python
project["name"]      # __getitem__
project["name"] = x  # __setitem__
project.get("name")  # .get() with default
list(project.keys()) # iterate fields
```

## Smart JSON Deserialization

Fields with `List` or `Dict` type hints are auto-deserialized:

```python
@entity(table="deployments")
@dataclass
class Deployment:
    droplet_ids: List[str] = entity_field(default=None)
    env_vars: Dict[str, str] = entity_field(default=None)

# In DB: '["drop1","drop2"]' (JSON string)
# In Python: ['drop1', 'drop2'] (list)

deployment = await Deployment.get(db, "123")
print(deployment.droplet_ids)  # ['drop1', 'drop2'] - already a list!
```

**No manual json.loads() needed.** Type hints drive deserialization.

## Custom Queries (Optional Stores)

For app-specific queries, create thin store modules:

```python
# stores/projects.py
from ..schemas import Project

# Re-export for backward compatibility: `projects.get(db, id)`
get = Project.get
create = Project.save
update = Project.update
delete = Project.delete

# Custom queries only
async def get_by_name(db, workspace_id: str, name: str):
    results = await Project.find(
        db,
        where="workspace_id = ? AND name = ? AND deleted_at IS NULL",
        params=(workspace_id, name),
        limit=1,
    )
    return results[0] if results else None
```

## Entity Field Options

```python
@entity(table="users")
@dataclass
class User:
    # Required field
    email: str = entity_field(unique=True, nullable=False)
    
    # With index
    workspace_id: str = entity_field(index=True)
    
    # With default
    role: str = entity_field(default="user")
    
    # JSON field (auto-serialized)
    permissions: List[str] = entity_field(default=None)
    
    # With check constraint
    status: str = entity_field(
        default="active",
        check="[status] IN ('active', 'suspended', 'deleted')"
    )
```

## Configuration: migrations_on Flag

| migrations_on | Behavior |
|---------------|----------|
| `True` (default) | Skip runtime DDL. Use AutoMigrator at startup. **Fast.** |
| `False` | Allow runtime ALTER TABLE (legacy POC mode). |

```python
# Recommended (default)
db = DatabaseManager("sqlite", database="app.db")

# Legacy POC mode
db = DatabaseManager("sqlite", database="app.db", migrations_on=False)
```

## Auto-Migration

```python
# Safe (default: no deletions)
migrator = AutoMigrator(db)
await migrator.auto_migrate()

# Preview changes
await migrator.auto_migrate(dry_run=True)

# Enable deletions (backup first!)
migrator = AutoMigrator(
    db,
    allow_column_deletion=True,
    allow_table_deletion=True
)
```

## Backup & Restore

```python
from databases.backup import BackupStrategy, rollback_to_date

# Create backup
strategy = BackupStrategy(db)
await strategy.backup_database("./backups")

# Rollback
await rollback_to_date(db, "2026-01-20", confirm=True)
```

## Performance

| Operation | Before (Runtime DDL) | After (Schema-First) |
|-----------|---------------------|----------------------|
| save_entity() | 60 seconds (timeout) | 0.05 seconds |
| find_entities() | With deserialize overhead | Fast (type-hint driven) |
| Startup | Instant | +1 second (migration) |

## New App Workflow

1. **Create `schemas.py`** with `@entity` dataclasses
2. **Import schemas** before app startup (registers entities)
3. **Run AutoMigrator** at startup
4. **Use Entity.get/create/update/find** directly
5. **Optional:** Add store modules for custom queries

No BaseStore class needed. No models.py needed. Just schemas.

---

## API Reference

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### function `entity`

Decorator to mark a dataclass as a database entity with auto-generated CRUD methods.

<details>
<summary><strong>Parameters</strong></summary>

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table` | `str` | Class name lowercase | Database table name |
| `history` | `bool` | `True` | Whether to create history table |

</details>

<br>

<details>
<summary><strong>Auto-Added Methods</strong></summary>

| Decorators | Method | Args | Returns | Description |
|------------|--------|------|---------|-------------|
| `@classmethod` | `get` | `db`, `id: str` | `Entity \| None` | Fetch entity by ID |
| `@classmethod` | `create` | `db`, `data: dict` | `Entity` | Insert with auto id/timestamps |
| `@classmethod` | `save` | `db`, `data: dict` | `Entity` | Alias for create |
| `@classmethod` | `update` | `db`, `id: str`, `data: dict` | `Entity \| None` | Merge with existing and save |
| `@classmethod` | `delete` | `db`, `id: str`, `permanent: bool=False` | `bool` | Hard or soft delete |
| `@classmethod` | `soft_delete` | `db`, `id: str` | `bool` | Set deleted_at timestamp |
| `@classmethod` | `find` | `db`, `where: str=None`, `params: tuple=None`, `order_by: str=None`, `limit: int=None`, `offset: int=None`, `include_deleted: bool=False` | `List[Entity]` | Query with filters |
| `@classmethod` | `count` | `db`, `where: str=None`, `params: tuple=None`, `include_deleted: bool=False` | `int` | Count matching entities |
| `@classmethod` | `from_dict` | `data: dict` | `Entity` | Create instance, deserializing JSON fields by type hints |

</details>

<br>

<details>
<summary><strong>Dict-Like Access (on instances)</strong></summary>

| Method | Description |
|--------|-------------|
| `entity["key"]` | Get attribute value |
| `entity["key"] = value` | Set attribute value |
| `entity.get("key", default)` | Get with default |
| `list(entity.keys())` | List field names |
| `list(entity.items())` | List (name, value) pairs |
| `"key" in entity` | Check if field exists |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### function `entity_field`

Create a dataclass field with database metadata.

<details>
<summary><strong>Parameters</strong></summary>

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default` | `Any` | `None` | Default value |
| `index` | `bool` | `False` | Create index on this field |
| `unique` | `bool` | `False` | Unique constraint |
| `nullable` | `bool` | `True` | Allow NULL values |
| `foreign_key` | `str` | `None` | Foreign key reference (e.g., "users.id") |
| `check` | `str` | `None` | SQL CHECK constraint |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AutoMigrator`

Automatic database migration system.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Description |
|------------|--------|------|---------|-------------|
| `async` | `auto_migrate` | `dry_run: bool=False` | `None` | Detect and apply schema changes |

</details>

<br>

<details>
<summary><strong>Constructor</strong></summary>

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db` | `Connection` | required | Database connection |
| `audit_dir` | `str` | `"./migrations_audit"` | Directory for migration files |
| `allow_column_deletion` | `bool` | `False` | Auto-drop removed columns |
| `allow_table_deletion` | `bool` | `False` | Auto-drop removed tables |

</details>

</div>
