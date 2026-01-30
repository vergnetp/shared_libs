"""
Complete backend migration workflow.

Orchestrates the full process of migrating from one database backend to another,
including schema migration and data transfer.
"""

from pathlib import Path
from typing import Optional

from .strategy import export_table_to_csv, import_table_from_csv
from ..migrations.replay import replay_all_migrations


async def copy_table_between_dbs(
    source_db,
    target_db,
    table_name: str,
    batch_size: int = 100
):
    """
    Copy data directly from one database to another.
    
    Works because the entity system handles serialization automatically.
    Both databases must be alive for this to work.
    
    Args:
        source_db: Source database connection
        target_db: Target database connection
        table_name: Name of the table to copy
        batch_size: Number of rows to copy per batch
    
    Example:
        source = await create_connection("sqlite:///old.db")
        target = await create_connection("postgres://...")
        await copy_table_between_dbs(source, target, "snapshots")
    """
    # Get all entities from source (including soft-deleted)
    entities = await source_db.find_entities(table_name, include_deleted=True)
    
    if not entities:
        print(f"  {table_name}: No data to copy")
        return
    
    print(f"  Copying {len(entities)} rows from {table_name}...")
    
    # Save in batches to target
    for i in range(0, len(entities), batch_size):
        batch = entities[i:i+batch_size]
        await target_db.save_entities(table_name, batch)
        print(f"    {min(i+batch_size, len(entities))}/{len(entities)}")
    
    print(f"  ✓ Copied {table_name}")


async def migrate_entire_database(
    source_url: str,
    target_url: str,
    batch_size: int = 100
):
    """
    Migrate entire database from one backend to another (direct copy).
    
    Both databases must be accessible. This is the fastest method
    when both databases are alive.
    
    Args:
        source_url: Source database URL
        target_url: Target database URL
        batch_size: Number of rows to copy per batch
    
    Example:
        await migrate_entire_database(
            "sqlite:///./data/app.db",
            "postgres://user:pass@localhost:5432/myapp"
        )
    """
    # Import here to avoid circular dependency
    from databases import create_connection
    
    print(f"Migrating from {source_url} to {target_url}")
    
    source_db = await create_connection(source_url)
    target_db = await create_connection(target_url)
    
    # Get all tables from source
    tables_sql, params = source_db.sql_generator.get_list_tables_sql()
    tables = await source_db.execute(tables_sql, params)
    
    # Filter out meta/history/system tables
    table_names = [
        row[0] for row in tables
        if not row[0].endswith('_meta')
        and not row[0].endswith('_history')
        and not row[0].startswith('_')
    ]
    
    print(f"\nCopying {len(table_names)} tables...")
    
    # Copy each table
    for table_name in table_names:
        await copy_table_between_dbs(source_db, target_db, table_name, batch_size)
    
    print("\n✓ Migration complete")


async def migrate_to_new_backend(
    old_url: str,
    new_url: str,
    migration_dir: str = "./migrations_audit",
    data_method: str = "direct",  # "direct", "csv"
    csv_export_dir: Optional[str] = None,
):
    """
    Complete migration workflow from one backend to another.
    
    Steps:
    1. Connect to new database
    2. Replay all schema migrations (portable [bracket] syntax)
    3. Copy data using chosen method
    
    Args:
        old_url: Source database URL
        new_url: Target database URL
        migration_dir: Directory containing portable migrations
        data_method: "direct" (both DBs alive) or "csv" (from CSV export)
        csv_export_dir: Directory with CSV exports (if method="csv")
    
    Example (direct):
        await migrate_to_new_backend(
            old_url="sqlite:///./data/app.db",
            new_url="postgres://user:pass@localhost:5432/myapp",
            data_method="direct"
        )
    
    Example (CSV):
        # First export from old DB
        old_db = await create_connection("sqlite:///./data/app.db")
        strategy = BackupStrategy(old_db)
        await strategy.backup_database("./backups", include_csv=True)
        
        # Then migrate using CSV
        await migrate_to_new_backend(
            old_url="sqlite:///./data/app.db",  # Not used with CSV
            new_url="postgres://user:pass@localhost:5432/myapp",
            data_method="csv",
            csv_export_dir="./backups/csv_20260130_120000"
        )
    """
    from databases import create_connection
    
    print(f"Migrating to {new_url}")
    
    # Connect to new database
    new_db = await create_connection(new_url)
    
    # Step 1: Replay schema migrations
    print("\n1. Replaying schema migrations...")
    await replay_all_migrations(new_db, migration_dir)
    
    # Step 2: Copy data
    print("\n2. Copying data...")
    
    if data_method == "direct":
        # Direct copy (requires both DBs alive)
        await migrate_entire_database(old_url, new_url)
    
    elif data_method == "csv":
        # Import from CSV files
        if not csv_export_dir:
            raise ValueError("csv_export_dir required when data_method='csv'")
        
        csv_path = Path(csv_export_dir)
        csv_files = list(csv_path.glob("*.csv"))
        
        print(f"  Importing from {len(csv_files)} CSV files...")
        
        for csv_file in csv_files:
            table_name = csv_file.stem
            await import_table_from_csv(new_db, table_name, str(csv_file))
    
    else:
        raise ValueError(f"Unknown data_method: {data_method}")
    
    print("\n✓ Migration complete!")


async def export_database_to_csv(db_url: str, output_dir: str):
    """
    Export entire database to CSV files.
    
    Useful for creating a portable backup before shutting down old database.
    
    Args:
        db_url: Database URL to export from
        output_dir: Directory to save CSV files
    
    Example:
        await export_database_to_csv(
            "sqlite:///./data/app.db",
            "./exports/2024-01-30"
        )
    """
    from databases import create_connection
    from .strategy import BackupStrategy
    
    db = await create_connection(db_url)
    strategy = BackupStrategy(db)
    
    result = await strategy.backup_database(
        output_dir,
        include_native=False,  # CSV only
        include_csv=True
    )
    
    print(f"\n✓ Exported to: {result['csv_dir']}")
    return result['csv_dir']
