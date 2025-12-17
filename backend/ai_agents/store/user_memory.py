"""User memory - persistent facts across threads."""

from typing import Optional, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class UserFact:
    """A fact about a user."""
    key: str
    value: str
    category: str = "general"
    confidence: float = 1.0
    source_thread_id: str = None
    created_at: datetime = None
    updated_at: datetime = None


class UserMemoryStore:
    """
    Store and retrieve persistent facts about users across threads.
    
    Unlike conversation memory (per-thread), user memory persists across
    all conversations and can be used to personalize responses.
    
    Example:
        memory = UserMemoryStore(conn)
        
        # Store facts
        await memory.set("user_123", "name", "Phil")
        await memory.set("user_123", "timezone", "Europe/London")
        await memory.set("user_123", "preference_style", "concise", category="preferences")
        
        # Retrieve
        name = await memory.get("user_123", "name")  # "Phil"
        all_facts = await memory.get_all("user_123")
        prefs = await memory.get_by_category("user_123", "preferences")
        
        # Use in agent context
        facts = await memory.get_all("user_123")
        context = memory.format_for_prompt(facts)
        # "User facts: name=Phil, timezone=Europe/London, ..."
    """
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def set(
        self,
        user_id: str,
        key: str,
        value: str,
        category: str = "general",
        confidence: float = 1.0,
        source_thread_id: str = None,
    ) -> dict:
        """
        Set a fact about a user. Updates if exists.
        
        Args:
            user_id: User ID
            key: Fact key (e.g., "name", "timezone", "preference_style")
            value: Fact value
            category: Category for grouping (e.g., "general", "preferences", "context")
            confidence: Confidence score 0-1 (for LLM-extracted facts)
            source_thread_id: Thread where fact was learned
        """
        # Check if exists
        existing = await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND [key] = ?",
            params=(user_id, key),
            limit=1,
        )
        
        if existing:
            # Update
            fact = existing[0]
            fact["value"] = value
            fact["category"] = category
            fact["confidence"] = confidence
            if source_thread_id:
                fact["source_thread_id"] = source_thread_id
            return await self.conn.save_entity("user_memory", fact)
        else:
            # Create
            return await self.conn.save_entity("user_memory", {
                "user_id": user_id,
                "key": key,
                "value": value,
                "category": category,
                "confidence": confidence,
                "source_thread_id": source_thread_id,
            })
    
    async def get(self, user_id: str, key: str) -> Optional[str]:
        """Get a single fact value."""
        results = await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND [key] = ?",
            params=(user_id, key),
            limit=1,
        )
        return results[0]["value"] if results else None
    
    async def get_fact(self, user_id: str, key: str) -> Optional[dict]:
        """Get full fact record."""
        results = await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND [key] = ?",
            params=(user_id, key),
            limit=1,
        )
        return results[0] if results else None
    
    async def get_all(self, user_id: str) -> list[dict]:
        """Get all facts for a user."""
        return await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ?",
            params=(user_id,),
            order_by="category, key",
        )
    
    async def get_by_category(self, user_id: str, category: str) -> list[dict]:
        """Get facts by category."""
        return await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND [category] = ?",
            params=(user_id, category),
            order_by="key",
        )
    
    async def delete(self, user_id: str, key: str) -> bool:
        """Delete a fact."""
        facts = await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND [key] = ?",
            params=(user_id, key),
            limit=1,
        )
        if facts:
            return await self.conn.delete_entity("user_memory", facts[0]["id"])
        return False
    
    async def delete_all(self, user_id: str) -> int:
        """Delete all facts for a user. Returns count deleted."""
        facts = await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ?",
            params=(user_id,),
        )
        for fact in facts:
            await self.conn.delete_entity("user_memory", fact["id"])
        return len(facts)
    
    async def search(self, user_id: str, query: str) -> list[dict]:
        """
        Search facts by key or value containing query.
        
        For more sophisticated search, use vector memory.
        """
        return await self.conn.find_entities(
            "user_memory",
            where_clause="[user_id] = ? AND ([key] LIKE ? OR [value] LIKE ?)",
            params=(user_id, f"%{query}%", f"%{query}%"),
        )
    
    def format_for_prompt(self, facts: list[dict], max_facts: int = 20) -> str:
        """
        Format facts for inclusion in system prompt.
        
        Args:
            facts: List of fact dicts
            max_facts: Max facts to include
            
        Returns:
            Formatted string for prompt
        """
        if not facts:
            return ""
        
        # Sort by confidence (highest first) and limit
        sorted_facts = sorted(facts, key=lambda f: f.get("confidence", 1.0), reverse=True)
        limited = sorted_facts[:max_facts]
        
        # Group by category
        by_category = {}
        for fact in limited:
            cat = fact.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(fact)
        
        # Format
        lines = ["# User Information"]
        for category, cat_facts in by_category.items():
            lines.append(f"\n## {category.title()}")
            for f in cat_facts:
                lines.append(f"- {f['key']}: {f['value']}")
        
        return "\n".join(lines)


class UserMemoryExtractor:
    """
    Extract facts from conversations using LLM.
    
    Run after conversations to populate user memory automatically.
    """
    
    EXTRACTION_PROMPT = """Analyze this conversation and extract factual information about the user.

Return JSON array of facts:
[
  {"key": "name", "value": "Phil", "category": "general", "confidence": 0.95},
  {"key": "timezone", "value": "Europe/London", "category": "context", "confidence": 0.8}
]

Categories: general, preferences, context, work, personal

Only extract clear facts, not opinions or temporary states.
Confidence 0-1 based on how certain the fact is.

Conversation:
{conversation}

Facts (JSON only):"""

    def __init__(self, provider, memory_store: UserMemoryStore):
        self.provider = provider
        self.memory = memory_store
    
    async def extract_and_save(
        self,
        user_id: str,
        messages: list[dict],
        thread_id: str = None,
        min_confidence: float = 0.7,
    ) -> list[dict]:
        """
        Extract facts from messages and save to user memory.
        
        Args:
            user_id: User ID
            messages: Conversation messages
            thread_id: Source thread ID
            min_confidence: Minimum confidence to save
            
        Returns:
            List of extracted facts
        """
        import json
        
        # Format conversation
        conversation = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in messages
        )
        
        prompt = self.EXTRACTION_PROMPT.format(conversation=conversation)
        
        response = await self.provider.run(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000,
        )
        
        # Parse JSON
        try:
            # Strip markdown code blocks if present
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            facts = json.loads(text)
        except json.JSONDecodeError:
            return []
        
        # Save facts
        saved = []
        for fact in facts:
            if fact.get("confidence", 0) >= min_confidence:
                await self.memory.set(
                    user_id=user_id,
                    key=fact["key"],
                    value=fact["value"],
                    category=fact.get("category", "general"),
                    confidence=fact.get("confidence", 1.0),
                    source_thread_id=thread_id,
                )
                saved.append(fact)
        
        return saved
