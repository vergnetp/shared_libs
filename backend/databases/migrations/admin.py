"""
Database admin router — visibility + restore endpoints.

Auto-mounted by app_kernel at /admin/db.

Endpoints:
    GET  /migrations           — list applied migrations
    GET  /migrations/{hash}    — detail for one migration
    GET  /backups              — list available CSV restore points
    GET  /schema/orphans       — columns/tables in DB but not in code
    POST /backup               — trigger backup now
    POST /backfill             — manually run rename backfills
    POST /restore/history      — point-in-time restore from history tables
    POST /restore/table        — restore a single table from history
    POST /restore/csv          — restore a single table from CSV backup
"""

import logging
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Request models ──────────────────────────────────────────────────────────

class HistoryRestoreRequest(BaseModel):
    target_time: str  # ISO format: "2026-02-10T14:30:00Z"
    tables: Optional[List[str]] = None  # None = all tables
    confirm: bool = False

class TableRestoreRequest(BaseModel):
    table_name: str
    target_time: str
    confirm: bool = False

class CsvTableRestoreRequest(BaseModel):
    table_names: Optional[List[str]] = None  # None or empty = all tables in backup
    backup_name: str  # e.g. "csv_20260130_120000_a1b2c3d4"
    confirm: bool = False


# ── Router factory ──────────────────────────────────────────────────────────

