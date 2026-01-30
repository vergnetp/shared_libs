"""
Migration replay utilities.

Allows replaying portable migrations on any database backend,
enabling smooth backend migrations.
"""

from pathlib import Path
from typing import List, Optional


async def replay_migration(db, migration_file: str):
    """
    Replay a single migration file on any backend.
    
    The migration file should use portable [bracket] syntax,
    which is converted to native SQL at runtime.
    
    Args:
        db: Database connection with sql_generator
        migration_file: Path to the .sql migration file
    
    Example:
        await replay_migration(db, "migrations_audit/20260130_120000_a1b2c3d4.sql")
    """
    with open(migration_file) as f:
        content = f.read()
    
    # Parse SQL statements (skip comments)
    statements = []
    current_stmt = []
    
    for line in content.split('\n'):
        stripped = line.strip()
        
        # Skip comment lines
        if stripped.startswith('--'):
            continue
        
        # Skip empty lines
        if not stripped:
            continue
        
        current_stmt.append(stripped)
        
        # Statement ends with semicolon
        if stripped.endswith(';'):
            # Join multi-line statement and remove trailing semicolon
            full_stmt = ' '.join(current_stmt)[:-1].strip()
            if full_stmt:
                statements.append(full_stmt)
            current_stmt = []
    
    # Execute each statement
    for sql in statements:
        # Convert [bracket] syntax to backend-native at runtime
        native_sql, params = db.sql_generator.convert_query_to_native(sql, ())
        await db.execute(native_sql, params)
    
    print(f"✓ Replayed migration: {Path(migration_file).name}")


async def replay_all_migrations(db, migration_dir: str = "./migrations_audit"):
    """
    Replay all migrations in a directory, in chronological order.
    
    Args:
        db: Database connection
        migration_dir: Directory containing .sql migration files
    
    Example:
        await replay_all_migrations(new_db, "./migrations_audit")
    """
    migration_path = Path(migration_dir)
    
    if not migration_path.exists():
        print(f"Migration directory not found: {migration_dir}")
        return
    
    # Get all .sql files, sorted by name (which includes timestamp)
    migration_files = sorted(migration_path.glob("*.sql"))
    
    if not migration_files:
        print(f"No migrations found in {migration_dir}")
        return
    
    print(f"Replaying {len(migration_files)} migrations...")
    
    for mig_file in migration_files:
        await replay_migration(db, str(mig_file))
    
    print(f"✓ All migrations applied")


async def get_pending_migrations(db, migration_dir: str = "./migrations_audit") -> List[str]:
    """
    Get list of migrations that haven't been applied yet.
    
    Args:
        db: Database connection
        migration_dir: Directory containing migration files
    
    Returns:
        List of migration file paths that haven't been applied
    """
    migration_path = Path(migration_dir)
    
    if not migration_path.exists():
        return []
    
    # Get all migrations from disk
    all_migrations = sorted(migration_path.glob("*.sql"))
    
    # Get applied migrations from database
    try:
        sql = "SELECT [schema_hash] FROM [_schema_migrations]"
        native_sql, params = db.sql_generator.convert_query_to_native(sql, ())
        result = await db.execute(native_sql, params)
        applied_hashes = {row[0] for row in result}
    except:
        # Migrations table doesn't exist - all migrations are pending
        return [str(f) for f in all_migrations]
    
    # Extract hash from filename (format: TIMESTAMP_HASH.sql)
    pending = []
    for mig_file in all_migrations:
        # Extract hash from filename
        filename = mig_file.stem  # Remove .sql extension
        if '_' in filename:
            hash_part = filename.split('_')[-1]
            if hash_part not in applied_hashes:
                pending.append(str(mig_file))
    
    return pending


async def verify_migrations(db, migration_dir: str = "./migrations_audit") -> dict:
    """
    Verify migration status of a database.
    
    Args:
        db: Database connection
        migration_dir: Directory containing migration files
    
    Returns:
        Dictionary with migration status information
    """
    migration_path = Path(migration_dir)
    
    if not migration_path.exists():
        return {
            "status": "error",
            "message": f"Migration directory not found: {migration_dir}"
        }
    
    all_migrations = sorted(migration_path.glob("*.sql"))
    
    try:
        sql = "SELECT [schema_hash], [applied_at] FROM [_schema_migrations] ORDER BY [id]"
        native_sql, params = db.sql_generator.convert_query_to_native(sql, ())
        result = await db.execute(native_sql, params)
        applied = [(row[0], row[1]) for row in result]
    except:
        applied = []
    
    pending = await get_pending_migrations(db, migration_dir)
    
    return {
        "status": "ok",
        "total_migrations": len(all_migrations),
        "applied_count": len(applied),
        "pending_count": len(pending),
        "pending_files": pending,
        "last_applied": applied[-1][1] if applied else None,
    }
