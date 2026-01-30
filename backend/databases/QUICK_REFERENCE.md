# Quick Reference

## Entity Definition

```python
from dataclasses import dataclass
from databases import entity, entity_field

@entity(table="users")
@dataclass
class User:
    email: str = entity_field(index=True, unique=True)
    name: str
    age: int = entity_field(default=0, nullable=True)
    status: str = entity_field(
        default="active",
        check="[status] IN ('active', 'inactive')"
    )
```

## Auto-Migration

```python
from databases.migrations import AutoMigrator

# Basic migration
migrator = AutoMigrator(db, audit_dir="./migrations_audit")
await migrator.auto_migrate()

# Dry run (preview changes)
await migrator.auto_migrate(dry_run=True)

# Enable deletions
migrator = AutoMigrator(
    db,
    allow_column_deletion=True,
    allow_table_deletion=True
)
await migrator.auto_migrate()
```

## Backup

```python
from databases.backup import BackupStrategy

strategy = BackupStrategy(db)

# Create backup (both native + CSV)
await strategy.backup_database("./backups")

# Native only (fast)
await strategy.backup_database("./backups", include_csv=False)

# CSV only (portable)
await strategy.backup_database("./backups", include_native=False)
```

## Rollback

```python
from databases.backup import rollback_to_date, rollback_to_backup, list_restore_points

# List available restore points
restore_points = list_restore_points()
for rp in restore_points:
    print(f"{rp.datetime.isoformat()} - {rp.schema_hash}")

# Rollback to date
await rollback_to_date(db, "2026-01-20", confirm=True)

# Rollback to specific backup
await rollback_to_backup(
    db,
    "csv_20260130_120000_a1b2c3d4",
    confirm=True
)
```

## Backend Migration

```python
from databases.backup import migrate_to_new_backend

# Direct migration (both DBs online)
await migrate_to_new_backend(
    old_url="sqlite:///dev.db",
    new_url="postgres://localhost/prod",
    data_method="direct"
)

# CSV-based migration
await migrate_to_new_backend(
    new_url="postgres://localhost/prod",
    data_method="csv",
    csv_export_dir="./backups/csv_20260130_120000"
)
```

## Entity Operations

```python
# Save entity
user = await db.save_entity("users", {
    "email": "phil@example.com",
    "name": "Phil"
})

# Find entities
users = await db.find_entities(
    "users",
    where_clause="[age] > ?",
    params=(18,)
)

# Get by ID
user = await db.get_entity("users", user_id)

# Delete
await db.delete_entity("users", user_id)
```

## Replay Migrations

```python
from databases.migrations import replay_migration

# Replay single migration
await replay_migration(db, "./migrations_audit/20260130_120000_a1b2c3d4.sql")
```
