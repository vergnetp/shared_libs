"""Message storage - pure CRUD."""

from typing import Optional, Any


class MessageStore:
    """Message CRUD operations."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        thread_id: str,
        role: str,
        content: str,
        user_id: str = None,
        tool_calls: list[dict] = None,
        tool_call_id: str = None,
        attachments: list[str] = None,
        metadata: dict = None,
    ) -> dict:
        """Create a message."""
        return await self.conn.save_entity("messages", {
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls or [],
            "tool_call_id": tool_call_id,
            "attachments": attachments or [],
            "metadata": metadata or {},
        }, user_id=user_id)
    
    async def get(self, message_id: str) -> Optional[dict]:
        """Get message by ID."""
        return await self.conn.get_entity("messages", message_id)
    
    async def list(
        self,
        thread_id: str,
        limit: int = 100,
        before: str = None,
        after: str = None,
    ) -> list[dict]:
        """
        List messages in a thread.
        
        Args:
            thread_id: Thread ID
            limit: Max messages to return
            before: Get messages before this message ID
            after: Get messages after this message ID
        """
        where = "[thread_id] = ?"
        params = [thread_id]
        
        if before:
            where += " AND [created_at] < (SELECT [created_at] FROM [messages] WHERE [id] = ?)"
            params.append(before)
        
        if after:
            where += " AND [created_at] > (SELECT [created_at] FROM [messages] WHERE [id] = ?)"
            params.append(after)
        
        return await self.conn.find_entities(
            "messages",
            where_clause=where,
            params=tuple(params),
            order_by="created_at ASC",
            limit=limit,
        )
    
    async def count(self, thread_id: str) -> int:
        """Count messages in thread."""
        return await self.conn.count_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
    
    async def delete(self, message_id: str) -> bool:
        """Delete message (soft delete)."""
        return await self.conn.delete_entity("messages", message_id)
    
    async def update_metadata(self, message_id: str, metadata: dict) -> dict:
        """Update message metadata."""
        msg = await self.conn.get_entity("messages", message_id)
        if not msg:
            return None
        
        msg["metadata"] = {**msg.get("metadata", {}), **metadata}
        return await self.conn.save_entity("messages", msg)
