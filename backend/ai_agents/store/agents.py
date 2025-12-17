"""Agent storage - pure CRUD."""

from typing import Optional, Any


class AgentStore:
    """Agent definition CRUD."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        name: str,
        system_prompt: str,
        model: str = "claude-sonnet-4-20250514",
        provider: str = "anthropic",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[str] = None,
        memory_strategy: str = "last_n",
        memory_params: dict = None,
        metadata: dict = None,
    ) -> dict:
        """Create an agent definition."""
        return await self.conn.save_entity("agents", {
            "name": name,
            "system_prompt": system_prompt,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools or [],
            "memory_strategy": memory_strategy,
            "memory_params": memory_params or {"n": 20},
            "metadata": metadata or {},
        })
    
    async def get(self, agent_id: str) -> Optional[dict]:
        """Get agent by ID."""
        return await self.conn.get_entity("agents", agent_id)
    
    async def get_by_name(self, name: str) -> Optional[dict]:
        """Get agent by name."""
        results = await self.conn.find_entities(
            "agents",
            where_clause="[name] = ?",
            params=(name,),
            limit=1,
        )
        return results[0] if results else None
    
    async def update(self, agent_id: str, **fields) -> dict:
        """Update agent fields."""
        agent = await self.conn.get_entity("agents", agent_id)
        if not agent:
            return None
        
        for k, v in fields.items():
            agent[k] = v
        
        return await self.conn.save_entity("agents", agent)
    
    async def delete(self, agent_id: str) -> bool:
        """Delete agent (soft delete)."""
        return await self.conn.delete_entity("agents", agent_id)
    
    async def list(self, limit: int = 100) -> list[dict]:
        """List all agents."""
        return await self.conn.find_entities(
            "agents",
            order_by="name ASC",
            limit=limit,
        )
