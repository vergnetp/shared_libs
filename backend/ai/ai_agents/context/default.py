"""Default context builder and provider."""
from __future__ import annotations

import json
from typing import Any, Optional
from datetime import datetime

from .base import ContextBuilder, ContextProvider
from ..memory import MemoryStrategy


def deep_merge(base: dict, updates: dict) -> dict:
    """
    Deep merge updates into base dict.
    
    - Dicts are recursively merged
    - Lists are replaced (not appended)
    - None values remove keys
    """
    result = base.copy()
    
    for key, value in updates.items():
        if value is None:
            # Remove key
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            # Recursive merge
            result[key] = deep_merge(result[key], value)
        else:
            # Replace
            result[key] = value
    
    return result


class DefaultContextProvider(ContextProvider):
    """
    Default context provider using simple JSON storage.
    
    Used for Tier 2 (schema-defined) and Tier 3 (auto) context.
    Stores context in a user_context table with a single JSONB column.
    
    Args:
        conn: Database connection (must support find_entities, save_entity, etc.)
              Can be None if conn_factory is provided.
        schema: Optional schema dict describing what to remember (Tier 2)
                If None, agent decides what to remember (Tier 3)
        conn_factory: Optional async context manager that yields a connection.
                      Used for short-lived connections (e.g., WebSocket mode).
    
    Example (Tier 2 - schema defined):
        provider = DefaultContextProvider(
            conn=db,
            schema={
                "name": "User's name",
                "properties": "List of properties with name, address, type",
            }
        )
    
    Example (Tier 3 - auto):
        provider = DefaultContextProvider(conn=db)
    
    Example (WebSocket mode with factory):
        provider = DefaultContextProvider(
            conn=None,
            conn_factory=db_manager,  # async context manager
            schema={"name": "User's name"}
        )
    """
    
    def __init__(
        self, 
        conn: Any = None, 
        schema: Optional[dict] = None,
        conn_factory: Any = None,
    ):
        self.conn = conn
        self.schema = schema
        self.conn_factory = conn_factory
        self._cache: dict[str, dict] = {}  # Cache for pre-loaded contexts
        
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")
    
    async def _get_conn(self):
        """Get a connection - either direct or from factory."""
        if self.conn is not None:
            return self.conn
        return None  # Caller must use conn_factory as context manager
    
    async def load(self, user_id: str, agent_id: Optional[str] = None) -> dict:
        """Load context for a user."""
        try:
            print(f"[DEBUG DefaultContextProvider.load] Loading context for user_id={user_id}")
            
            # Check cache first
            if user_id in self._cache:
                print(f"[DEBUG DefaultContextProvider.load] Using cached context")
                return self._cache[user_id]
            
            # Use factory if no direct connection
            if self.conn is None and self.conn_factory:
                async with self.conn_factory as conn:
                    return await self._load_with_conn(conn, user_id)
            else:
                return await self._load_with_conn(self.conn, user_id)
                
        except Exception as e:
            print(f"[DEBUG DefaultContextProvider.load] Error: {e}")
            return {}
    
    async def _load_with_conn(self, conn: Any, user_id: str) -> dict:
        """Load context using a specific connection."""
        results = await conn.find_entities(
            "user_context",
            where_clause="user_id = ?",
            params=(user_id,),
            limit=1,
        )
        
        print(f"[DEBUG DefaultContextProvider.load] Found {len(results) if results else 0} records")
        
        if results:
            context = results[0].get("context", {})
            # Parse if stored as string
            if isinstance(context, str):
                context = json.loads(context) if context else {}
            print(f"[DEBUG DefaultContextProvider.load] Returning context: {context}")
            return context
        
        return {}
    
    async def update(
        self,
        user_id: str,
        updates: dict,
        reason: str,
        agent_id: Optional[str] = None,
    ) -> dict:
        """Update context for a user using deep merge."""
        import logging
        logger = logging.getLogger(__name__)
        
        print(f"[DEBUG DefaultContextProvider.update] user_id={user_id}, updates={updates}")
        
        # Use factory if no direct connection
        if self.conn is None and self.conn_factory:
            async with self.conn_factory as conn:
                return await self._update_with_conn(conn, user_id, updates, reason, agent_id)
        else:
            return await self._update_with_conn(self.conn, user_id, updates, reason, agent_id)
    
    async def _update_with_conn(
        self,
        conn: Any,
        user_id: str,
        updates: dict,
        reason: str,
        agent_id: Optional[str] = None,
    ) -> dict:
        """Update context using a specific connection."""
        now = datetime.utcnow()
        
        # Load existing (use same connection)
        results = await conn.find_entities(
            "user_context",
            where_clause="user_id = ?",
            params=(user_id,),
            limit=1,
        )
        current = {}
        if results:
            context = results[0].get("context", {})
            if isinstance(context, str):
                current = json.loads(context) if context else {}
            else:
                current = context
        
        print(f"[DEBUG DefaultContextProvider.update] Current context: {current}")
        
        # Deep merge
        merged = deep_merge(current, updates)
        print(f"[DEBUG DefaultContextProvider.update] Merged context: {merged}")
        
        try:
            print(f"[DEBUG DefaultContextProvider.update] Existing records: {len(results) if results else 0}")
            
            if results:
                # Update existing by saving with same id
                existing = results[0]
                existing["context"] = json.dumps(merged)
                existing["updated_at"] = now.isoformat()
                existing["last_reason"] = reason
                print(f"[DEBUG DefaultContextProvider.update] Updating existing record {existing['id']}")
                await conn.save_entity("user_context", existing)
            else:
                # Create new
                import uuid
                new_id = str(uuid.uuid4())
                print(f"[DEBUG DefaultContextProvider.update] Creating new record {new_id}")
                await conn.save_entity(
                    "user_context",
                    {
                        "id": new_id,
                        "user_id": user_id,
                        "context": json.dumps(merged),
                        "schema": json.dumps(self.schema) if self.schema else None,
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                        "last_reason": reason,
                    }
                )
            
            # Explicitly commit - @auto_transaction may skip commit if already in transaction
            try:
                print(f"[DEBUG DefaultContextProvider.update] in_transaction={await conn.in_transaction()}")
                await conn.commit_transaction()
                print(f"[DEBUG DefaultContextProvider.update] Committed successfully")
            except Exception as commit_err:
                print(f"[DEBUG DefaultContextProvider.update] Commit error (may be ok): {commit_err}")
            
            print(f"[DEBUG DefaultContextProvider.update] Successfully saved context for {user_id}")
            return merged
            
        except Exception as e:
            print(f"[DEBUG DefaultContextProvider.update] ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    async def delete(self, user_id: str, agent_id: Optional[str] = None) -> bool:
        """Delete all context for a user."""
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


class InMemoryContextProvider(ContextProvider):
    """
    In-memory context provider for testing and simple usage.
    
    Context is stored in a dict and lost when the process ends.
    """
    
    def __init__(self, schema: Optional[dict] = None):
        self.schema = schema
        self._storage: dict[str, dict] = {}
    
    async def load(self, user_id: str, agent_id: Optional[str] = None) -> dict:
        """Load context for a user."""
        return self._storage.get(user_id, {}).copy()
    
    async def update(
        self,
        user_id: str,
        updates: dict,
        reason: str,
        agent_id: Optional[str] = None,
    ) -> dict:
        """Update context for a user."""
        current = self._storage.get(user_id, {})
        merged = deep_merge(current, updates)
        self._storage[user_id] = merged
        return merged
    
    async def delete(self, user_id: str, agent_id: Optional[str] = None) -> bool:
        """Delete all context for a user."""
        if user_id in self._storage:
            del self._storage[user_id]
            return True
        return False


class DefaultContextBuilder(ContextBuilder):
    """
    Default context builder.
    
    Combines:
    - System prompt
    - User context (from ContextProvider)
    - RAG documents (if any)
    - Conversation history (via memory strategy)
    """
    
    def __init__(self, memory: MemoryStrategy):
        self.memory = memory
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        tools: list[dict] = None,
        documents: list[dict] = None,
        user_context: dict = None,
        user_context_formatted: str = None,
        # Extra params for summarize strategy
        thread_summary: str = None,
        tools_chars: int = 0,
        user_input_chars: int = 0,
        max_tokens: int = None,
        **kwargs,
    ) -> list[dict]:
        # Build system prompt with context and documents
        full_system = self._build_system_prompt(
            system_prompt, 
            documents, 
            user_context,
            user_context_formatted,
        )
        
        # Apply memory strategy
        context = await self.memory.build(
            messages,
            system_prompt=full_system,
            # Pass through extra params for summarize strategy
            thread_summary=thread_summary,
            tools_chars=tools_chars,
            user_input_chars=user_input_chars,
            max_tokens=max_tokens,
            **kwargs,
        )
        
        return context
    
    def _build_system_prompt(
        self,
        base_prompt: str,
        documents: list[dict] = None,
        user_context: dict = None,
        user_context_formatted: str = None,
    ) -> str:
        parts = []
        
        if base_prompt:
            parts.append(base_prompt)
        
        # Add user context
        if user_context_formatted:
            parts.append(f"\n\n{user_context_formatted}")
        elif user_context:
            parts.append("\n\n## User Context")
            parts.append(self._format_context(user_context))
        
        if documents:
            parts.append("\n\n## Relevant Documents\n")
            for doc in documents:
                title = doc.get("title", "Document")
                content = doc.get("content", "")
                parts.append(f"### {title}\n{content}\n")
        
        return "\n".join(parts) if parts else None
    
    def _format_context(self, context: dict, indent: int = 0) -> str:
        """Format context dict as readable text."""
        lines = []
        prefix = "  " * indent
        
        for key, value in context.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}- {key}:")
                lines.append(self._format_context(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{prefix}- {key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(self._format_context(item, indent + 1))
                    else:
                        lines.append(f"{prefix}  - {item}")
            else:
                lines.append(f"{prefix}- {key}: {value}")
        
        return "\n".join(lines)