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
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        
        where_clause = " AND ".join(conditions) if conditions else None
        
        return await db.find_entities(
            self.table,
            where_clause=where_clause,
            params=tuple(params) if params else None,
            limit=limit,
            offset=skip,
            include_deleted=include_deleted if self.soft_delete else True,
        )
    
    async def get(self, db: Any, id: str, include_deleted: bool = False) -> Optional[dict]:
        """Get entity by ID."""
        return await db.get_entity(self.table, id, include_deleted=include_deleted if self.soft_delete else True)
    
    async def create(self, db: Any, data: Any) -> dict:
        """Create new entity."""
        now = datetime.now(timezone.utc).isoformat()
        entity_id = str(uuid.uuid4())
        
        values = data.model_dump(exclude_unset=True)
        values["id"] = entity_id
        values["created_at"] = now
        values["updated_at"] = now
        
        result = await db.save_entity(self.table, values)
        return result
    
    async def update(self, db: Any, id: str, data: Any) -> Optional[dict]:
        """Update entity."""
        # Get existing entity
        existing = await self.get(db, id)
        if not existing:
            return None
        
        values = data.model_dump(exclude_unset=True)
        if not values:
            return existing
        
        # Merge with existing
        updated = {**existing, **values}
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        result = await db.save_entity(self.table, updated)
        return result
    
    async def delete(self, db: Any, id: str) -> bool:
        """Delete entity (soft delete if configured)."""
        permanent = not self.soft_delete
        return await db.delete_entity(self.table, id, permanent=permanent)
