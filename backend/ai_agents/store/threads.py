"""Thread storage - pure CRUD."""

from typing import Optional, Any


class ThreadStore:
    """Thread CRUD operations."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(self, agent_id: str, title: str = None, config: dict = None) -> dict:
        """Create a new thread."""
        return await self.conn.save_entity("threads", {
            "agent_id": agent_id,
            "title": title,
            "config": config or {},
        })
    
    async def get(self, thread_id: str) -> Optional[dict]:
        """Get thread by ID."""
        return await self.conn.get_entity("threads", thread_id)
    
    async def update(self, thread_id: str, **fields) -> dict:
        """Update thread fields."""
        thread = await self.conn.get_entity("threads", thread_id)
        if not thread:
            return None
        
        for k, v in fields.items():
            thread[k] = v
        
        return await self.conn.save_entity("threads", thread)
    
    async def delete(self, thread_id: str) -> bool:
        """Delete thread (soft delete)."""
        return await self.conn.delete_entity("threads", thread_id)
    
    async def list_by_agent(self, agent_id: str, limit: int = 50) -> list[dict]:
        """List threads for an agent."""
        return await self.conn.find_entities(
            "threads",
            where_clause="[agent_id] = ?",
            params=(agent_id,),
            order_by="created_at DESC",
            limit=limit,
        )
    
    async def list_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        """
        List threads accessible by user.
        
        Note: Caller should filter by auth permissions.
        This just returns threads where user has any role.
        """
        # This would typically join with auth_role_assignments
        # For now, return all and let caller filter
        return await self.conn.find_entities(
            "threads",
            order_by="updated_at DESC",
            limit=limit,
        )
