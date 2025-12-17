"""Content filtering guardrails."""

from ..core import GuardrailError


class ContentGuardrail:
    """
    Content filtering.
    
    This is a placeholder - implement with your preferred moderation API:
    - OpenAI Moderation API
    - Perspective API
    - Custom classifier
    """
    
    def __init__(self, moderation_fn=None):
        """
        Args:
            moderation_fn: Async function(text: str) -> dict
                           Returns {"flagged": bool, "categories": dict}
        """
        self.moderation_fn = moderation_fn
    
    async def check(self, text: str) -> bool:
        """
        Check content for policy violations.
        
        Args:
            text: Text to check
            
        Returns:
            True if safe
            
        Raises:
            GuardrailError if content violates policy
        """
        if self.moderation_fn is None:
            # No moderation configured, allow all
            return True
        
        result = await self.moderation_fn(text)
        
        if result.get("flagged"):
            categories = result.get("categories", {})
            flagged_cats = [k for k, v in categories.items() if v]
            raise GuardrailError(
                "content",
                f"Content policy violation: {', '.join(flagged_cats)}"
            )
        
        return True
    
    async def is_safe(self, text: str) -> bool:
        """Check if content is safe (non-raising version)."""
        try:
            return await self.check(text)
        except GuardrailError:
            return False


class WordlistGuardrail:
    """Simple wordlist-based filtering."""
    
    def __init__(self, blocked_words: list[str] = None):
        self.blocked_words = set(w.lower() for w in (blocked_words or []))
    
    def check(self, text: str) -> bool:
        """Check for blocked words."""
        text_lower = text.lower()
        for word in self.blocked_words:
            if word in text_lower:
                raise GuardrailError("wordlist", f"Blocked content detected")
        return True
    
    def is_safe(self, text: str) -> bool:
        try:
            return self.check(text)
        except GuardrailError:
            return False
