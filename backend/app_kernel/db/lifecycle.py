"""
Database lifecycle management: automated backups and schema migrations.

Handles:
- Automated backups on startup (always)
- Schema migrations (only if @entity schemas detected)
- Backend change detection (SQLite → Postgres, etc.)
- Configurable backup/migration directories
"""

import os
from pathlib import Path
from typing import Optional, Tuple
from ... import log as logger
from ...databases.backup import BackupStrategy, list_restore_points
from ...databases.backup.restore import import_csv_backup
from ...databases.entity import ENTITY_SCHEMAS
from ...databases.migrations import AutoMigrator


def _get_backend_type(db_connection) -> str:
    """
    Detect database backend type from connection.
    
    Returns:
        "sqlite", "postgres", or "mysql"
    """
    backend_class = type(db_connection.sql_generator).__name__.lower()
    
    if "sqlite" in backend_class:
        return "sqlite"
    elif "postgres" in backend_class:
        return "postgres"
    elif "mysql" in backend_class:
        return "mysql"
    else:
        return "unknown"


def _get_stored_backend(data_dir: str) -> Optional[str]:
    """
    Get previously used backend from .db_backend file.
    
    Returns:
        Backend type string or None if file doesn't exist
    """
    backend_file = Path(data_dir) / ".db_backend"
    if backend_file.exists():
        return backend_file.read_text().strip()
    return None


def _store_backend(data_dir: str, backend: str):
    """Store current backend type for future detection."""
    backend_file = Path(data_dir) / ".db_backend"
    backend_file.write_text(backend)


def _check_backend_change(db_connection, data_dir: str) -> Tuple[bool, Optional[str], str]:
    """
    Check if database backend has changed.
    
    Returns:
        (changed: bool, old_backend: str, new_backend: str)
    """
    current_backend = _get_backend_type(db_connection)
    stored_backend = _get_stored_backend(data_dir)
    
    if stored_backend is None:
        # First run, no backend stored yet
        return False, None, current_backend
    
    if stored_backend != current_backend:
        # Backend changed!
        return True, stored_backend, current_backend
    
    # Same backend
    return False, stored_backend, current_backend


