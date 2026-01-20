"""User context storage - simple JSON store for persistent context."""

import json
from typing import Any, Optional, List
from datetime import datetime


class UserContextStore:
    """
    Simple storage for user context.
    
    Stores context as a single JSON blob per user.
    Used by DefaultContextProvider for Tier 2 and Tier 3 context.
    
    Schema:
        CREATE TABLE user_context (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL UNIQUE,
            context TEXT,           -- JSON blob
            schema TEXT,            -- Optional schema definition (JSON)
            created_at TEXT,
            updated_at TEXT,
            last_reason TEXT        -- Last update reason (for audit)
        );
        CREATE INDEX idx_user_context_user ON user_context(user_id);
    
    Example:
        store = UserContextStore(conn)
        
        # Get context
        ctx = await store.get("user_123")
        # {"name": "Phil", "properties": [...]}
        
        # Update context
        await store.update("user_123", {"name": "Phil"}, reason="User introduced themselves")
        
        # Delete
        await store.delete("user_123")
    """
    
    def __init__(self, conn: Any):
        """
        Args:
            conn: Database connection with find_entities, save_entity, etc.
        """
        self.conn = conn
    
    async def get(self, user_id: str) -> Optional[dict]:
        """
        Get context for a user.
        
        Returns:
            Context dict or None if not found
        """
        print(f"[DEBUG UserContextStore.get] user_id={user_id}")
        
        results = await self.conn.find_entities(
            "user_context",
            where_clause="user_id = ?",
            params=(user_id,),
            limit=1,
        )
        
        print(f"[DEBUG UserContextStore.get] Found {len(results)} records")
        
        if not results:
            return None
        
        context = results[0].get("context")
        print(f"[DEBUG UserContextStore.get] Raw context: {context}")
        
        if context is None:
            return {}
        
        if isinstance(context, str):
            parsed = json.loads(context) if context else {}
            print(f"[DEBUG UserContextStore.get] Parsed context: {parsed}")
            return parsed
        
        return context
    
    async def set(
        self,
        user_id: str,
        context: dict,
        schema: Optional[dict] = None,
        reason: str = None,
    ) -> dict:
        """
        Set context for a user (replaces existing).
        
        Args:
            user_id: User identifier
            context: Context dict to store
            schema: Optional schema definition
            reason: Reason for update (for audit)
            
        Returns:
            Stored context
        """
        now = datetime.utcnow()
        
        # Check if exists
        results = await self.conn.find_entities(
            "user_context",
            where_clause="user_id = ?",
            params=(user_id,),
            limit=1,
        )
        
        if results:
            # Update by saving with same id
            existing = results[0]
            existing["context"] = json.dumps(context)
            if schema is not None:
                existing["schema"] = json.dumps(schema)
            existing["updated_at"] = now.isoformat()
            existing["last_reason"] = reason
            await self.conn.save_entity("user_context", existing)
        else:
            # Create
            import uuid
            await self.conn.save_entity(
                "user_context",
                {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "context": json.dumps(context),
                    "schema": json.dumps(schema) if schema else None,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_reason": reason,
                }
            )
        
        return context
    
    async def update(
        self,
        user_id: str,
        updates: dict,
        reason: str = None,
    ) -> dict:
        """
        Update context using deep merge.
        
        Args:
            user_id: User identifier
            updates: Updates to merge
            reason: Reason for update
            
        Returns:
            Merged context
        """
        current = await self.get(user_id) or {}
        merged = self._deep_merge(current, updates)
        return await self.set(user_id, merged, reason=reason)
    
    async def delete(self, user_id: str) -> bool:
        """
        Delete context for a user.
        
        Returns:
            True if deleted, False if not found
        """
        results = await self.conn.find_entities(
            "user_context",
            where_clause="user_id = ?",
            params=(user_id,),
            limit=1,
        )
        
        if results:
            await self.conn.delete_entity("user_context", results[0]["id"])
            return True
        
        return False
    
    async def list_users(self, limit: int = 100) -> List[str]:
        """List all user IDs with context."""
        results = await self.conn.find_entities(
            "user_context",
            limit=limit,
        )
        return [r["user_id"] for r in results]
    
    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge updates into base."""
        result = base.copy()
        
        for key, value in updates.items():
            if value is None:
                result.pop(key, None)
            elif isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
