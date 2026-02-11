"""
Enhanced auto-migration system with deletion support.

Handles:
- Column additions (as before)
- Column deletions (new)
- Table deletions (new)
"""

from dataclasses import fields, MISSING
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from ..entity.decorators import ENTITY_SCHEMAS


class AutoMigrator:
    """
    Automatic database migration system with deletion support.
    
    Detects schema changes including additions AND deletions.
    """
    
    def __init__(
        self,
        db,
        audit_dir: str = "./migrations_audit",
        allow_column_deletion: bool = False,
        allow_table_deletion: bool = False,
    ):
        """
        Initialize the auto-migrator.
        
        Args:
            db: Database connection with sql_generator
            audit_dir: Directory to save migration files
            allow_column_deletion: Whether to automatically drop removed columns
            allow_table_deletion: Whether to automatically drop removed tables
        """
        self.db = db
        self.sql_gen = db.sql_generator
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.allow_column_deletion = allow_column_deletion
        self.allow_table_deletion = allow_table_deletion
    
    async def auto_migrate(self, dry_run: bool = False):
        """
        Auto-detect and apply schema changes.
        
        Args:
            dry_run: If True, only show what would be done without applying
        """
        await self._ensure_migrations_table()
        
        code_hash = self._compute_schema_hash()
        
        if not await self._is_schema_applied(code_hash):
            changes = await self._detect_changes()
            
            if not changes:
                await self._record_migration(code_hash, [])
            else:
                # Check for dangerous operations
                has_deletions = any(
                    c["type"] in ["drop_column", "drop_table"]
                    for c in changes
                )
                has_renames = any(
                    c["type"] == "rename_table" or c.get("renamed_from")
                    for c in changes
                )
                
                if (has_deletions or has_renames) and not dry_run:
                    if has_deletions:
                        print("⚠️  WARNING: Migration includes deletions (data loss!)")
                    if has_renames:
                        print("📋 Migration includes renames (data will be copied, old tables/columns kept)")
                    for change in changes:
                        if change["type"] == "drop_column":
                            print(f"   - DROP COLUMN {change['table']}.{change['field']}")
                        elif change["type"] == "drop_table":
                            print(f"   - DROP TABLE {change['table']}")
                        elif change["type"] == "rename_table":
                            print(f"   - RENAME TABLE {change['old_table']} → {change['table']}")
                        elif change.get("renamed_from"):
                            print(f"   - RENAME COLUMN {change['table']}.{change['renamed_from']} → {change['field']}")
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                migration_id = f"{timestamp}_{code_hash[:8]}"
                operations = self._generate_sql(changes)
                
                self._save_audit(migration_id, changes, operations)
                
                if dry_run:
                    print(f"[DRY RUN] Would apply migration {migration_id}")
                    for sql, params, description in operations:
                        print(f"  - {description}")
                    return
                
                await self._apply_migration(operations)
                await self._record_migration(code_hash, operations)
                
                print(f"✓ Applied migration {migration_id}")
        
        # Always run rename backfills — catches rows written by old containers
        # during blue-green switchover. No-op when nothing to backfill.
        if not dry_run:
            await self._run_rename_backfills()
    
    async def _run_rename_backfills(self):
        """
        Backfill renamed columns/tables on every startup.
        
        During blue-green deployments, the old container may write rows AFTER
        the migration's initial UPDATE ran. This catches those stragglers.
        
        Only runs for fields with renamed_from set. Each UPDATE is idempotent
        (WHERE new IS NULL) so it's a no-op when fully backfilled.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        db_tables = await self._get_db_tables()
        
        for table_name, entity_class in ENTITY_SCHEMAS.items():
            if table_name not in db_tables:
                continue
            
            # Column renames — backfill both main and history tables
            for f in fields(entity_class):
                old_col = (f.metadata or {}).get("renamed_from")
                if not old_col:
                    continue
                
                db_cols = await self._get_db_columns(table_name)
                if old_col.lower() not in db_cols:
                    continue
                
                # Main table
                sql = f"UPDATE [{table_name}] SET [{f.name}] = [{old_col}] WHERE [{f.name}] IS NULL AND [{old_col}] IS NOT NULL"
                native_sql, params = self.sql_gen.convert_query_to_native(sql, ())
                try:
                    result = await self.db.execute(native_sql, params)
                    if result and isinstance(result, int) and result > 0:
                        logger.info(f"Backfilled {result} rows: {table_name}.{f.name} ← {old_col}")
                except Exception:
                    pass  # Column might not exist yet on first boot
                
                # History table
                history_sql = f"UPDATE [{table_name}_history] SET [{f.name}] = [{old_col}] WHERE [{f.name}] IS NULL AND [{old_col}] IS NOT NULL"
                native_sql, params = self.sql_gen.convert_query_to_native(history_sql, ())
                try:
                    result = await self.db.execute(native_sql, params)
                    if result and isinstance(result, int) and result > 0:
                        logger.info(f"Backfilled {result} history rows: {table_name}_history.{f.name} ← {old_col}")
                except Exception:
                    pass
            
            # Table renames
            old_table = getattr(entity_class, '__entity_renamed_from__', None)
            if not old_table or old_table not in db_tables:
                continue
            
            # Copy any new rows from old table that aren't in new table yet
            old_cols = await self._get_db_columns(old_table)
            new_cols = await self._get_db_columns(table_name)
            shared = sorted(old_cols & new_cols)
            if shared:
                cols_str = ", ".join(f"[{c}]" for c in shared)
                source_sql = (
                    f"SELECT {cols_str} FROM [{old_table}] "
                    f"WHERE [{old_table}].[id] NOT IN (SELECT [id] FROM [{table_name}])"
                )
                sql = self.sql_gen.get_insert_ignore_sql(table_name, shared, source_sql)
                native_sql, params = self.sql_gen.convert_query_to_native(sql, ())
                try:
                    result = await self.db.execute(native_sql, params)
                    if result and isinstance(result, int) and result > 0:
                        logger.info(f"Backfilled {result} rows from {old_table} → {table_name}")
                except Exception:
                    pass
    
    async def _ensure_migrations_table(self):
        """Create migrations tracking table if it doesn't exist"""
        sql = """
            CREATE TABLE IF NOT EXISTS [_schema_migrations] (
                [id] INTEGER PRIMARY KEY AUTOINCREMENT,
                [schema_hash] TEXT NOT NULL UNIQUE,
                [applied_at] TEXT NOT NULL,
                [operations] TEXT
            )
        """
        native_sql, params = self.sql_gen.convert_query_to_native(sql, ())
        await self.db.execute(native_sql, params)
    
    def _compute_schema_hash(self) -> str:
        """Compute SHA256 hash of all entity schemas"""
        schema_repr = {}
        
        for table_name, entity_class in sorted(ENTITY_SCHEMAS.items()):
            schema_repr[table_name] = self._serialize_entity_schema(entity_class)
        
        schema_json = json.dumps(schema_repr, sort_keys=True)
        return hashlib.sha256(schema_json.encode()).hexdigest()
    
    def _serialize_entity_schema(self, entity_class) -> dict:
        """Convert entity class to a comparable dictionary"""
        field_defs = {}
        
        for f in fields(entity_class):
            default_val = f.default if f.default is not MISSING else None
            field_defs[f.name] = {
                "type": str(f.type),
                "default": str(default_val) if default_val is not None else None,
                "metadata": dict(f.metadata or {}),
            }
        
        return field_defs
    
    async def _is_schema_applied(self, schema_hash: str) -> bool:
        """Check if this schema hash has already been applied"""
        sql = "SELECT 1 FROM [_schema_migrations] WHERE [schema_hash] = ?"
        native_sql, params = self.sql_gen.convert_query_to_native(sql, (schema_hash,))
        result = await self.db.execute(native_sql, params)
        return bool(result)
    
    async def _detect_changes(self) -> List[Dict]:
        """
        Detect differences between code schemas and database.
        
        Returns list of change dictionaries including additions AND deletions.
        """
        changes = []
        
        # Get all tables in database
        db_tables = await self._get_db_tables()
        code_tables = set(ENTITY_SCHEMAS.keys())
        
        # Detect new tables (or renamed tables)
        for table_name, entity_class in ENTITY_SCHEMAS.items():
            if table_name not in db_tables:
                old_table = getattr(entity_class, '__entity_renamed_from__', None)
                if old_table and old_table in db_tables:
                    # Table rename: old table exists in DB, new one doesn't
                    old_columns = await self._get_db_columns(old_table)
                    changes.append({
                        "type": "rename_table",
                        "table": table_name,
                        "old_table": old_table,
                        "old_columns": old_columns,
                        "entity": entity_class,
                    })
                else:
                    if old_table and old_table not in db_tables:
                        print(f"⚠️  renamed_from='{old_table}' on table '{table_name}' "
                              f"but '{old_table}' not found in DB — treating as new table")
                    changes.append({
                        "type": "create_table",
                        "table": table_name,
                        "entity": entity_class,
                    })
            else:
                # Table exists - check for column changes
                code_fields = self._get_entity_fields(entity_class)
                db_fields = await self._get_db_columns(table_name)
                
                # System columns - managed by CREATE TABLE, skip for add detection
                system_columns = {'id', 'created_at', 'updated_at', 'deleted_at', 
                                  'created_by', 'updated_by'}
                
                # Detect new columns (compare lowercase, skip system columns)
                for field_name, field_info in code_fields.items():
                    # Skip system columns - they're added by CREATE TABLE
                    if field_name.lower() in system_columns:
                        continue
                    if field_name.lower() not in db_fields:
                        change = {
                            "type": "add_column",
                            "table": table_name,
                            "field": field_name,
                            "field_info": field_info,
                        }
                        # Track rename source for data copy
                        if field_info.get("renamed_from"):
                            old_col = field_info["renamed_from"].lower()
                            if old_col in db_fields:
                                change["renamed_from"] = field_info["renamed_from"]
                            else:
                                print(f"⚠️  renamed_from='{field_info['renamed_from']}' on {table_name}.{field_name} "
                                      f"but column '{field_info['renamed_from']}' not found in DB — treating as new column")
                        changes.append(change)
                    else:
                        # Column exists — check for metadata changes (new index, new unique)
                        if field_info.get("index"):
                            changes.append({
                                "type": "add_index",
                                "table": table_name,
                                "field": field_name,
                            })
                
                # Detect removed columns
                if self.allow_column_deletion:
                    # Build lowercase code fields set for comparison
                    code_fields_lower = {k.lower() for k in code_fields.keys()}
                    
                    for field_name in db_fields:
                        # Skip system columns
                        if field_name in system_columns:
                            continue
                        
                        if field_name not in code_fields_lower:
                            changes.append({
                                "type": "drop_column",
                                "table": table_name,
                                "field": field_name,
                            })
        
        # Detect removed tables
        if self.allow_table_deletion:
            # Protect old tables that are sources of a rename (still needed for rollback)
            rename_sources = {
                getattr(ec, '__entity_renamed_from__', None)
                for ec in ENTITY_SCHEMAS.values()
            } - {None}
            
            for table_name in db_tables:
                # Skip system tables
                if table_name.startswith('_') or table_name.endswith('_meta') or \
                   table_name.endswith('_history'):
                    continue
                
                # Skip tables that are the source of a rename
                if table_name in rename_sources:
                    continue
                
                if table_name not in code_tables:
                    changes.append({
                        "type": "drop_table",
                        "table": table_name,
                    })
        
        return changes
    
    async def _get_db_tables(self) -> set:
        """Get all entity tables from database (excluding meta/history/system)"""
        tables_sql, params = self.sql_gen.get_list_tables_sql()
        result = await self.db.execute(tables_sql, params)
        
        all_tables = {row[0] for row in result}
        
        # Filter to entity tables only
        entity_tables = {
            t for t in all_tables
            if not t.endswith('_meta') 
            and not t.endswith('_history')
            and not t.startswith('_')
        }
        
        return entity_tables
    
    def _get_entity_fields(self, entity_class) -> Dict[str, Dict]:
        """Extract field definitions from entity dataclass"""
        result = {}
        
        for f in fields(entity_class):
            meta = f.metadata or {}
            
            result[f.name] = {
                "type": self._python_to_sql_type(f.type),
                "default": f.default if f.default is not None and f.default is not MISSING else None,
                "nullable": meta.get("nullable", True),
                "index": meta.get("index", False),
                "unique": meta.get("unique", False),
                "foreign_key": meta.get("foreign_key"),
                "check": meta.get("check"),
                "renamed_from": meta.get("renamed_from"),
            }
        
        return result
    
    def _python_to_sql_type(self, py_type) -> str:
        """Convert Python type hint to SQL type.
        
        Always returns TEXT — all values are serialized to strings by
        _serialize_entity before INSERT, so typed columns add no benefit
        and break on strict backends (Postgres rejects string→INTEGER
        in some edge cases).
        """
        return "TEXT"
    
    async def _get_db_columns(self, table_name: str) -> set:
        """Get existing column names from database table"""
        sql, params = self.sql_gen.get_list_columns_sql(table_name)
        result = await self.db.execute(sql, params)
        
        if not result:
            return set()
        
        # SQLite returns (cid, name, type, ...) - name is at index 1
        # Other DBs return just column name at index 0
        if len(result[0]) > 1 and isinstance(result[0][0], int):
            return {row[1].lower() for row in result}  # Normalize to lowercase
        return {row[0].lower() for row in result}  # Normalize to lowercase
    
    def _generate_sql(self, changes: List[Dict]) -> List[Tuple[str, tuple, str]]:
        """Generate SQL operations from detected changes"""
        operations = []
        
        for change in changes:
            if change["type"] == "create_table":
                operations.extend(self._generate_create_table_sql(change))
            elif change["type"] == "rename_table":
                operations.extend(self._generate_rename_table_sql(change))
            elif change["type"] == "add_column":
                operations.extend(self._generate_add_column_sql(change))
            elif change["type"] == "add_index":
                operations.extend(self._generate_add_index_sql(change))
            elif change["type"] == "drop_column":
                operations.extend(self._generate_drop_column_sql(change))
            elif change["type"] == "drop_table":
                operations.extend(self._generate_drop_table_sql(change))
        
        return operations
    
    def _build_col_type(self, field_info: Dict, for_history: bool = False) -> str:
        """
        Build full column type definition from field metadata.
        
        Args:
            field_info: Field metadata dict with type, unique, nullable, default, check
            for_history: If True, only include DEFAULT (history tables don't need constraints)
        
        Returns:
            Column type string, e.g. "TEXT NOT NULL DEFAULT 'x' CHECK ([status] IN (...))"
        """
        col_type = field_info['type']
        
        if not for_history:
            if field_info.get("unique"):
                col_type += " UNIQUE"
            if not field_info.get("nullable", True):
                col_type += " NOT NULL"
        
        if field_info.get("default") is not None:
            default = field_info["default"]
            if isinstance(default, str):
                col_type += f" DEFAULT '{default}'"
            elif isinstance(default, bool):
                col_type += f" DEFAULT {1 if default else 0}"
            else:
                col_type += f" DEFAULT {default}"
        
        if not for_history and field_info.get("check"):
            col_type += f" CHECK ({field_info['check']})"
        
        return col_type

    def _generate_create_table_sql(self, change: Dict) -> List[Tuple]:
        """Generate CREATE TABLE and related SQL operations"""
        entity_class = change["entity"]
        table_name = change["table"]
        field_dict = self._get_entity_fields(entity_class)
        
        operations = []
        
        # System columns - added automatically below, skip from entity fields
        system_columns = {'id', 'created_at', 'updated_at', 'deleted_at', 
                          'created_by', 'updated_by'}
        
        # Build column definitions
        columns = [("id", "TEXT PRIMARY KEY")]
        indexes = []
        
        for field_name, field_info in field_dict.items():
            # Skip system columns - added below
            if field_name.lower() in system_columns:
                continue
                
            col_type = self._build_col_type(field_info)
            
            columns.append((field_name, col_type))
            
            if field_info.get("index"):
                indexes.append(field_name)
        
        # Add base entity columns (system columns)
        columns.extend([
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("created_by", "TEXT"),
            ("updated_by", "TEXT"),
        ])
        
        # 1. CREATE TABLE
        create_sql = self.sql_gen.get_create_table_sql(table_name, columns)
        operations.append((create_sql, (), f"Create table {table_name}"))
        
        # 2. CREATE INDEXES
        for idx_field in indexes:
            idx_sql = self._get_create_index_sql(table_name, idx_field)
            operations.append((idx_sql, (), f"Create index on {table_name}.{idx_field}"))
        
        # 3. CREATE META TABLE
        meta_sql = self.sql_gen.get_create_meta_table_sql(table_name)
        operations.append((meta_sql, (), f"Create meta table for {table_name}"))
        
        # 4. POPULATE META TABLE (skip system columns)
        for field_name, field_info in field_dict.items():
            if field_name.lower() in system_columns:
                continue
            meta_insert = self.sql_gen.get_meta_upsert_sql(table_name)
            operations.append((
                meta_insert,
                (field_name, field_info['type']),
                f"Add {field_name} to meta"
            ))
        
        # 5. CREATE HISTORY TABLE
        history_columns = columns + [
            ("version", "INTEGER NOT NULL"),
            ("history_timestamp", "TEXT NOT NULL"),
            ("history_user_id", "TEXT"),
            ("history_comment", "TEXT"),
        ]
        history_sql = self.sql_gen.get_create_history_table_sql(table_name, history_columns)
        operations.append((history_sql, (), f"Create history table for {table_name}"))
        
        return operations
    
    def _generate_rename_table_sql(self, change: Dict) -> List[Tuple]:
        """
        Generate table rename operations: create new table, copy data, keep old.
        
        Strategy:
        1. Create new table (full schema from entity class)
        2. Copy rows from old table (only matching columns)
        3. Same for history table
        4. Old table is NOT dropped (rollback safe)
        """
        # Step 1: Create new table with full schema (reuse create_table logic)
        operations = self._generate_create_table_sql(change)
        
        old_table = change["old_table"]
        new_table = change["table"]
        entity_class = change["entity"]
        old_columns = change["old_columns"]  # Set of lowercase column names from DB
        
        # Step 2: Find columns that exist in BOTH old and new tables
        system_columns = {'id', 'created_at', 'updated_at', 'deleted_at',
                          'created_by', 'updated_by'}
        code_fields = self._get_entity_fields(entity_class)
        new_columns = {f.lower() for f in code_fields.keys()} | system_columns
        
        shared_cols = sorted(new_columns & old_columns)
        
        if shared_cols:
            source_sql = (
                f"SELECT {', '.join(f'[{c}]' for c in shared_cols)} FROM [{old_table}]"
            )
            copy_sql = self.sql_gen.get_insert_ignore_sql(new_table, shared_cols, source_sql)
            operations.append((copy_sql, (), f"Copy data from {old_table} to {new_table} ({len(shared_cols)} columns)"))
            
            # Step 3: Copy history table (shared cols + history-specific cols)
            history_extra = ["version", "history_timestamp", "history_user_id", "history_comment"]
            history_cols = shared_cols + history_extra
            
            history_source_sql = (
                f"SELECT {', '.join(f'[{c}]' for c in history_cols)} FROM [{old_table}_history]"
            )
            history_copy_sql = self.sql_gen.get_insert_ignore_sql(
                f"{new_table}_history", history_cols, history_source_sql
            )
            operations.append((history_copy_sql, (), f"Copy history from {old_table} to {new_table}"))
        
        return operations
    
    def _generate_add_index_sql(self, change: Dict) -> List[Tuple]:
        """
        Generate CREATE INDEX for an existing column.
        
        Uses IF NOT EXISTS — idempotent, safe to run even if index already present.
        """
        table_name = change["table"]
        field_name = change["field"]
        
        idx_sql = self._get_create_index_sql(table_name, field_name)
        return [(idx_sql, (), f"Create index on {table_name}.{field_name}")]
    
    def _generate_add_column_sql(self, change: Dict) -> List[Tuple]:
        """Generate ALTER TABLE ADD COLUMN operations"""
        table_name = change["table"]
        field_name = change["field"]
        field_info = change["field_info"]
        
        operations = []
        
        # Build full column type (UNIQUE, NOT NULL, DEFAULT, CHECK)
        col_type = self._build_col_type(field_info)
        col_sql = self.sql_gen.get_add_column_sql(table_name, field_name, col_type)
        operations.append((col_sql, (), f"Add column {field_name} to {table_name}"))
        
        # Add to history table (DEFAULT only — no constraints on history)
        history_col_type = self._build_col_type(field_info, for_history=True)
        history_sql = self.sql_gen.get_add_column_sql(f"{table_name}_history", field_name, history_col_type)
        operations.append((history_sql, (), f"Add column {field_name} to {table_name}_history"))
        
        # Update meta table
        meta_sql = self.sql_gen.get_meta_upsert_sql(table_name)
        operations.append((
            meta_sql,
            (field_name, field_info['type']),
            f"Add {field_name} to meta"
        ))
        
        # Create index if needed
        if field_info.get("index"):
            idx_sql = self._get_create_index_sql(table_name, field_name)
            operations.append((idx_sql, (), f"Create index on {table_name}.{field_name}"))
        
        # Copy data from old column if this is a rename
        if change.get("renamed_from"):
            old_col = change["renamed_from"]
            copy_sql = f"UPDATE [{table_name}] SET [{field_name}] = [{old_col}] WHERE [{field_name}] IS NULL"
            operations.append((copy_sql, (), f"Copy data from {old_col} to {field_name} (rename)"))
            
            # Copy in history table too
            history_copy_sql = f"UPDATE [{table_name}_history] SET [{field_name}] = [{old_col}] WHERE [{field_name}] IS NULL"
            operations.append((history_copy_sql, (), f"Copy data from {old_col} to {field_name} in history (rename)"))
        
        return operations
    
    def _generate_drop_column_sql(self, change: Dict) -> List[Tuple]:
        """
        Generate DROP COLUMN operations.
        
        Note: SQLite doesn't support DROP COLUMN directly until 3.35.0+
        For older versions, this requires table recreation.
        """
        table_name = change["table"]
        field_name = change["field"]
        
        operations = []
        
        # Drop column from main table
        drop_sql = self._get_drop_column_sql(table_name, field_name)
        operations.append((drop_sql, (), f"Drop column {field_name} from {table_name}"))
        
        # Drop column from history table
        history_drop_sql = self._get_drop_column_sql(f"{table_name}_history", field_name)
        operations.append((history_drop_sql, (), f"Drop column {field_name} from {table_name}_history"))
        
        # Remove from meta table
        meta_delete_sql = f"DELETE FROM [{table_name}_meta] WHERE [name] = ?"
        operations.append((meta_delete_sql, (field_name,), f"Remove {field_name} from meta"))
        
        return operations
    
    def _generate_drop_table_sql(self, change: Dict) -> List[Tuple]:
        """Generate DROP TABLE operations"""
        table_name = change["table"]
        
        operations = []
        
        # Drop main table
        operations.append((
            f"DROP TABLE IF EXISTS [{table_name}]",
            (),
            f"Drop table {table_name}"
        ))
        
        # Drop meta table
        operations.append((
            f"DROP TABLE IF EXISTS [{table_name}_meta]",
            (),
            f"Drop meta table {table_name}_meta"
        ))
        
        # Drop history table
        operations.append((
            f"DROP TABLE IF EXISTS [{table_name}_history]",
            (),
            f"Drop history table {table_name}_history"
        ))
        
        return operations
    
    def _get_create_index_sql(self, table_name: str, field_name: str) -> str:
        """Generate CREATE INDEX SQL in portable [bracket] syntax"""
        index_name = f"idx_{table_name}_{field_name}"
        sql = f"CREATE INDEX IF NOT EXISTS [{index_name}] ON [{table_name}]([{field_name}])"
        return sql
    
    def _get_drop_column_sql(self, table_name: str, field_name: str) -> str:
        """
        Generate DROP COLUMN SQL.
        
        Note: SQLite < 3.35.0 doesn't support this.
        The sql_generator will need to handle this per-backend.
        """
        # Most databases use this syntax
        sql = f"ALTER TABLE [{table_name}] DROP COLUMN [{field_name}]"
        return sql
    
    def _save_audit(self, migration_id: str, changes: List[Dict], operations: List[Tuple]):
        """Save migration to audit file in portable [bracket] syntax"""
        audit_file = self.audit_dir / f"{migration_id}.sql"
        
        with open(audit_file, 'w') as f:
            f.write(f"-- Migration: {migration_id}\n")
            f.write(f"-- Backend-agnostic (uses [bracket] syntax)\n")
            f.write(f"-- Generated: {datetime.now().isoformat()}\n")
            f.write(f"-- Changes: {len(changes)}\n")
            
            # Warn about deletions
            has_deletions = any(c["type"] in ["drop_column", "drop_table"] for c in changes)
            if has_deletions:
                f.write(f"-- ⚠️  WARNING: This migration includes DELETIONS (data loss!)\n")
            
            f.write("\n")
            
            for i, (sql, params, description) in enumerate(operations, 1):
                f.write(f"-- {description}\n")
                f.write(sql)
                if params:
                    f.write(f"  -- params: {params}")
                f.write(";\n\n")
        
        # Save JSON metadata
        meta_file = self.audit_dir / f"{migration_id}.json"
        
        with open(meta_file, 'w') as f:
            json.dump({
                "migration_id": migration_id,
                "timestamp": datetime.now().isoformat(),
                "backend": type(self.sql_gen).__name__,
                "has_deletions": has_deletions,
                "changes": [
                    {k: str(v) if k == 'entity' else v for k, v in change.items()}
                    for change in changes
                ],
            }, f, indent=2, default=str)
    
    async def _apply_migration(self, operations: List[Tuple]):
        """Execute migration SQL operations"""
        import logging
        logger = logging.getLogger(__name__)
        
        for sql, params, description in operations:
            native_sql, native_params = self.sql_gen.convert_query_to_native(sql, params)
            try:
                await self.db.execute(native_sql, native_params)
            except Exception as e:
                error_str = str(e).lower()
                
                # Idempotent errors - safe to skip
                if "already exists" in error_str:
                    continue  # Table, index, column already exists
                if "duplicate column" in error_str:
                    continue  
                if "duplicate key" in error_str:
                    continue  # Meta table entry already exists
                    
                # Table doesn't exist yet - might be order issue, log and continue
                if "no such table" in error_str or "doesn't exist" in error_str:
                    logger.warning(f"Migration skipped (table not ready): {description} - {e}")
                    continue
                    
                # Re-raise other errors
                raise
    
    async def _record_migration(self, schema_hash: str, operations: List[Tuple]):
        """Record migration as applied in tracking table"""
        sql = "INSERT INTO [_schema_migrations] ([schema_hash], [applied_at], [operations]) VALUES (?, ?, ?)"
        operations_json = json.dumps([
            {"sql": op[0], "params": op[1], "desc": op[2]}
            for op in operations
        ])
        
        native_sql, params = self.sql_gen.convert_query_to_native(
            sql,
            (schema_hash, datetime.now().isoformat(), operations_json)
        )
        await self.db.execute(native_sql, params)
