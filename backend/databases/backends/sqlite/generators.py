from typing import Tuple, List, Any, Optional
from ...generators import SqlGenerator
from ...entity.generators import SqlEntityGenerator

class SqliteSqlGenerator(SqlGenerator, SqlEntityGenerator):
    """
    SQLite-specific SQL generator implementation.
    
    This class provides SQL generation tailored to SQLite's dialect and features.
    """
    
    def escape_identifier(self, identifier: str) -> str:
        """Escape a column or table name for SQLite."""
        return f"\"{identifier}\""
    
    def _convert_parameters(self, sql: str, params: Optional[Tuple] = None) -> Tuple[str, Any]:
        """Convert placeholders - SQLite already uses ? so just handle escaped ??"""
        new_sql = ''
        i = 0
        while i < len(sql):
            if i+1 < len(sql) and sql[i:i+2] == '??':
                new_sql += '?'  # Replace ?? with single ?
                i += 2          
            else:
                new_sql += sql[i]
                i += 1

        if not params:
            return new_sql, []  
              
        return new_sql, params

    def get_upsert_sql(self, entity_name: str, fields: List[str]) -> str:
        """Generate SQLite-specific upsert SQL for an entity."""
        fields_str = ', '.join([f"[{field}]" for field in fields])
        placeholders = ', '.join(['?'] * len(fields))
        
        return f"INSERT OR REPLACE INTO [{entity_name}] ({fields_str}) VALUES ({placeholders})"
    
    def get_create_table_sql(self, entity_name: str, columns: List[Tuple[str, str]]) -> str:
        """Generate SQLite-specific CREATE TABLE SQL."""
        column_defs = []
        for name, type_name in columns:
            if name == 'id':
                column_defs.append(f"[id] TEXT PRIMARY KEY")
            else:
                column_defs.append(f"[{name}] {type_name}")
        
        return f"""
            CREATE TABLE IF NOT EXISTS [{entity_name}] (
                {', '.join(column_defs)}
            )
        """
    
    def get_create_meta_table_sql(self, entity_name: str) -> str:
        """Generate SQLite-specific SQL for creating a metadata table."""
        return f"""
            CREATE TABLE IF NOT EXISTS [{entity_name}_meta] (
                [name] TEXT PRIMARY KEY,
                [type] TEXT
            )
        """
    
    def get_create_history_table_sql(self, entity_name: str, columns: List[Tuple[str, str]]) -> str:
        """Generate SQLite-specific history table SQL.
        
        History columns are TEXT (matching serialization) with DEFAULT values
        preserved from the main table. Constraints (UNIQUE, NOT NULL, CHECK)
        are stripped — history is append-only and doesn't enforce business rules.
        """
        # History-specific fields that we'll add - filter these from main columns
        history_fields = {'version', 'history_timestamp', 'history_user_id', 'history_comment'}
        
        # Ensure id is included
        has_id = any(name == 'id' for name, _ in columns)
        column_defs = []
        if not has_id:
            column_defs.append("[id] TEXT")
        
        # Add main table columns, preserving DEFAULT but stripping constraints
        for name, col_type in columns:
            if name not in history_fields:
                # Extract DEFAULT clause if present (e.g. "TEXT DEFAULT 'free'" → "DEFAULT 'free'")
                default_part = ""
                upper = col_type.upper()
                idx = upper.find("DEFAULT ")
                if idx != -1:
                    # Grab from DEFAULT to the next constraint keyword or end
                    rest = col_type[idx:]
                    # Stop at UNIQUE, NOT NULL, CHECK, or end
                    for keyword in [" UNIQUE", " NOT NULL", " CHECK"]:
                        kw_pos = rest.upper().find(keyword)
                        if kw_pos != -1:
                            rest = rest[:kw_pos]
                    default_part = f" {rest.strip()}"
                
                column_defs.append(f"[{name}] TEXT{default_part}")
        
        # Add history-specific columns
        column_defs.append("[version] INTEGER")
        column_defs.append("[history_timestamp] TEXT")
        column_defs.append("[history_user_id] TEXT")
        column_defs.append("[history_comment] TEXT")
        
        # SQLite's PRIMARY KEY syntax - no brackets around column names
        return f"""
            CREATE TABLE IF NOT EXISTS [{entity_name}_history] (
                {', '.join(column_defs)},
                PRIMARY KEY (id, version)
            )
        """
    
    def get_list_tables_sql(self) -> Tuple[str, tuple]:
        """Get SQL to list all tables in SQLite."""
        return (
            "SELECT name FROM sqlite_master WHERE type='table'",
            ()
        )
    
    def get_list_columns_sql(self, table_name: str) -> Tuple[str, tuple]:
        """Get SQL to list all columns in a SQLite table."""
        # Note: SQLite's PRAGMA statements don't support escaped identifiers in the same way
        # We need to handle the escaping differently for PRAGMA statements
        return (
            f"PRAGMA table_info({table_name})",
            ()
        )
    
    def get_meta_upsert_sql(self, entity_name: str) -> str:
        """Generate SQLite-specific upsert SQL for a metadata table."""
        return f"INSERT OR REPLACE INTO [{entity_name}_meta] VALUES (?, ?)"
    
    def get_insert_ignore_sql(self, target_table: str, columns: List[str], source_sql: str) -> str:
        """Generate SQLite INSERT OR IGNORE."""
        cols_str = ", ".join(f"[{c}]" for c in columns)
        return f"INSERT OR IGNORE INTO [{target_table}] ({cols_str}) {source_sql}"
    
    def get_add_column_sql(self, table_name: str, column_name: str, col_type: str = "TEXT") -> str:
        """Generate SQL to add a column to an existing SQLite table."""
        # SQLite doesn't support ADD COLUMN IF NOT EXISTS, so the caller must check
        return f"ALTER TABLE [{table_name}] ADD COLUMN [{column_name}] {col_type}"
    
    def get_check_table_exists_sql(self, table_name: str) -> Tuple[str, tuple]:
        """Generate SQL to check if a table exists in SQLite."""
        return (
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
    
    def get_check_column_exists_sql(self, table_name: str, column_name: str) -> Tuple[str, tuple]:
        """Generate SQL to check if a column exists in a SQLite table."""
        # SQLite requires checking the table_info PRAGMA result
        return (
            f"PRAGMA table_info({table_name})",
            ()
        )
        # Note: Caller will need to check if column_name is in the results
    
    def get_entity_by_id_sql(self, entity_name: str, include_deleted: bool = False) -> str:
        """Generate SQL to retrieve an entity by ID in SQLite."""
        query = f"SELECT * FROM [{entity_name}] WHERE [id] = ?"
        
        if not include_deleted:
            query += " AND [deleted_at] IS NULL"
            
        return query
    
    def get_entity_history_sql(self, entity_name: str, id: str) -> Tuple[str, tuple]:
        """Generate SQL to retrieve the history of an entity in SQLite."""
        return (
            f"SELECT * FROM [{entity_name}_history] WHERE [id] = ? ORDER BY [version] DESC",
            (id,)
        )
    
    def get_entity_version_sql(self, entity_name: str, id: str, version: int) -> Tuple[str, tuple]:
        """Generate SQL to retrieve a specific version of an entity in SQLite."""
        return (
            f"SELECT * FROM [{entity_name}_history] WHERE [id] = ? AND [version] = ?",
            (id, version)
        )
    
    def get_soft_delete_sql(self, entity_name: str) -> str:
        """Generate SQL for soft-deleting an entity in SQLite."""
        return f"UPDATE [{entity_name}] SET [deleted_at] = ?, [updated_at] = ?, [updated_by] = ? WHERE [id] = ?"
    
    def get_restore_entity_sql(self, entity_name: str) -> str:
        """Generate SQL for restoring a soft-deleted entity in SQLite."""
        return f"UPDATE [{entity_name}] SET [deleted_at] = NULL, [updated_at] = ?, [updated_by] = ? WHERE [id] = ?"
    
    def get_count_entities_sql(self, entity_name: str, where_clause: Optional[str] = None,
                              include_deleted: bool = False) -> str:
        """Generate SQL for counting entities in SQLite."""
        query = f"SELECT COUNT(*) FROM [{entity_name}]"
        conditions = []
        
        if not include_deleted:
            conditions.append("[deleted_at] IS NULL")
            
        if where_clause:
            conditions.append(where_clause)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        return query
    
    def get_query_builder_sql(self, entity_name: str, where_clause: Optional[str] = None,
                            order_by: Optional[str] = None, limit: Optional[int] = None,
                            offset: Optional[int] = None, include_deleted: bool = False) -> str:
        """Generate SQL for a flexible query in SQLite."""
        query = f"SELECT * FROM [{entity_name}]"
        conditions = []
        
        if not include_deleted:
            conditions.append("[deleted_at] IS NULL")
            
        if where_clause:
            conditions.append(where_clause)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        if order_by:
            query += f" ORDER BY {order_by}"
            
        if limit is not None:
            query += f" LIMIT {limit}"
            
        if offset is not None:
            query += f" OFFSET {offset}"
            
        return query
    
    def get_update_fields_sql(self, entity_name: str, fields: List[str]) -> str:
        """Generate SQL for updating specific fields of an entity in SQLite."""
        set_clause = ", ".join([f"[{field}] = ?" for field in fields])
        return f"UPDATE [{entity_name}] SET {set_clause}, [updated_at] = ?, [updated_by] = ? WHERE [id] = ?"
    
    def get_pragma_or_settings_sql(self) -> List[str]:
        """Get optimal SQLite settings using PRAGMAs."""
        return [
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA cache_size = -8000"  # Negative means kibibytes
        ]
    
    def get_next_sequence_value_sql(self, sequence_name: str) -> Optional[str]:
        """
        SQLite doesn't support native sequences like PostgreSQL.
        This is typically implemented using rowid or custom tables.
        """
        # For SQLite, we return None as there's no direct sequence support
        return None
