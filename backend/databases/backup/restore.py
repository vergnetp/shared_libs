"""
Rollback and point-in-time restore utilities.

Uses filename-based schema hash linking to restore database to a previous state.
Automatically chooses between native restore (fast, same backend) and CSV restore
(portable, cross-backend) based on availability and compatibility.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import re

from ..migrations.replay import replay_migration
from .strategy import import_table_from_csv


# Backend detection mapping
BACKEND_EXTENSIONS = {
    "sqlite": [".backup", ".db", ".sqlite", ".sqlite3"],
    "postgres": [".dump", ".pgdump"],
    "mysql": [".sql", ".mysqldump"],
}


class RestorePoint:
    """Represents a restore point with matched migration and CSV backup."""
    
    def __init__(
        self,
        timestamp: str,
        schema_hash: str,
        csv_dir: Path,
        migration_file: Optional[Path] = None,
    ):
        self.timestamp = timestamp  # Format: YYYYMMDD_HHMMSS
        self.schema_hash = schema_hash
        self.csv_dir = csv_dir
        self.migration_file = migration_file
    
    def __repr__(self):
        return (
            f"RestorePoint(timestamp={self.timestamp}, "
            f"hash={self.schema_hash}, "
            f"csv={self.csv_dir.name})"
        )
    
    @property
    def datetime(self) -> datetime:
        """Convert timestamp string to datetime object"""
        return datetime.strptime(self.timestamp, "%Y%m%d_%H%M%S")


async def rollback_to_date(
    db,
    target_date: str,  # ISO format: "2026-01-20" or "2026-01-20T14:30:00"
    migration_dir: str = "./migrations_audit",
    backup_dir: str = "./backups",
    confirm: bool = False,
) -> bool:
    """
    Rollback database to a specific date.
    
    This is a DESTRUCTIVE operation that:
    1. Finds backup closest to target date (by filename timestamp)
    2. Drops all tables in database
    3. Replays migrations up to backup's schema hash
    4. Imports CSV data from backup
    
    Args:
        db: Database connection
        target_date: Target date/time (ISO format)
        migration_dir: Directory containing migration files
        backup_dir: Directory containing CSV backups
        confirm: Must be True to execute (safety check)
    
    Returns:
        True if rollback succeeded
    
    Example:
        success = await rollback_to_date(
            db,
            "2026-01-20",
            confirm=True  # Must explicitly confirm
        )
    """
    if not confirm:
        print("❌ Rollback not confirmed. Pass confirm=True to execute.")
        print("   This is a DESTRUCTIVE operation that will DELETE ALL DATA.")
        return False
    
    # Parse target date
    try:
        target_dt = datetime.fromisoformat(target_date)
    except ValueError:
        print(f"❌ Invalid date format: {target_date}")
        print("   Use ISO format: '2026-01-20' or '2026-01-20T14:30:00'")
        return False
    
    print(f"\n{'='*80}")
    print(f"🔄 ROLLBACK TO: {target_dt.isoformat()}")
    print(f"{'='*80}")
    print("⚠️  WARNING: This will DESTROY ALL CURRENT DATA!")
    print()
    
    # Find restore point closest to target date
    restore_point = find_restore_point(target_dt, migration_dir, backup_dir)
    
    if not restore_point:
        print("❌ No valid restore point found for target date")
        print(f"   Check that backups exist in: {backup_dir}")
        return False
    
    print(f"📍 Restore point found:")
    print(f"   Timestamp: {restore_point.datetime.isoformat()}")
    print(f"   Schema hash: {restore_point.schema_hash}")
    print(f"   CSV backup: {restore_point.csv_dir}")
    
    # Detect current backend and check for native backup
    current_backend = detect_backend(db)
    native_file = find_native_backup(restore_point, backup_dir)
    
    # Determine restore strategy
    use_native = False
    if native_file:
        backup_backend = detect_backup_backend(native_file)
        if backup_backend == current_backend:
            use_native = True
            print(f"   Native backup: {native_file.name} ({backup_backend})")
            print(f"\n✓ Backends match - using FAST native restore")
        else:
            print(f"   Native backup: {native_file.name} ({backup_backend})")
            print(f"   Current backend: {current_backend}")
            print(f"\n✓ Different backends - using PORTABLE CSV restore")
    else:
        print(f"\n✓ No native backup - using PORTABLE CSV restore")
    
    # Execute restore
    if use_native:
        # Fast path: Native restore (single operation)
        print(f"\n{'='*80}")
        print("🚀 NATIVE RESTORE (FAST)")
        print(f"{'='*80}")
        await restore_from_native(db, native_file, current_backend)
        print("   ✓ Database restored from native backup")
    else:
        # Portable path: Clear + Migrations + CSV (3 operations)
        # Step 1: Clear database
        print(f"\n{'='*80}")
        print("1️⃣  CLEARING DATABASE")
        print(f"{'='*80}")
        await clear_database(db)
        print("   ✓ All tables dropped")
        
        # Step 2: Replay migrations up to schema hash
        print(f"\n{'='*80}")
        print("2️⃣  REPLAYING MIGRATIONS")
        print(f"{'='*80}")
        migrations_to_replay = get_migrations_up_to_hash(
            restore_point.schema_hash,
            migration_dir
        )
        
        if not migrations_to_replay:
            print("   No migrations to replay (empty database backup)")
        else:
            print(f"   Found {len(migrations_to_replay)} migrations to replay")
            for i, mig_file in enumerate(migrations_to_replay, 1):
                print(f"   [{i}/{len(migrations_to_replay)}] {mig_file.name}")
                await replay_migration(db, str(mig_file))
        
        print("   ✓ Schema restored")
        
        # Step 3: Import CSV data
        print(f"\n{'='*80}")
        print("3️⃣  IMPORTING DATA")
        print(f"{'='*80}")
        await import_csv_backup(db, restore_point.csv_dir)
        print("   ✓ Data restored")
    
    # Success
    print(f"\n{'='*80}")
    print("✅ ROLLBACK COMPLETE")
    print(f"{'='*80}")
    print(f"Database restored to: {restore_point.datetime.isoformat()}")
    print(f"Schema hash: {restore_point.schema_hash}")
    print()
    
    return True


async def rollback_to_backup(
    db,
    backup_name: str,  # e.g., "csv_20260130_120000_a1b2c3d4"
    migration_dir: str = "./migrations_audit",
    backup_dir: str = "./backups",
    confirm: bool = False,
) -> bool:
    """
    Rollback to a specific backup by name.
    
    Useful when you know exactly which backup you want.
    
    Args:
        db: Database connection
        backup_name: Name of CSV backup directory
        migration_dir: Directory containing migrations
        backup_dir: Directory containing backups
        confirm: Must be True to execute
    
    Returns:
        True if rollback succeeded
    
    Example:
        await rollback_to_backup(
            db,
            "csv_20260130_120000_a1b2c3d4",
            confirm=True
        )
    """
    if not confirm:
        print("❌ Rollback not confirmed. Pass confirm=True to execute.")
        return False
    
    # Parse backup name to extract schema hash
    match = re.match(r'csv_(\d{8}_\d{6})_([a-f0-9]+)', backup_name)
    if not match:
        print(f"❌ Invalid backup name format: {backup_name}")
        print("   Expected format: csv_YYYYMMDD_HHMMSS_HASH")
        return False
    
    timestamp, schema_hash = match.groups()
    
    # Build full path
    backup_path = Path(backup_dir) / backup_name
    if not backup_path.exists():
        backup_path = Path(backup_name)  # Maybe full path
    
    if not backup_path.exists():
        print(f"❌ Backup not found: {backup_path}")
        return False
    
    # Create restore point
    restore_point = RestorePoint(
        timestamp=timestamp,
        schema_hash=schema_hash,
        csv_dir=backup_path,
    )
    
    print(f"\n{'='*80}")
    print(f"🔄 ROLLBACK TO BACKUP: {backup_name}")
    print(f"{'='*80}")
    print("⚠️  WARNING: This will DESTROY ALL CURRENT DATA!")
    print()
    print(f"📍 Restore point:")
    print(f"   Timestamp: {restore_point.datetime.isoformat()}")
    print(f"   Schema hash: {restore_point.schema_hash}")
    print(f"   CSV backup: {restore_point.csv_dir}")
    
    # Detect backend and check for native backup
    current_backend = detect_backend(db)
    native_file = find_native_backup(restore_point, backup_dir)
    
    # Determine restore strategy
    use_native = False
    if native_file:
        backup_backend = detect_backup_backend(native_file)
        if backup_backend == current_backend:
            use_native = True
            print(f"   Native backup: {native_file.name} ({backup_backend})")
            print(f"\n✓ Backends match - using FAST native restore")
        else:
            print(f"   Native backup: {native_file.name} ({backup_backend})")
            print(f"   Current backend: {current_backend}")
            print(f"\n✓ Different backends - using PORTABLE CSV restore")
    else:
        print(f"\n✓ No native backup - using PORTABLE CSV restore")
    
    # Execute restore
    if use_native:
        # Fast path: Native restore
        print(f"\n{'='*80}")
        print("🚀 NATIVE RESTORE (FAST)")
        print(f"{'='*80}")
        await restore_from_native(db, native_file, current_backend)
        print("   ✓ Database restored from native backup")
    else:
        # Portable path: Clear + Migrations + CSV
        # Clear database
        print(f"\n{'='*80}")
        print("1️⃣  CLEARING DATABASE")
        print(f"{'='*80}")
        await clear_database(db)
        print("   ✓ All tables dropped")
        
        # Replay migrations
        print(f"\n{'='*80}")
        print("2️⃣  REPLAYING MIGRATIONS")
        print(f"{'='*80}")
        migrations_to_replay = get_migrations_up_to_hash(schema_hash, migration_dir)
        
        if not migrations_to_replay:
            print("   No migrations to replay")
        else:
            print(f"   Found {len(migrations_to_replay)} migrations")
            for i, mig_file in enumerate(migrations_to_replay, 1):
                print(f"   [{i}/{len(migrations_to_replay)}] {mig_file.name}")
                await replay_migration(db, str(mig_file))
        
        print("   ✓ Schema restored")
        
        # Import data
        print(f"\n{'='*80}")
        print("3️⃣  IMPORTING DATA")
        print(f"{'='*80}")
        await import_csv_backup(db, restore_point.csv_dir)
        print("   ✓ Data restored")
    
    print(f"\n{'='*80}")
    print("✅ ROLLBACK COMPLETE")
    print(f"{'='*80}")
    
    return True


async def restore_from_history(
    db,
    target_time: str,  # ISO format: "2026-02-10T14:30:00Z"
    tables: list = None,  # None = all entity tables with history
    confirm: bool = False,
) -> dict:
    """
    Point-in-time restore using history tables — no CSV backup needed.
    
    For each entity table with history tracking:
    1. Find each row's state at target_time (latest version before cutoff)
    2. Overwrite main table rows with that state
    3. Soft-delete rows that didn't exist at target_time
    
    History tables are NEVER modified — you can always roll forward again.
    
    Args:
        db: Database connection
        target_time: Restore to this point (ISO format)
        tables: Specific tables to restore, or None for all
        confirm: Must be True to execute
    
    Returns:
        dict with per-table restore stats
    
    Example:
        # Restore everything to 2 hours ago
        result = await restore_from_history(db, "2026-02-10T14:30:00Z", confirm=True)
        
        # Restore just the services table
        result = await restore_from_history(db, "2026-02-10T14:30:00Z", tables=["services"], confirm=True)
    """
    from ..entity import ENTITY_SCHEMAS
    
    if not confirm:
        return {"error": "Pass confirm=True to execute. This overwrites current data."}
    
    # Validate target time
    try:
        from datetime import datetime, timezone
        target_dt = datetime.fromisoformat(target_time.replace('Z', '+00:00'))
    except ValueError:
        return {"error": f"Invalid time format: {target_time}. Use ISO format."}
    
    results = {}
    sql_gen = db.sql_generator
    
    # Determine which tables to restore
    for table_name, entity_class in ENTITY_SCHEMAS.items():
        if not getattr(entity_class, '__entity_history__', False):
            continue
        if tables and table_name not in tables:
            continue
        
        history_table = f"{table_name}_history"
        
        # Check history table exists
        if not await db._table_exists(history_table):
            results[table_name] = {"skipped": "no history table"}
            continue
        
        # Get column names from history table
        col_sql, col_params = sql_gen.get_list_columns_sql(history_table)
        col_result = await db.execute(col_sql, col_params)
        if col_result and len(col_result[0]) > 1 and isinstance(col_result[0][0], int):
            history_fields = [row[1] for row in col_result]
        else:
            history_fields = [row[0] for row in col_result]
        
        if not history_fields:
            results[table_name] = {"skipped": "empty schema"}
            continue
        
        history_meta_cols = {'version', 'history_timestamp', 'history_user_id', 'history_comment'}
        
        # Step 1: Get the latest history version of each row before cutoff
        snapshot_sql = (
            f"SELECT * FROM [{history_table}] "
            f"WHERE ([id], [version]) IN ("
            f"  SELECT [id], MAX([version]) FROM [{history_table}] "
            f"  WHERE [history_timestamp] <= ? "
            f"  GROUP BY [id]"
            f") "
        )
        native_sql, native_params = sql_gen.convert_query_to_native(snapshot_sql, (target_time,))
        rows = await db.execute(native_sql, native_params)
        
        if not rows:
            results[table_name] = {"skipped": "no history before target time"}
            continue
        
        # Build entity dicts from history rows (strip history-specific columns)
        snapshot_entities = []
        snapshot_ids = set()
        for row in rows:
            row_dict = dict(zip(history_fields, row))
            # Remove history-specific fields
            for hc in history_meta_cols:
                row_dict.pop(hc, None)
            snapshot_entities.append(row_dict)
            snapshot_ids.add(row_dict['id'])
        
        # Step 2: Upsert snapshot rows into main table using import_raw
        # This preserves original timestamps (no _prepare_entity mangling)
        restored = await db.import_raw(table_name, snapshot_entities)
        
        # Step 3: Record restore in history (audit trail)
        for entity in snapshot_entities:
            try:
                await db._add_to_history(
                    table_name, entity,
                    user_id="system:history_restore",
                    comment=f"Restored to {target_time}",
                )
            except Exception:
                pass  # History table might not exist for all entity types
        
        # Step 4: Soft-delete rows that didn't exist at target_time
        # These are rows created after the cutoff
        soft_deleted = 0
        all_ids_sql = f"SELECT [id] FROM [{table_name}] WHERE [deleted_at] IS NULL"
        native_sql, native_params = sql_gen.convert_query_to_native(all_ids_sql, ())
        current_rows = await db.execute(native_sql, native_params)
        
        orphan_ids = [row[0] for row in current_rows if row[0] not in snapshot_ids]
        
        if orphan_ids:
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ", ".join(["?"] * len(orphan_ids))
            delete_sql = (
                f"UPDATE [{table_name}] SET [deleted_at] = ? "
                f"WHERE [id] IN ({placeholders}) AND [deleted_at] IS NULL"
            )
            native_sql, native_params = sql_gen.convert_query_to_native(
                delete_sql, (now, *orphan_ids)
            )
            await db.execute(native_sql, native_params)
            soft_deleted = len(orphan_ids)
        
        results[table_name] = {
            "restored": restored,
            "soft_deleted": soft_deleted,
            "target_time": target_time,
        }
    
    return results


async def restore_single_table(
    db,
    table_name: str,
    target_time: str,
    confirm: bool = False,
) -> dict:
    """
    Convenience wrapper: restore a single table from history.
    
    Args:
        db: Database connection
        table_name: Table to restore
        target_time: Restore to this point (ISO format)
        confirm: Must be True to execute
    
    Returns:
        dict with restore stats for the table
    """
    result = await restore_from_history(db, target_time, tables=[table_name], confirm=confirm)
    return result.get(table_name, result)


def list_restore_points(
    migration_dir: str = "./migrations_audit",
    backup_dir: str = "./backups",
) -> List[RestorePoint]:
    """
    List all available restore points.
    
    Returns list sorted by timestamp (newest first).
    
    Example:
        restore_points = list_restore_points()
        for rp in restore_points:
            print(f"{rp.datetime.isoformat()} - {rp.schema_hash}")
    """
    backup_path = Path(backup_dir)
    migration_path = Path(migration_dir)
    
    if not backup_path.exists():
        return []
    
    restore_points = []
    
    # Find all CSV backup directories
    for csv_dir in sorted(backup_path.glob("csv_*"), reverse=True):
        # Parse: csv_YYYYMMDD_HHMMSS_HASH
        match = re.match(r'csv_(\d{8}_\d{6})_([a-f0-9]+)', csv_dir.name)
        if not match:
            continue
        
        timestamp, schema_hash = match.groups()
        
        # Find corresponding migration file
        migration_file = None
        for mig in migration_path.glob(f"*_{schema_hash}.sql"):
            migration_file = mig
            break
        
        restore_points.append(RestorePoint(
            timestamp=timestamp,
            schema_hash=schema_hash,
            csv_dir=csv_dir,
            migration_file=migration_file,
        ))
    
    return restore_points


def find_restore_point(
    target_date: datetime,
    migration_dir: str,
    backup_dir: str,
) -> Optional[RestorePoint]:
    """
    Find restore point closest to (but not after) target date.
    
    Args:
        target_date: Target datetime
        migration_dir: Directory containing migrations
        backup_dir: Directory containing backups
    
    Returns:
        RestorePoint closest to target, or None
    """
    restore_points = list_restore_points(migration_dir, backup_dir)
    
    if not restore_points:
        return None
    
    # Find closest backup at or before target date
    for rp in restore_points:  # Already sorted newest first
        if rp.datetime <= target_date:
            return rp
    
    # If all backups after target, return oldest
    return restore_points[-1]


def get_migrations_up_to_hash(
    target_hash: str,
    migration_dir: str,
) -> List[Path]:
    """
    Get all migration files up to and including the target schema hash.
    
    Args:
        target_hash: Schema hash to stop at
        migration_dir: Directory containing migrations
    
    Returns:
        List of migration paths in chronological order
    """
    migration_path = Path(migration_dir)
    
    if not migration_path.exists():
        return []
    
    # Get all migrations sorted by filename (chronological)
    all_migrations = sorted(migration_path.glob("*.sql"))
    
    migrations_to_replay = []
    
    for mig_file in all_migrations:
        migrations_to_replay.append(mig_file)
        
        # Extract hash from filename: YYYYMMDD_HHMMSS_HASH.sql
        filename = mig_file.stem
        parts = filename.split('_')
        if len(parts) >= 3:
            hash_part = parts[-1]
            
            # Check if this is the target hash
            if hash_part == target_hash or hash_part.startswith(target_hash):
                break  # Found it! Stop here
    
    return migrations_to_replay


async def clear_database(db):
    """Drop all tables from database (DESTRUCTIVE)."""
    # Get all tables
    tables_sql, params = db.sql_generator.get_list_tables_sql()
    result = await db.execute(tables_sql, params)
    
    all_tables = [row[0] for row in result]
    
    # Drop each table
    for table_name in all_tables:
        try:
            drop_sql = f"DROP TABLE IF EXISTS [{table_name}]"
            native_sql, native_params = db.sql_generator.convert_query_to_native(drop_sql, ())
            await db.execute(native_sql, native_params)
        except Exception as e:
            print(f"   Warning: Could not drop {table_name}: {e}")


async def import_csv_backup(db, csv_dir: Path):
    """
    Import all CSV files from backup directory.
    
    Import order matters:
    1. Meta tables first (*_meta.csv) — needed for deserialization
    2. Entity tables (no suffix) — main data
    3. History tables last (*_history.csv) — audit trail
    """
    csv_files = sorted(csv_dir.glob("*.csv"))
    
    if not csv_files:
        print("   No CSV files found")
        return
    
    # Partition into ordered groups
    meta_files = [f for f in csv_files if f.stem.endswith('_meta')]
    history_files = [f for f in csv_files if f.stem.endswith('_history')]
    entity_files = [f for f in csv_files if f not in meta_files and f not in history_files]
    
    ordered = meta_files + entity_files + history_files
    
    for i, csv_file in enumerate(ordered, 1):
        table_name = csv_file.stem
        print(f"   [{i}/{len(ordered)}] Importing {table_name}...", end=" ")
        
        try:
            await import_table_from_csv(db, table_name, str(csv_file))
        except Exception as e:
            print(f"✗ ({e})")


def detect_backend(db) -> str:
    """
    Detect database backend from connection.
    
    Returns:
        Backend type: "sqlite", "postgres", or "mysql"
    """
    backend_type = type(db.sql_generator).__name__.lower()
    
    if "sqlite" in backend_type:
        return "sqlite"
    elif "postgres" in backend_type:
        return "postgres"
    elif "mysql" in backend_type:
        return "mysql"
    else:
        return "unknown"


def detect_backup_backend(backup_file: Path) -> Optional[str]:
    """
    Detect backend from backup file extension.
    
    Args:
        backup_file: Path to native backup file
    
    Returns:
        Backend type: "sqlite", "postgres", "mysql", or None
    """
    ext = backup_file.suffix.lower()
    
    for backend, extensions in BACKEND_EXTENSIONS.items():
        if ext in extensions:
            return backend
    
    return None


def find_native_backup(restore_point: RestorePoint, backup_dir: str) -> Optional[Path]:
    """
    Find native backup file matching restore point.
    
    Args:
        restore_point: RestorePoint to find backup for
        backup_dir: Directory containing backups
    
    Returns:
        Path to native backup file, or None
    """
    backup_path = Path(backup_dir)
    
    # Look for native_TIMESTAMP_HASH.* files
    pattern = f"native_{restore_point.timestamp}_{restore_point.schema_hash}.*"
    
    for native_file in backup_path.glob(pattern):
        # Check if it's a valid backend extension
        if detect_backup_backend(native_file):
            return native_file
    
    return None


async def restore_from_native(db, native_file: Path, backend: str):
    """
    Restore database from native backup file.
    
    Args:
        db: Database connection
        native_file: Path to native backup file
        backend: Backend type ("sqlite", "postgres", "mysql")
    """
    if backend == "sqlite":
        await restore_sqlite_native(db, native_file)
    elif backend == "postgres":
        await restore_postgres_native(db, native_file)
    elif backend == "mysql":
        await restore_mysql_native(db, native_file)
    else:
        raise ValueError(f"Unsupported backend for native restore: {backend}")


async def restore_sqlite_native(db, backup_file: Path):
    """
    Restore SQLite database from VACUUM INTO backup.
    
    SQLite restore process:
    1. Close current connection
    2. Replace database file with backup
    3. Reconnect
    
    Note: This requires knowing the database file path.
    """
    # For SQLite, we need to close connection and copy file
    # This is backend-specific and requires database file path
    
    # Get database file path from connection
    # This varies by implementation - may need to be passed in
    
    print("   SQLite native restore:")
    print("   1. Close connection")
    print("   2. Copy backup file to database location")
    print("   3. Reconnect")
    print()
    print(f"   Manual steps required:")
    print(f"   1. Close your application")
    print(f"   2. Copy: {backup_file} → your_database.db")
    print(f"   3. Restart application")
    
    # For now, raise NotImplementedError
    # In production, you'd implement connection close/reopen logic
    raise NotImplementedError(
        "SQLite native restore requires manual file copy. "
        f"Copy {backup_file} to your database location."
    )


async def restore_postgres_native(db, dump_file: Path):
    """
    Restore PostgreSQL database from pg_dump file.
    
    PostgreSQL restore process:
    1. Drop all tables (or drop/recreate database)
    2. Run pg_restore
    
    Note: Requires pg_restore command and connection details.
    """
    print(f"   PostgreSQL native restore:")
    print(f"   Run: pg_restore -d dbname {dump_file}")
    print()
    print(f"   Manual steps required:")
    print(f"   1. Ensure PostgreSQL client tools installed")
    print(f"   2. Run: pg_restore -d your_database {dump_file}")
    
    # For now, raise NotImplementedError
    # In production, you'd use subprocess to call pg_restore
    raise NotImplementedError(
        f"PostgreSQL native restore requires pg_restore. "
        f"Run: pg_restore -d dbname {dump_file}"
    )


async def restore_mysql_native(db, sql_file: Path):
    """
    Restore MySQL database from mysqldump file.
    
    MySQL restore process:
    1. Drop all tables (or drop/recreate database)
    2. Run mysql < dump.sql
    
    Note: Requires mysql command and connection details.
    """
    print(f"   MySQL native restore:")
    print(f"   Run: mysql dbname < {sql_file}")
    print()
    print(f"   Manual steps required:")
    print(f"   1. Ensure MySQL client tools installed")
    print(f"   2. Run: mysql your_database < {sql_file}")
    
    # For now, raise NotImplementedError
    # In production, you'd use subprocess to call mysql
    raise NotImplementedError(
        f"MySQL native restore requires mysql client. "
        f"Run: mysql dbname < {sql_file}"
    )
