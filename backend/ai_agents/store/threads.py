"""Thread store with fork, archive, and size tracking."""

from typing import Optional, Any
from datetime import datetime


class ThreadStore:
    """
    CRUD operations for threads.
    
    Features:
    - Basic CRUD
    - Fork/branch threads
    - Archive threads
    - Size tracking (message count, bytes)
    """
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        agent_id: str,
        title: str = None,
        config: dict = None,
        user_id: str = None,
        metadata: dict = None,
    ) -> dict:
        """Create a new thread."""
        return await self.conn.save_entity("threads", {
            "agent_id": agent_id,
            "title": title,
            "config": config or {},
            "user_id": user_id,
            "metadata": metadata or {},
            "message_count": 0,
            "total_bytes": 0,
            "archived": False,
        })
    
    async def get(self, thread_id: str) -> Optional[dict]:
        """Get thread by ID."""
        return await self.conn.get_entity("threads", thread_id)
    
    async def update(self, thread_id: str, **fields) -> Optional[dict]:
        """Update thread fields."""
        thread = await self.conn.get_entity("threads", thread_id)
        if not thread:
            return None
        
        for k, v in fields.items():
            thread[k] = v
        
        return await self.conn.save_entity("threads", thread)
    
    async def delete(self, thread_id: str) -> bool:
        """Delete thread and all messages."""
        return await self.conn.delete_entity("threads", thread_id)
    
    async def list_by_agent(
        self,
        agent_id: str,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """List threads for an agent."""
        if include_archived:
            where = "[agent_id] = ?"
            params = (agent_id,)
        else:
            where = "[agent_id] = ? AND ([archived] IS NULL OR [archived] = ?)"
            params = (agent_id, False)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=where,
            params=params,
            order_by="created_at DESC",
            limit=limit,
        )
    
    async def list_by_user(
        self,
        user_id: str,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """List threads for a user."""
        if include_archived:
            where = "[user_id] = ?"
            params = (user_id,)
        else:
            where = "[user_id] = ? AND ([archived] IS NULL OR [archived] = ?)"
            params = (user_id, False)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=where,
            params=params,
            order_by="created_at DESC",
            limit=limit,
        )
    
    # ==================
    # Archive Operations
    # ==================
    
    async def archive(self, thread_id: str) -> Optional[dict]:
        """Archive a thread (soft delete)."""
        return await self.update(thread_id, archived=True)
    
    async def unarchive(self, thread_id: str) -> Optional[dict]:
        """Unarchive a thread."""
        return await self.update(thread_id, archived=False)
    
    async def list_archived(
        self,
        user_id: str = None,
        agent_id: str = None,
        limit: int = 50,
    ) -> list[dict]:
        """List archived threads."""
        conditions = ["[archived] = ?"]
        params = [True]
        
        if user_id:
            conditions.append("[user_id] = ?")
            params.append(user_id)
        
        if agent_id:
            conditions.append("[agent_id] = ?")
            params.append(agent_id)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )
    
    # ===============
    # Fork Operations
    # ===============
    
    async def fork(
        self,
        thread_id: str,
        title: str = None,
        up_to_message_id: str = None,
        user_id: str = None,
    ) -> dict:
        """
        Fork a thread, copying messages to a new thread.
        
        Args:
            thread_id: Source thread to fork
            title: Title for new thread (default: "Fork of {original}")
            up_to_message_id: Only copy messages up to this ID (inclusive)
            user_id: Owner of new thread
            
        Returns:
            New forked thread
        """
        # Get source thread
        source = await self.get(thread_id)
        if not source:
            raise ValueError(f"Thread not found: {thread_id}")
        
        # Create new thread
        fork_title = title or f"Fork of {source.get('title') or thread_id[:8]}"
        new_thread = await self.create(
            agent_id=source["agent_id"],
            title=fork_title,
            config=source.get("config", {}),
            user_id=user_id or source.get("user_id"),
            metadata={
                **source.get("metadata", {}),
                "forked_from": thread_id,
                "forked_at": datetime.utcnow().isoformat(),
            },
        )
        
        # Get messages to copy
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
            order_by="created_at ASC",
        )
        
        # If up_to_message_id specified, truncate
        if up_to_message_id:
            truncated = []
            for msg in messages:
                truncated.append(msg)
                if msg["id"] == up_to_message_id:
                    break
            messages = truncated
        
        # Copy messages
        total_bytes = 0
        for msg in messages:
            content = msg.get("content", "")
            total_bytes += len(content.encode("utf-8"))
            
            await self.conn.save_entity("messages", {
                "thread_id": new_thread["id"],
                "role": msg["role"],
                "content": content,
                "tool_calls": msg.get("tool_calls", []),
                "tool_call_id": msg.get("tool_call_id"),
                "attachments": msg.get("attachments", []),
                "metadata": {
                    **msg.get("metadata", {}),
                    "copied_from": msg["id"],
                },
            })
        
        # Update counts
        await self.update(
            new_thread["id"],
            message_count=len(messages),
            total_bytes=total_bytes,
        )
        
        return await self.get(new_thread["id"])
    
    async def branch(
        self,
        thread_id: str,
        from_message_id: str,
        title: str = None,
        user_id: str = None,
    ) -> dict:
        """
        Branch from a specific message (fork up to that point).
        
        Alias for fork(up_to_message_id=from_message_id).
        """
        return await self.fork(
            thread_id=thread_id,
            up_to_message_id=from_message_id,
            title=title or "Branch",
            user_id=user_id,
        )
    
    # ==============
    # Size Tracking
    # ==============
    
    async def update_size(self, thread_id: str) -> Optional[dict]:
        """
        Recalculate and update thread size metrics.
        
        Call after adding/removing messages.
        """
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
        
        total_bytes = sum(
            len(m.get("content", "").encode("utf-8"))
            for m in messages
        )
        
        return await self.update(
            thread_id,
            message_count=len(messages),
            total_bytes=total_bytes,
        )
    
    async def get_stats(self, thread_id: str) -> dict:
        """Get thread statistics."""
        thread = await self.get(thread_id)
        if not thread:
            return {}
        
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
        
        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        
        return {
            "thread_id": thread_id,
            "message_count": len(messages),
            "user_messages": len(user_msgs),
            "assistant_messages": len(assistant_msgs),
            "tool_messages": len(tool_msgs),
            "total_bytes": thread.get("total_bytes", 0),
            "archived": thread.get("archived", False),
            "created_at": thread.get("created_at"),
            "forked_from": thread.get("metadata", {}).get("forked_from"),
        }
    
    # ==============
    # Search/Filter
    # ==============
    
    async def search(
        self,
        query: str,
        user_id: str = None,
        agent_id: str = None,
        include_archived: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search threads by title.
        
        For message content search, use a separate search service.
        """
        conditions = ["[title] LIKE ?"]
        params = [f"%{query}%"]
        
        if user_id:
            conditions.append("[user_id] = ?")
            params.append(user_id)
        
        if agent_id:
            conditions.append("[agent_id] = ?")
            params.append(agent_id)
        
        if not include_archived:
            conditions.append("([archived] IS NULL OR [archived] = ?)")
            params.append(False)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )
