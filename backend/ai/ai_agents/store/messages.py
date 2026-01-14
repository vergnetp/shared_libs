"""Message storage - pure CRUD with optional thread safety."""
from __future__ import annotations

from typing import Optional, Any, List


class MessageStore:
    """
    Message CRUD operations.
    
    For multi-agent scenarios where agents share a thread,
    use ThreadSafeMessageStore wrapper.
    """
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        thread_id: str,
        role: str,
        content: str,
        user_id: str = None,
        tool_calls: List[dict] = None,
        tool_call_id: str = None,
        attachments: List[str] = None,
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
    ) -> List[dict]:
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
        
        messages = await self.conn.find_entities(
            "messages",
            where_clause=where,
            params=tuple(params),
            order_by="created_at ASC",
            limit=limit,
        )
        
        return [self._normalize_message(m) for m in (messages or [])]
    
    def _normalize_message(self, msg: dict) -> dict:
        """
        Normalize message for LLM API compatibility.
        
        - Deserialize JSON strings (metadata, attachments)
        - Remove tool_calls (stored for audit only, not conversation replay)
        """
        import json
        
        result = dict(msg)
        
        # Deserialize JSON fields
        for field in ("metadata", "attachments"):
            val = result.get(field)
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val) if val else None
                except (json.JSONDecodeError, TypeError):
                    result[field] = None
        
        # Remove tool_calls - it's audit-only (tool names), not for LLM replay
        # Including old-format tool_calls causes orphan issues with Anthropic
        result.pop("tool_calls", None)
        
        return result
    
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
        import json
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"update_metadata called: message_id={message_id}, metadata_keys={list(metadata.keys()) if metadata else None}")
        
        msg = await self.conn.get_entity("messages", message_id)
        if not msg:
            logger.warning(f"update_metadata: message not found: {message_id}")
            return None
        
        # Handle metadata as JSON string or dict
        existing = msg.get("metadata", {})
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except:
                existing = {}
        
        msg["metadata"] = {**existing, **metadata}
        result = await self.conn.save_entity("messages", msg)
        logger.info(f"update_metadata saved: message_id={message_id}, success={result is not None}")
        return result
    
    # Compatibility aliases for Agent class
    async def add(self, thread_id: str, role: str, content: str, **kwargs) -> dict:
        """Alias for create() - used by Agent class."""
        return await self.create(thread_id=thread_id, role=role, content=content, **kwargs)
    
    async def get_recent(self, thread_id: str, limit: int = 50) -> List[dict]:
        """Alias for list() - used by Agent class."""
        return await self.list(thread_id=thread_id, limit=limit)
    
    async def get_recent_by_chars(
        self,
        thread_id: str,
        max_chars: int = 32000,
        batch_size: int = 20,
    ) -> List[dict]:
        """
        Get recent messages up to a character limit.
        
        Fetches in batches from newest to oldest, stops when limit reached.
        Returns messages in chronological order (oldest first).
        
        Args:
            thread_id: Thread ID
            max_chars: Maximum total characters to return
            batch_size: Messages to fetch per batch
            
        Returns:
            Messages in chronological order, fitting within max_chars
        """
        result = []
        total_chars = 0
        offset = 0
        
        while True:
            # Fetch batch from newest
            batch = await self.conn.find_entities(
                "messages",
                where_clause="[thread_id] = ?",
                params=(thread_id,),
                order_by="created_at DESC",
                limit=batch_size,
                offset=offset,
            )
            
            if not batch:
                break
            
            for msg in batch:
                content = msg.get("content") or ""
                msg_chars = len(content)
                
                if total_chars + msg_chars > max_chars:
                    # Hit limit - return what we have (normalized)
                    result.reverse()  # Chronological order
                    return [self._normalize_message(m) for m in result]
                
                result.append(msg)
                total_chars += msg_chars
            
            offset += batch_size
            
            # Safety limit: don't fetch more than 500 messages
            if offset >= 500:
                break
        
        result.reverse()  # Chronological order
        return [self._normalize_message(m) for m in result]
    
    async def get_unsummarized(
        self,
        thread_id: str,
        after_msg_id: Optional[str] = None,
        keep_recent: int = 10,
    ) -> List[dict]:
        """
        Get messages that haven't been summarized yet, excluding recent ones.
        
        Args:
            thread_id: Thread ID
            after_msg_id: Get messages after this ID (exclusive)
            keep_recent: Number of recent messages to exclude (they stay in detail)
            
        Returns:
            Messages to summarize (excludes last `keep_recent` messages)
        """
        # Build query
        if after_msg_id:
            where = "[thread_id] = ? AND [created_at] > (SELECT [created_at] FROM [messages] WHERE [id] = ?)"
            params = (thread_id, after_msg_id)
        else:
            where = "[thread_id] = ?"
            params = (thread_id,)
        
        messages = await self.conn.find_entities(
            "messages",
            where_clause=where,
            params=params,
            order_by="created_at ASC",
        )
        
        # Exclude last N (keep_recent)
        if messages and len(messages) > keep_recent:
            return [self._normalize_message(m) for m in messages[:-keep_recent]]
        
        return []  # Not enough messages to summarize


class ThreadSafeMessageStore(MessageStore):
    """
    Thread-safe message store for multi-agent scenarios.
    
    Wraps MessageStore with per-thread locking to ensure
    message ordering is consistent when multiple agents
    write to the same thread.
    
    Usage:
        store = ThreadSafeMessageStore(conn)
        
        # All operations on same thread are serialized
        await store.add(thread_id="shared", ...)
        await store.add(thread_id="shared", ...)  # Waits for previous
    """
    
    def __init__(self, conn: Any, lock_timeout: float = 30.0):
        super().__init__(conn)
        self._lock_timeout = lock_timeout
    
    async def create(
        self,
        thread_id: str,
        role: str,
        content: str,
        **kwargs,
    ) -> dict:
        """Create a message (thread-safe)."""
        from ..concurrency import thread_lock
        
        async with thread_lock(thread_id, timeout=self._lock_timeout):
            return await super().create(thread_id, role, content, **kwargs)
    
    async def add(self, thread_id: str, role: str, content: str, **kwargs) -> dict:
        """Alias for create() (thread-safe)."""
        return await self.create(thread_id=thread_id, role=role, content=content, **kwargs)
    
    async def update_metadata(self, message_id: str, metadata: dict) -> dict:
        """Update message metadata (thread-safe via message lookup)."""
        from ..concurrency import get_lock
        
        # Get thread_id from message first
        msg = await self.conn.get_entity("messages", message_id)
        if not msg:
            return None
        
        thread_id = msg.get("thread_id", "unknown")
        
        async with get_lock("thread", thread_id, timeout=self._lock_timeout):
            return await super().update_metadata(message_id, metadata)
