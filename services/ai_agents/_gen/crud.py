"""
Generic CRUD operations - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from typing import Any, Optional, TypeVar
from datetime import datetime, timezone
import uuid

T = TypeVar("T")


class EntityCRUD:
    """Generic CRUD for any entity."""
    
    def __init__(self, table: str, soft_delete: bool = False):
        self.table = table
        self.soft_delete = soft_delete
    
    async def list(
        self, 
        db: Any, 
        skip: int = 0, 
        limit: int = 100,
        workspace_id: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        """List entities with pagination."""
        conditions = []
        params = []
        
        if workspace_id:
            conditions.append("workspace_id = ?")
            params.append(workspace_id)
        
        if self.soft_delete and not include_deleted:
            conditions.append("deleted_at IS NULL")
        
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        query = f"""
            SELECT * FROM {self.table}
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, skip])
        
        return await db.fetch_all(query, params)
    
    async def get(self, db: Any, id: str) -> Optional[dict]:
        """Get entity by ID."""
        query = f"SELECT * FROM {self.table} WHERE id = ?"
        return await db.fetch_one(query, [id])
    
    async def create(self, db: Any, data: Any) -> dict:
        """Create new entity."""
        now = datetime.now(timezone.utc).isoformat()
        entity_id = str(uuid.uuid4())
        
        values = data.model_dump(exclude_unset=True)
        values["id"] = entity_id
        values["created_at"] = now
        values["updated_at"] = now
        
        columns = ", ".join(values.keys())
        placeholders = ", ".join(["?"] * len(values))
        query = f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders})"
        
        await db.execute(query, list(values.values()))
        return await self.get(db, entity_id)
    
    async def update(self, db: Any, id: str, data: Any) -> Optional[dict]:
        """Update entity."""
        values = data.model_dump(exclude_unset=True)
        if not values:
            return await self.get(db, id)
        
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        sets = ", ".join(f"{k} = ?" for k in values.keys())
        query = f"UPDATE {self.table} SET {sets} WHERE id = ?"
        
        await db.execute(query, list(values.values()) + [id])
        return await self.get(db, id)
    
    async def delete(self, db: Any, id: str) -> bool:
        """Delete entity (soft delete if configured)."""
        if self.soft_delete:
            now = datetime.now(timezone.utc).isoformat()
            query = f"UPDATE {self.table} SET deleted_at = ? WHERE id = ?"
            await db.execute(query, [now, id])
        else:
            query = f"DELETE FROM {self.table} WHERE id = ?"
            await db.execute(query, [id])
        return True