async def run_database_lifecycle(
    db_connection,
    data_dir: str = ".data",
    backup_enabled: bool = True,
    migration_enabled: bool = True,
) -> dict:
    """
    Run automated backup and migration on database startup.
    
    Args:
        db_connection: Database connection from get_db_connection()
        data_dir: Base directory for backups and migrations (default: .data)
        backup_enabled: Whether to create backup (default: True)
        migration_enabled: Whether to run migrations (default: True)
    
    Returns:
        dict with backup and migration results
    
    Example:
        from .session import get_db_connection
        from .db.lifecycle import run_database_lifecycle
        
        db = await get_db_connection()
        result = await run_database_lifecycle(db)
        # Creates .data/backups/ and .data/migrations_audit/
    """
    results = {
        "backup_created": False,
        "migration_applied": False,
        "backend_changed": False,
        "backup_path": None,
        "migration_id": None,
    }
    
    # Ensure data directory exists
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    
    backup_dir = str(data_path / "backups")
    migrations_dir = str(data_path / "migrations_audit")
    
    # Create directories
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    Path(migrations_dir).mkdir(parents=True, exist_ok=True)
    
    try:
        # Check for backend change FIRST
        backend_changed, old_backend, new_backend = _check_backend_change(
            db_connection, data_dir
        )
        
        if backend_changed:
            results["backend_changed"] = True
            
            logger.warning(
                f"Database backend changed: {old_backend} → {new_backend}",
                extra={
                    "old_backend": old_backend,
                    "new_backend": new_backend,
                }
            )
            
            print(f"\n{'='*80}")
            print(f"🔄 DATABASE BACKEND CHANGED: {old_backend} → {new_backend}")
            print(f"{'='*80}")
            
            # Try to auto-migrate using latest CSV backup            
            try:
                restore_points = list_restore_points(backup_dir)
                
                if not restore_points:
                    print("⚠️  No backups found - cannot auto-migrate data")
                    print("Starting with empty database on new backend.")
                    print(f"{'='*80}\n")
                else:
                    # Use latest backup
                    latest = restore_points[0]  # Sorted newest first
                    
                    print(f"✓ Found backup from {latest.datetime.isoformat()}")
                    print(f"  Auto-migrating data to new backend...")
                    
                    # Import CSV data to new backend                    
                    csv_dir = Path(backup_dir) / latest.backup_id
                    
                    await import_csv_backup(db_connection, csv_dir)
                    
                    print(f"✓ Data migrated successfully from {old_backend} to {new_backend}")
                    print(f"{'='*80}\n")
                    
                    logger.info(f"Auto-migration completed", extra={
                        "old_backend": old_backend,
                        "new_backend": new_backend,
                        "backup_used": latest.backup_id,
                    })
            
            except Exception as e:
                logger.error(f"Auto-migration failed: {e}")
                print(f"⚠️  Auto-migration failed: {e}")
                print(f"Starting with empty database on new backend.")
                print(f"{'='*80}\n")
            
            # Update stored backend
            _store_backend(data_dir, new_backend)
            
            # Create backup on new backend (after migration)
            if backup_enabled:                
                logger.info(f"Creating backup on new backend ({new_backend})...")
                strategy = BackupStrategy(db_connection)
                
                try:
                    result = await strategy.backup_database(
                        backup_dir,
                        include_native=True,
                        include_csv=True
                    )
                    
                    results["backup_created"] = True
                    results["backup_path"] = result.get("csv_dir")
                    
                    logger.info("Backup created on new backend", extra={
                        "backend": new_backend,
                        "backup_dir": backup_dir,
                    })
                except Exception as e:
                    logger.error(f"Backup failed: {e}", extra={"error": str(e)})
            
            # Now run migrations on new backend (with migrated data)
            if migration_enabled:               
                if ENTITY_SCHEMAS:
                    logger.info(f"Running migrations on new backend...")
                    migrator = AutoMigrator(
                        db_connection,
                        audit_dir=migrations_dir,
                        allow_column_deletion=False,
                        allow_table_deletion=False,
                    )
                    
                    try:
                        migration_result = await migrator.auto_migrate()
                        
                        if migration_result:
                            results["migration_applied"] = True
                            results["migration_id"] = migration_result.get("migration_id")
                            
                            logger.info("Database migration completed on new backend")
                    
                    except Exception as e:
                        logger.error(f"Migration failed on new backend: {e}")
                        raise
            
            # Backend change handled, continue normally
            return results
        
        # Store backend (first run or same backend)
        _store_backend(data_dir, new_backend)
        
        # Step 1: Create backup (skip in non-prod, skip if empty)
        if backup_enabled:
            from ..env_checks import is_prod
            
            if not is_prod():
                logger.info("Skipping backup in non-prod environment")
            else:
                # Check if database has any user tables
                try:
                    tables = await db_connection.list_tables()
                    user_tables = [t for t in tables if not t.startswith('_')]
                    
                    if not user_tables:
                        logger.info("Skipping backup - database is empty")
                    else:
                        logger.info("Creating database backup...")
                        strategy = BackupStrategy(db_connection)
                        
                        result = await strategy.backup_database(
                            backup_dir,
                            include_native=True,
                            include_csv=True
                        )
                        
                        results["backup_created"] = True
                        results["backup_path"] = result.get("csv_dir")
                        
                        logger.info("Database backup created", extra={
                            "backup_dir": backup_dir,
                            "tables": len(user_tables),
                        })
                except Exception as e:
                    logger.error(f"Backup failed: {e}", extra={"error": str(e)})
                    # Continue even if backup fails
        
        # Step 2: Run migrations (only if @entity schemas detected)
        if migration_enabled:
            # Check if any entities are registered
            if not ENTITY_SCHEMAS:
                logger.info("No @entity schemas detected - skipping migration")
            else:
                logger.info(f"Found {len(ENTITY_SCHEMAS)} entity schemas", extra={
                    "entities": list(ENTITY_SCHEMAS.keys())
                })
                
                logger.info("Running database migration...")
                migrator = AutoMigrator(
                    db_connection,
                    audit_dir=migrations_dir,
                    allow_column_deletion=False,  # Safe defaults
                    allow_table_deletion=False,
                )
                
                try:
                    migration_result = await migrator.auto_migrate()
                    
                    if migration_result:
                        results["migration_applied"] = True
                        results["migration_id"] = migration_result.get("migration_id")
                        
                        logger.info("Database migration completed", extra={
                            "migrations_dir": migrations_dir,
                            "migration_id": migration_result.get("migration_id"),
                            "changes": migration_result.get("changes", 0),
                        })
                    else:
                        logger.info("No schema changes detected - database is up to date")
                
                except Exception as e:
                    logger.error(f"Migration failed: {e}", extra={"error": str(e)})
                    raise  # Fail startup if migration fails
    
    except Exception as e:
        logger.error(f"Database lifecycle error: {e}")
        raise
    
    return results


def get_lifecycle_config(
    backup_enabled: bool = None,
    migration_enabled: bool = True,
    data_dir: str = ".data",
) -> dict:
    """
    Get lifecycle configuration.
    
    Args:
        backup_enabled: Enable/disable backups. Default: False in dev/uat/staging, True in prod.
        migration_enabled: Enable/disable migrations (default: True)
        data_dir: Base data directory (default: .data)
    
    Returns:
        dict with configuration
    """
    from ..env_checks import is_prod
    
    # Default backup to false in non-prod (too slow), true in prod
    if backup_enabled is None:
        backup_enabled = is_prod()
    
    return {
        "backup_enabled": backup_enabled,
        "migration_enabled": migration_enabled,
        "data_dir": data_dir,
    }
