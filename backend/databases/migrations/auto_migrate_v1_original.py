"""
Auto-migration system for database schema evolution.

Compares entity schemas in code with actual database schema,
generates portable SQL migrations, and applies them automatically.
"""

from dataclasses import fields
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from ..entity.decorators import ENTITY_SCHEMAS


class AutoMigrator:
    """
    Automatic database migration system.
    
    Detects schema changes by comparing entity classes with the database,
    generates portable SQL migrations in [bracket] syntax, and applies them.
    Saves all migrations to disk for audit/replay.
    """
    
    def __init__(self, db, audit_dir: str = "./migrations_audit"):
        """
        Initialize the auto-migrator.
        
        Args:
            db: Database connection with sql_generator
            audit_dir: Directory to save migration files
        """
        self.db = db
        self.sql_gen = db.sql_generator
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
    
    async def auto_migrate(self):
        """
        Auto-detect and apply schema changes.
        
        This is the main entry point. Call this on application startup
        to automatically apply any pending schema migrations.
        """
        # Ensure migrations tracking table exists
        await self._ensure_migrations_table()
        
        # Compute hash of current schema definitions
        code_hash = self._compute_schema_hash()
        
        # Check if we've already migrated to this schema
        if await self._is_schema_applied(code_hash):
            return  # Already up to date
        
        # Detect changes between code and database
        changes = await self._detect_changes()
        
        if not changes:
            # Mark current schema as applied even if no changes
            await self._record_migration(code_hash, [])
            return
        
        # Generate migration ID and SQL operations
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        migration_id = f"{timestamp}_{code_hash[:8]}"
        operations = self._generate_sql(changes)
        
        # Save to audit file (portable [bracket] syntax)
        self._save_audit(migration_id, changes, operations)
        
        # Apply migration
        await self._apply_migration(operations)
        
        # Record as applied
        await self._record_migration(code_hash, operations)
        
        print(f"✓ Applied migration {migration_id}")
    
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
        """
        Compute SHA256 hash of all entity schemas.
        
        This creates a fingerprint of the current schema state.
        Any change to entities will produce a different hash.
        """
        schema_repr = {}
        
        for table_name, entity_class in sorted(ENTITY_SCHEMAS.items()):
            schema_repr[table_name] = self._serialize_entity_schema(entity_class)
        
        schema_json = json.dumps(schema_repr, sort_keys=True)
        return hashlib.sha256(schema_json.encode()).hexdigest()
    
    def _serialize_entity_schema(self, entity_class) -> dict:
        """Convert entity class to a comparable dictionary"""
        field_defs = {}
        
        for f in fields(entity_class):
            field_defs[f.name] = {
                "type": str(f.type),
                "default": str(f.default) if f.default is not None else None,
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
        
        Returns list of change dictionaries with type, table, and details.
        """
        changes = []
        
        for table_name, entity_class in ENTITY_SCHEMAS.items():
            # Check if table exists in database
            exists = await self.db._table_exists(table_name)
            
            if not exists:
                # New table - needs to be created
                changes.append({
                    "type": "create_table",
                    "table": table_name,
                    "entity": entity_class,
                })
            else:
                # Existing table - check for new columns
                code_fields = self._get_entity_fields(entity_class)
                db_fields = await self._get_db_columns(table_name)
                
                for field_name, field_info in code_fields.items():
                    if field_name not in db_fields:
                        changes.append({
                            "type": "add_column",
                            "table": table_name,
                            "field": field_name,
                            "field_info": field_info,
                        })
        
        return changes
    
    def _get_entity_fields(self, entity_class) -> Dict[str, Dict]:
        """
        Extract field definitions from entity dataclass.
        
        Returns dict mapping field names to their metadata.
        """
        result = {}
        
        for f in fields(entity_class):
            meta = f.metadata or {}
            
            result[f.name] = {
                "type": self._python_to_sql_type(f.type),
                "default": f.default if f.default is not None else None,
                "nullable": meta.get("nullable", True),
                "index": meta.get("index", False),
                "unique": meta.get("unique", False),
                "foreign_key": meta.get("foreign_key"),
                "check": meta.get("check"),
            }
        
        return result
    
    def _python_to_sql_type(self, py_type) -> str:
        """Convert Python type hint to SQL type"""
        type_str = str(py_type)
        
        # Handle Optional[X] -> X
        if "Optional" in type_str or "Union" in type_str:
            if hasattr(py_type, "__args__"):
                py_type = py_type.__args__[0]
        
        # Type mapping
        type_map = {
            str: "TEXT",
            int: "INTEGER",
            float: "REAL",
            bool: "INTEGER",
            "str": "TEXT",
            "int": "INTEGER",
            "float": "REAL",
            "bool": "INTEGER",
        }
        
        return type_map.get(py_type, "TEXT")
    
    async def _get_db_columns(self, table_name: str) -> set:
        """Get existing column names from database table"""
        sql, params = self.sql_gen.get_list_columns_sql(table_name)
        result = await self.db.execute(sql, params)
        
        if not result:
            return set()
        
        # SQLite returns (cid, name, type, ...) - name is at index 1
        # Other DBs return just column name at index 0
        if len(result[0]) > 1 and isinstance(result[0][0], int):
            return {row[1] for row in result}
        return {row[0] for row in result}
    
    def _generate_sql(self, changes: List[Dict]) -> List[Tuple[str, tuple, str]]:
        """
        Generate SQL operations from detected changes.
        
        Returns list of (sql, params, description) tuples.
        SQL is in portable [bracket] syntax.
        """
        operations = []
        
        for change in changes:
            if change["type"] == "create_table":
                operations.extend(self._generate_create_table_sql(change))
            elif change["type"] == "add_column":
                operations.extend(self._generate_add_column_sql(change))
        
        return operations
    
    def _generate_create_table_sql(self, change: Dict) -> List[Tuple]:
        """Generate CREATE TABLE and related SQL operations"""
        entity_class = change["entity"]
        table_name = change["table"]
        field_dict = self._get_entity_fields(entity_class)
        
        operations = []
        
        # Build column definitions
        columns = [("id", "TEXT PRIMARY KEY")]
        indexes = []
        
        for field_name, field_info in field_dict.items():
            col_type = field_info['type']
            
            # Add constraints to column definition
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
            if field_info.get("check"):
                col_type += f" CHECK ({field_info['check']})"
            
            columns.append((field_name, col_type))
            
            # Collect fields that need indexes
            if field_info.get("index"):
                indexes.append(field_name)
        
        # Add base entity columns
        columns.extend([
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("created_by", "TEXT"),
            ("updated_by", "TEXT"),
        ])
        
        # 1. CREATE TABLE (using sql_generator)
        create_sql = self.sql_gen.get_create_table_sql(table_name, columns)
        operations.append((create_sql, (), f"Create table {table_name}"))
        
        # 2. CREATE INDEXES
        for idx_field in indexes:
            idx_sql = self._get_create_index_sql(table_name, idx_field)
            operations.append((idx_sql, (), f"Create index on {table_name}.{idx_field}"))
        
        # 3. CREATE META TABLE
        meta_sql = self.sql_gen.get_create_meta_table_sql(table_name)
        operations.append((meta_sql, (), f"Create meta table for {table_name}"))
        
        # 4. POPULATE META TABLE
        for field_name, field_info in field_dict.items():
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
    
    def _generate_add_column_sql(self, change: Dict) -> List[Tuple]:
        """Generate ALTER TABLE ADD COLUMN operations"""
        table_name = change["table"]
        field_name = change["field"]
        field_info = change["field_info"]
        
        operations = []
        
        # Build column definition
        col_sql = self.sql_gen.get_add_column_sql(table_name, field_name)
        
        # Add DEFAULT if specified
        if field_info.get("default") is not None:
            default = field_info["default"]
            if isinstance(default, str):
                col_sql += f" DEFAULT '{default}'"
            elif isinstance(default, bool):
                col_sql += f" DEFAULT {1 if default else 0}"
            else:
                col_sql += f" DEFAULT {default}"
        
        # 1. Add to main table
        operations.append((col_sql, (), f"Add column {field_name} to {table_name}"))
        
        # 2. Add to history table
        history_sql = self.sql_gen.get_add_column_sql(f"{table_name}_history", field_name)
        if field_info.get("default") is not None:
            default = field_info["default"]
            if isinstance(default, str):
                history_sql += f" DEFAULT '{default}'"
            elif isinstance(default, bool):
                history_sql += f" DEFAULT {1 if default else 0}"
            else:
                history_sql += f" DEFAULT {default}"
        
        operations.append((history_sql, (), f"Add column {field_name} to {table_name}_history"))
        
        # 3. Update meta table
        meta_sql = self.sql_gen.get_meta_upsert_sql(table_name)
        operations.append((
            meta_sql,
            (field_name, field_info['type']),
            f"Add {field_name} to meta"
        ))
        
        # 4. Create index if needed
        if field_info.get("index"):
            idx_sql = self._get_create_index_sql(table_name, field_name)
            operations.append((idx_sql, (), f"Create index on {table_name}.{field_name}"))
        
        return operations
    
    def _get_create_index_sql(self, table_name: str, field_name: str) -> str:
        """Generate CREATE INDEX SQL in portable [bracket] syntax"""
        index_name = f"idx_{table_name}_{field_name}"
        sql = f"CREATE INDEX [{index_name}] ON [{table_name}]([{field_name}])"
        return sql
    
    def _save_audit(self, migration_id: str, changes: List[Dict], operations: List[Tuple]):
        """
        Save migration to audit file in portable [bracket] syntax.
        
        This creates both .sql and .json files for the migration.
        """
        # Save SQL file (portable format)
        audit_file = self.audit_dir / f"{migration_id}.sql"
        
        with open(audit_file, 'w') as f:
            f.write(f"-- Migration: {migration_id}\n")
            f.write(f"-- Backend-agnostic (uses [bracket] syntax)\n")
            f.write(f"-- Generated: {datetime.now().isoformat()}\n")
            f.write(f"-- Changes: {len(changes)}\n\n")
            
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
                "changes": [
                    {k: str(v) if k == 'entity' else v for k, v in change.items()}
                    for change in changes
                ],
            }, f, indent=2, default=str)
    
    async def _apply_migration(self, operations: List[Tuple]):
        """Execute migration SQL operations"""
        for sql, params, description in operations:
            # Convert portable [bracket] syntax to native SQL
            native_sql, native_params = self.sql_gen.convert_query_to_native(sql, params)
            await self.db.execute(native_sql, native_params)
    
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