def create_db_admin_router(
    get_current_user: Callable,
    data_dir: str = ".data",
    prefix: str = "/admin/db",
    tags: List[str] = None,
    is_admin: Optional[Callable] = None,
) -> APIRouter:
    """
    Create database admin router.
    
    Args:
        get_current_user: Auth dependency
        data_dir: Base data directory (contains backups/ and migrations_audit/)
        prefix: URL prefix
        tags: OpenAPI tags
        is_admin: Optional admin check function
    """
    router = APIRouter(prefix=prefix, tags=tags or ["admin:db"])
    
    backup_dir = str(Path(data_dir) / "backups")
    migrations_dir = str(Path(data_dir) / "migrations_audit")
    
    def _check_admin(user):
        if is_admin:
            return is_admin(user)
        return getattr(user, "role", None) == "admin"
    
    def _require_admin(user):
        if not _check_admin(user):
            raise HTTPException(403, "Admin access required")
    
    # ── Visibility ──────────────────────────────────────────────────────
    
    @router.get("/migrations", summary="List applied migrations")
    async def list_migrations(user=Depends(get_current_user)):
        """List all applied schema migrations."""
        _require_admin(user)
        from ..db.session import raw_db_context
        
        async with raw_db_context() as db:
            sql = "SELECT [id], [schema_hash], [applied_at], [operations] FROM [_schema_migrations] ORDER BY [id] DESC"
            native_sql, params = db.sql_generator.convert_query_to_native(sql, ())
            try:
                rows = await db.execute(native_sql, params)
            except Exception:
                return {"migrations": [], "note": "No migrations table yet"}
            
            migrations = []
            for row in rows:
                ops = row[3]
                op_count = 0
                if ops:
                    try:
                        import json
                        op_count = len(json.loads(ops))
                    except Exception:
                        pass
                
                migrations.append({
                    "id": row[0],
                    "schema_hash": row[1],
                    "applied_at": row[2],
                    "operation_count": op_count,
                })
            
            return {"migrations": migrations}
    
    @router.get("/migrations/{schema_hash}", summary="Migration detail")
    async def get_migration(schema_hash: str, user=Depends(get_current_user)):
        """Get operations for a specific migration."""
        _require_admin(user)
        from ..db.session import raw_db_context
        
        async with raw_db_context() as db:
            sql = "SELECT [operations], [applied_at] FROM [_schema_migrations] WHERE [schema_hash] = ?"
            native_sql, params = db.sql_generator.convert_query_to_native(sql, (schema_hash,))
            rows = await db.execute(native_sql, params)
            
            if not rows:
                raise HTTPException(404, f"Migration {schema_hash} not found")
            
            import json
            ops = []
            try:
                ops = json.loads(rows[0][0]) if rows[0][0] else []
            except Exception:
                pass
            
            # Also check for audit file on disk
            audit_file = None
            audit_path = Path(migrations_dir)
            for f in audit_path.glob(f"*_{schema_hash[:8]}.*"):
                audit_file = str(f)
                break
            
            return {
                "schema_hash": schema_hash,
                "applied_at": rows[0][1],
                "operations": ops,
                "audit_file": audit_file,
            }
    
    @router.get("/backups", summary="List restore points")
    async def list_backups(user=Depends(get_current_user)):
        """List available CSV backup restore points."""
        _require_admin(user)
        from ...databases.backup import list_restore_points
        
        points = list_restore_points(migrations_dir, backup_dir)
        return {
            "restore_points": [
                {
                    "timestamp": rp.timestamp,
                    "datetime": rp.datetime.isoformat(),
                    "schema_hash": rp.schema_hash,
                    "csv_dir": str(rp.csv_dir),
                    "has_migration": rp.migration_file is not None,
                }
                for rp in points
            ]
        }
    
    @router.get("/schema/orphans", summary="Find orphaned columns/tables")
    async def schema_orphans(user=Depends(get_current_user)):
        """
        Find columns and tables in the DB that aren't in any @entity.
        
        These are candidates for eventual cleanup — left behind by
        additive-only migrations (renamed columns, old tables).
        """
        _require_admin(user)
        from ..db.session import raw_db_context
        from ...databases.entity import ENTITY_SCHEMAS
        from dataclasses import fields
        
        async with raw_db_context() as db:
            tables = await db.list_tables()
            
            entity_tables = set(ENTITY_SCHEMAS.keys())
            system_tables = {t for t in tables if t.startswith('_')}
            history_tables = {t for t in tables if t.endswith('_history')}
            meta_tables = {t for t in tables if t.endswith('_meta')}
            known_tables = entity_tables | system_tables | history_tables | meta_tables
            
            # Tables in DB but not in code (excluding system/history/meta)
            orphan_tables = sorted(set(tables) - known_tables)
            
            # Columns in DB but not in entity definition
            orphan_columns = {}
            system_cols = {'id', 'created_at', 'updated_at', 'deleted_at', 'created_by', 'updated_by'}
            
            for table_name, entity_class in ENTITY_SCHEMAS.items():
                if table_name not in tables:
                    continue
                
                code_fields = {f.name.lower() for f in fields(entity_class)} | system_cols
                
                col_sql, col_params = db.sql_generator.get_list_columns_sql(table_name)
                db_result = await db.execute(col_sql, col_params)
                
                # SQLite returns (cid, name, type, ...), others return (name, ...)
                if db_result and len(db_result[0]) > 1 and isinstance(db_result[0][0], int):
                    db_cols = {row[1].lower() for row in db_result}
                else:
                    db_cols = {row[0].lower() for row in db_result}
                
                extra = sorted(db_cols - code_fields)
                if extra:
                    orphan_columns[table_name] = extra
            
            return {
                "orphan_tables": orphan_tables,
                "orphan_columns": orphan_columns,
                "summary": {
                    "orphan_table_count": len(orphan_tables),
                    "tables_with_orphan_columns": len(orphan_columns),
                    "total_orphan_columns": sum(len(v) for v in orphan_columns.values()),
                },
            }
    
    # ── Actions ─────────────────────────────────────────────────────────
    
    @router.post("/backup", summary="Trigger backup now")
    async def create_backup(user=Depends(get_current_user)):
        """Create a CSV + native backup right now."""
        _require_admin(user)
        from ..db.session import raw_db_context
        from ...databases.backup import BackupStrategy
        
        async with raw_db_context() as db:
            strategy = BackupStrategy(db)
            result = await strategy.backup_database(
                backup_dir,
                include_native=True,
                include_csv=True,
            )
            return {
                "status": "ok",
                "timestamp": result.get("timestamp"),
                "csv_dir": result.get("csv_dir"),
                "native": result.get("native"),
            }
    
    @router.post("/backfill", summary="Run rename backfills")
    async def run_backfill(user=Depends(get_current_user)):
        """
        Manually trigger rename backfills.
        
        Same as what runs on every startup — catches rows written by old
        containers after migration. Safe to run anytime (idempotent).
        """
        _require_admin(user)
        from ..db.session import raw_db_context
        from ...databases.migrations import AutoMigrator
        
        async with raw_db_context() as db:
            migrator = AutoMigrator(db, audit_dir=migrations_dir)
            await migrator._run_rename_backfills()
            return {"status": "ok", "message": "Backfills completed"}
    
    @router.post("/restore/history", summary="Point-in-time restore from history")
    async def restore_history(req: HistoryRestoreRequest, user=Depends(get_current_user)):
        """
        Restore tables to a point in time using history tables.
        
        No CSV backup needed — uses the built-in version history.
        History tables are never modified; you can roll forward again.
        
        Set confirm=true to execute. Without it, returns a dry-run preview.
        """
        _require_admin(user)
        from ..db.session import raw_db_context
        from ...databases.backup import restore_from_history
        
        if not req.confirm:
            # Dry run: show what would be affected
            from ...databases.entity import ENTITY_SCHEMAS
            
            preview = {}
            async with raw_db_context() as db:
                for table_name, entity_class in ENTITY_SCHEMAS.items():
                    if not getattr(entity_class, '__entity_history__', False):
                        continue
                    if req.tables and table_name not in req.tables:
                        continue
                    
                    history_table = f"{table_name}_history"
                    if not await db._table_exists(history_table):
                        preview[table_name] = {"status": "no history table"}
                        continue
                    
                    # Count rows that would be restored
                    count_sql = (
                        f"SELECT COUNT(DISTINCT [id]) FROM [{history_table}] "
                        f"WHERE [history_timestamp] <= ?"
                    )
                    native_sql, params = db.sql_generator.convert_query_to_native(count_sql, (req.target_time,))
                    result = await db.execute(native_sql, params)
                    history_count = result[0][0] if result else 0
                    
                    # Count current rows
                    current_sql = f"SELECT COUNT(*) FROM [{table_name}] WHERE [deleted_at] IS NULL"
                    native_sql, params = db.sql_generator.convert_query_to_native(current_sql, ())
                    result = await db.execute(native_sql, params)
                    current_count = result[0][0] if result else 0
                    
                    preview[table_name] = {
                        "rows_in_history": history_count,
                        "current_rows": current_count,
                        "rows_created_after": max(0, current_count - history_count),
                    }
            
            return {
                "dry_run": True,
                "target_time": req.target_time,
                "preview": preview,
                "message": "Set confirm=true to execute",
            }
        
        async with raw_db_context() as db:
            result = await restore_from_history(
                db, req.target_time, tables=req.tables, confirm=True
            )
            
            logger.warning(
                f"History restore executed by {getattr(user, 'id', 'unknown')}",
                extra={"target_time": req.target_time, "tables": req.tables, "result": result}
            )
            
            return {"status": "ok", "result": result}
    
    @router.post("/restore/table", summary="Restore single table from history")
    async def restore_table(req: TableRestoreRequest, user=Depends(get_current_user)):
        """Restore a single table to a point in time using history."""
        _require_admin(user)
        from ..db.session import raw_db_context
        from ...databases.backup import restore_single_table
        
        async with raw_db_context() as db:
            result = await restore_single_table(
                db, req.table_name, req.target_time, confirm=req.confirm
            )
            
            if req.confirm:
                logger.warning(
                    f"Table restore: {req.table_name} by {getattr(user, 'id', 'unknown')}",
                    extra={"table": req.table_name, "target_time": req.target_time, "result": result}
                )
            
            return result
    
    @router.post("/restore/csv", summary="Restore tables from CSV backup")
    async def restore_table_csv(req: CsvTableRestoreRequest, user=Depends(get_current_user)):
        """
        Restore one or more tables from a CSV backup.
        
        Upserts rows from the backup — existing rows get overwritten,
        rows created after the backup are kept (not deleted).
        """
        _require_admin(user)
        
        # Validate backup exists
        backup_path = Path(backup_dir) / req.backup_name
        if not backup_path.exists():
            raise HTTPException(404, f"Backup not found: {req.backup_name}")
        
        # Resolve tables: if omitted/empty, restore all CSVs in backup
        table_names = req.table_names or []
        if not table_names:
            table_names = sorted(f.stem for f in backup_path.glob("*.csv"))
            if not table_names:
                raise HTTPException(404, f"No CSV files in {req.backup_name}")
        
        missing = [t for t in table_names if not (backup_path / f"{t}.csv").exists()]
        if missing:
            available = sorted(f.stem for f in backup_path.glob("*.csv"))
            raise HTTPException(404, f"CSVs not found for: {missing}. Available: {available}")
        
        if not req.confirm:
            return {
                "dry_run": True,
                "tables": table_names,
                "backup": req.backup_name,
                "message": "Set confirm=true to execute.",
            }
        
        from ..db.session import raw_db_context
        from ...databases.backup import import_table_from_csv
        
        results = {}
        async with raw_db_context() as db:
            for table_name in table_names:
                csv_file = backup_path / f"{table_name}.csv"
                try:
                    await import_table_from_csv(db, table_name, str(csv_file))
                    results[table_name] = "ok"
                except Exception as e:
                    results[table_name] = f"error: {e}"
        
        logger.warning(
            f"CSV restore: {table_names} by {getattr(user, 'id', 'unknown')}",
            extra={"tables": table_names, "backup": req.backup_name, "results": results}
        )
        
        return {"status": "ok", "results": results, "source": req.backup_name}
    
    return router
