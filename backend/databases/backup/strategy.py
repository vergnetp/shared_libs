"""
Hybrid backup strategy: Native + CSV exports.

Provides both fast native backups (for same-backend recovery)
and portable CSV exports (for backend migration and inspection).
"""

import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List


class BackupStrategy:
    """
    Hybrid backup strategy combining native and CSV backups.
    
    Native backups are fast and complete (for disaster recovery).
    CSV backups are portable and inspectable (for backend migration).
    """
    
    def __init__(self, db):
        """
        Initialize backup strategy.
        
        Args:
            db: Database connection
        """
        self.db = db
        self.sql_gen = db.sql_generator
    
    async def backup_database(
        self,
        backup_dir: str,
        include_native: bool = True,
        include_csv: bool = True,
    ) -> Dict:
        """
        Create complete database backup.
        
        Args:
            backup_dir: Directory to save backups
            include_native: Whether to create native backup
            include_csv: Whether to create CSV export
        
        Returns:
            Dictionary with backup file locations
        """
        backup_path = Path(backup_dir)
        backup_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Get current schema hash for filename
        schema_hash = await self._get_current_schema_hash()
        
        result = {
            "timestamp": timestamp,
            "schema_hash": schema_hash,
        }
        
        # 1. Native backup (backend-specific, fast)
        if include_native:
            # Use backend-specific extension
            extension = self._get_native_extension()
            native_file = backup_path / f"native_{timestamp}_{schema_hash[:8]}{extension}"
            await self._native_backup(native_file)
            result["native"] = str(native_file)
            print(f"✓ Created native backup: {native_file}")
        
        # 2. CSV export (portable, inspectable)
        if include_csv:
            csv_dir = backup_path / f"csv_{timestamp}_{schema_hash[:8]}"
            await self._csv_backup(csv_dir)
            result["csv_dir"] = str(csv_dir)
            print(f"✓ Created CSV export: {csv_dir}")
        
        # 3. Metadata snapshot
        meta_file = backup_path / f"metadata_{timestamp}.json"
        await self._save_metadata(meta_file)
        result["metadata"] = str(meta_file)
        
        return result
    
    async def _native_backup(self, output_file: Path):
        """
        Create backend-specific native backup.
        
        This is the fastest way to backup/restore within the same backend.
        """
        backend_type = type(self.sql_gen).__name__
        
        if "Sqlite" in backend_type:
            # SQLite: Use VACUUM INTO
            sql = f"VACUUM INTO ?"
            native_sql, _ = self.sql_gen.convert_query_to_native(sql, ())
            # For SQLite, we need to use the raw SQL with string parameter
            await self.db.execute(f"VACUUM INTO '{output_file}'", ())
        
        elif "Postgres" in backend_type:
            # PostgreSQL: Use pg_dump
            # Note: This requires connection details from db.config
            # For now, document the manual approach
            raise NotImplementedError(
                "PostgreSQL native backup requires pg_dump. "
                "Run: pg_dump -Fc -f backup.dump dbname"
            )
        
        elif "Mysql" in backend_type:
            # MySQL: Use mysqldump
            raise NotImplementedError(
                "MySQL native backup requires mysqldump. "
                "Run: mysqldump dbname > backup.sql"
            )
        
        else:
            raise ValueError(f"Unknown backend: {backend_type}")
    
    async def _csv_backup(self, output_dir: Path):
        """
        Export all entity tables to CSV files.
        
        This creates portable, human-readable backups.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Get all tables
        tables_sql, params = self.sql_gen.get_list_tables_sql()
        tables = await self.db.execute(tables_sql, params)
        
        for table_row in tables:
            table_name = table_row[0]
            
            # Skip meta/history/system tables
            if (table_name.endswith('_meta') or 
                table_name.endswith('_history') or 
                table_name.startswith('_')):
                continue
            
            # Export to CSV
            await self._export_table_csv(table_name, output_dir)
    
    async def _export_table_csv(self, table_name: str, output_dir: Path):
        """Export a single table to CSV"""
        try:
            entities = await self.db.find_entities(table_name, include_deleted=True)
            
            if not entities:
                return
            
            csv_file = output_dir / f"{table_name}.csv"
            
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=entities[0].keys())
                writer.writeheader()
                writer.writerows(entities)
            
            print(f"  Exported {len(entities)} rows from {table_name}")
        
        except Exception as e:
            print(f"  Warning: Could not export {table_name}: {e}")
    
    async def _save_metadata(self, output_file: Path):
        """Save database metadata for reference"""
        backend_type = type(self.sql_gen).__name__
        
        # Get all tables
        tables_sql, params = self.sql_gen.get_list_tables_sql()
        tables = await self.db.execute(tables_sql, params)
        
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "backend": backend_type,
            "tables": [row[0] for row in tables],
        }
        
        with open(output_file, 'w') as f:
            json.dump(metadata, f, indent=2)
    
    async def _get_current_schema_hash(self) -> str:
        """
        Get current schema hash from database.
        
        Returns the hash of the most recently applied migration,
        or empty string if no migrations have been applied.
        """
        try:
            # First check if the table exists to avoid retry loops
            tables = await self.db.list_tables()
            if "_schema_migrations" not in tables:
                return "00000000"
            
            # Query the _schema_migrations table for the latest hash
            sql = "SELECT [schema_hash] FROM [_schema_migrations] ORDER BY [id] DESC LIMIT 1"
            native_sql, params = self.sql_gen.convert_query_to_native(sql, ())
            result = await self.db.execute(native_sql, params)
            
            if result and len(result) > 0:
                full_hash = result[0][0]
                return full_hash[:8]  # Return first 8 characters
            
            return "00000000"  # No migrations applied yet
        except Exception:
            # Table might not exist if no migrations run yet
            return "00000000"
    
    def _get_native_extension(self) -> str:
        """
        Get appropriate file extension for native backup based on backend.
        
        Returns:
            File extension including the dot (e.g., ".dump", ".backup")
        """
        backend_type = type(self.sql_gen).__name__.lower()
        
        if "sqlite" in backend_type:
            return ".backup"  # SQLite VACUUM INTO
        elif "postgres" in backend_type:
            return ".dump"    # PostgreSQL pg_dump
        elif "mysql" in backend_type:
            return ".sql"     # MySQL mysqldump
        else:
            return ".backup"  # Default fallback


async def export_table_to_csv(db, table_name: str, output_file: str):
    """
    Export a single table to CSV file.
    
    Args:
        db: Database connection
        table_name: Name of the table to export
        output_file: Path to output CSV file
    
    Example:
        await export_table_to_csv(db, "snapshots", "./backups/snapshots.csv")
    """
    entities = await db.find_entities(table_name, include_deleted=True)
    
    if not entities:
        print(f"No data in {table_name}")
        return
    
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=entities[0].keys())
        writer.writeheader()
        writer.writerows(entities)
    
    print(f"✓ Exported {len(entities)} rows from {table_name} to {output_file}")


async def import_table_from_csv(db, table_name: str, csv_file: str, batch_size: int = 100):
    """
    Import entities from CSV file into a table.
    
    Args:
        db: Database connection
        table_name: Name of the table to import into
        csv_file: Path to CSV file
        batch_size: Number of rows to import per batch
    
    Example:
        await import_table_from_csv(db, "snapshots", "./backups/snapshots.csv")
    """
    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        count = 0
        batch = []
        
        for row in reader:
            batch.append(dict(row))
            count += 1
            
            # Save in batches
            if len(batch) >= batch_size:
                await db.save_entities(table_name, batch)
                batch = []
        
        # Save remaining
        if batch:
            await db.save_entities(table_name, batch)
        
        print(f"✓ Imported {count} rows into {table_name}")


async def restore_native_backup(db, backup_file: str):
    """
    Restore from a native backup file.
    
    Args:
        db: Database connection (must be same backend as backup)
        backup_file: Path to native backup file
    
    Note:
        For SQLite, this copies the backup file.
        For Postgres/MySQL, use native tools (pg_restore, mysql).
    """
    backend_type = type(db.sql_generator).__name__
    
    if "Sqlite" in backend_type:
        # SQLite: Copy backup file to database location
        # This requires knowing the database file path
        raise NotImplementedError(
            "SQLite restore: Close connection and copy backup file to database location"
        )
    
    elif "Postgres" in backend_type:
        raise NotImplementedError(
            "PostgreSQL restore requires pg_restore. "
            "Run: pg_restore -d dbname backup.dump"
        )
    
    elif "Mysql" in backend_type:
        raise NotImplementedError(
            "MySQL restore requires mysql client. "
            "Run: mysql dbname < backup.sql"
        )
