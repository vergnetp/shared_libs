"""Context provider and builder base classes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class ContextProvider(ABC):
    """
    Interface for loading and updating user context.
    
    Three tiers of usage:
    
    Tier 1 - Custom Provider (full control):
        Implement this interface to connect to your own database.
        
        class HostomaticContextProvider(ContextProvider):
            async def load(self, user_id, agent_id):
                # Fetch from your Postgres/Firebase/etc
                ...
            async def update(self, user_id, agent_id, updates, reason):
                # Write to your database
                ...
    
    Tier 2 - Schema-Defined:
        Use DefaultContextProvider with a schema dict.
        Schema guides the LLM on what to remember.
        
        agent = Agent(
            role="Property assistant",
            context_schema={
                "name": "User's name",
                "properties": "List of properties with name, address, type",
            }
        )
    
    Tier 3 - Auto (zero config):
        Use DefaultContextProvider with no schema.
        Agent decides what's worth remembering based on its role.
        
        agent = Agent(role="Running coach")
    """
    
    @abstractmethod
    async def load(self, user_id: str, agent_id: Optional[str] = None) -> dict:
        """
        Load context for a user.
        
        Args:
            user_id: User identifier
            agent_id: Optional agent identifier (for agent-specific context)
            
        Returns:
            Context dict to inject into system prompt
        """
        ...
    
    @abstractmethod
    async def update(
        self,
        user_id: str,
        updates: dict,
        reason: str,
        agent_id: Optional[str] = None,
    ) -> dict:
        """
        Update context for a user.
        
        Args:
            user_id: User identifier
            updates: Dict of updates (deep merged with existing)
            reason: Why this update is being made (for audit)
            agent_id: Optional agent identifier
            
        Returns:
            Updated context dict
        """
        ...
    
    async def delete(self, user_id: str, agent_id: Optional[str] = None) -> bool:
        """
        Delete all context for a user.
        
        Args:
            user_id: User identifier
            agent_id: Optional agent identifier
            
        Returns:
            True if deleted, False if not found
        """
        # Default implementation - subclasses can override
        return False


class ContextBuilder(ABC):
    """Builds context for LLM from various sources."""
    
    @abstractmethod
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        tools: list[dict] = None,
        documents: list[dict] = None,
        **kwargs,
    ) -> list[dict]:
        """
        Build context for LLM.
        
        Args:
            messages: Conversation messages
            system_prompt: System prompt
            tools: Tool definitions
            documents: RAG documents to include
            **kwargs: Additional context sources
            
        Returns:
            Messages ready for LLM
        """
        ...
