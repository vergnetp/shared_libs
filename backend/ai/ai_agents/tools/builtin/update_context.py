"""Update context tool - allows agents to remember information about users.

Thread-safe: Uses locks to prevent race conditions when multiple agents
update the same user's context concurrently.
"""

from typing import Any, Optional
from ..base import Tool, ToolDefinition


# Global reference to context provider - set by Agent when tools are configured
_context_provider = None
_current_user_id = None
_current_agent_id = None

# Per-agent context storage for parallel safety
# Maps agent instance id -> (provider, user_id, agent_id)
_agent_contexts: dict[int, tuple[Any, str, Optional[str]]] = {}


def set_context_tool_provider(
    provider: Any, 
    user_id: str, 
    agent_id: Optional[str] = None,
    instance_id: int = None,
):
    """
    Configure the update_context tool with a provider.
    
    Called by Agent before each chat to set up the tool.
    
    Args:
        provider: Context provider instance
        user_id: Current user ID
        agent_id: Current agent ID
        instance_id: Agent instance id() for parallel safety
    """
    global _context_provider, _current_user_id, _current_agent_id
    
    # Store both globally (for single-agent compat) and per-instance (for parallel)
    _context_provider = provider
    _current_user_id = user_id
    _current_agent_id = agent_id
    
    if instance_id is not None:
        _agent_contexts[instance_id] = (provider, user_id, agent_id)


def get_context_for_instance(instance_id: int = None) -> tuple[Any, str, Optional[str]]:
    """Get context provider info, preferring instance-specific if available."""
    if instance_id is not None and instance_id in _agent_contexts:
        return _agent_contexts[instance_id]
    return (_context_provider, _current_user_id, _current_agent_id)


def clear_context_tool_provider(instance_id: int = None):
    """Clear the context provider (for testing)."""
    global _context_provider, _current_user_id, _current_agent_id
    _context_provider = None
    _current_user_id = None
    _current_agent_id = None
    
    if instance_id is not None and instance_id in _agent_contexts:
        del _agent_contexts[instance_id]


class UpdateContextTool(Tool):
    """
    Tool for agents to update persistent user context.
    
    Thread-safe: Uses locking to prevent race conditions when multiple
    agents update the same user's context concurrently.
    
    The agent calls this tool when it learns important information
    about the user that should be remembered across conversations.
    
    Updates are deep-merged with existing context:
    - Dicts are recursively merged
    - Lists are replaced
    - Setting a value to null removes it
    
    Example tool call:
        {
            "name": "update_context",
            "arguments": {
                "updates": {
                    "name": "Phil",
                    "goals": ["Run a marathon", "Improve 5K time"],
                    "injuries": {"knee": "recovering"}
                },
                "reason": "User shared their running goals and injury status"
            }
        }
    """
    
    name = "update_context"
    description = (
        "Save information about this user. "
        "IMPORTANT: You MUST wrap all data inside an 'updates' object. "
        "CORRECT: {\"updates\": {\"name\": \"Phil\", \"age\": 47}, \"reason\": \"User shared info\"} "
        "WRONG: {\"name\": \"Phil\", \"reason\": \"...\"} - this will fail! "
        "Always put the data you want to save inside 'updates'."
    )
    
    def __init__(self, instance_id: int = None):
        """
        Args:
            instance_id: Agent instance id() for parallel safety
        """
        self._instance_id = instance_id
    
    async def execute(self, updates: dict = None, reason: str = "User information update", **kwargs) -> str:
        """
        Update user context (thread-safe).
        
        Args:
            updates: Dict of updates to merge into context
            reason: Brief explanation of why this update is being made
            **kwargs: Additional fields (for LLMs that don't wrap in 'updates')
            
        Returns:
            Confirmation message
        """
        # Import here to avoid circular imports
        from ...concurrency import user_context_lock
        
        # Get context for this instance (or global fallback)
        context_provider, current_user_id, current_agent_id = get_context_for_instance(
            self._instance_id
        )
        
        print(f"[DEBUG UpdateContextTool] execute called with updates={updates}, reason={reason}, kwargs={kwargs}")
        print(f"[DEBUG UpdateContextTool] provider={context_provider}, user_id={current_user_id}, instance={self._instance_id}")
        
        # Handle LLMs that put data directly instead of in 'updates'
        # e.g., {"reason": "...", "running_level": "beginner"} instead of {"updates": {"running_level": "beginner"}, "reason": "..."}
        if updates is None and kwargs:
            updates = {k: v for k, v in kwargs.items() if k != "reason"}
            print(f"[DEBUG UpdateContextTool] Extracted updates from kwargs: {updates}")
        
        # Handle missing updates
        if updates is None:
            print("[DEBUG UpdateContextTool] ERROR: updates is None")
            return "Error: No data provided to save. Please provide an 'updates' object with the data to remember."
        
        if not isinstance(updates, dict):
            print(f"[DEBUG UpdateContextTool] ERROR: updates is not a dict, got {type(updates)}")
            return f"Error: 'updates' must be an object, got {type(updates).__name__}"
        
        if not updates:
            print("[DEBUG UpdateContextTool] ERROR: updates is empty")
            return "Error: 'updates' object is empty. Please provide data to remember."
        
        if context_provider is None:
            print("[DEBUG UpdateContextTool] ERROR: Context provider not configured")
            return "Error: Context provider not configured"
        
        if current_user_id is None:
            print("[DEBUG UpdateContextTool] ERROR: User ID not set")
            return "Error: User ID not set"
        
        try:
            # Lock on user_id + agent_id to prevent concurrent updates
            # This is critical for parallel agent execution
            async with user_context_lock(current_user_id, current_agent_id):
                print(f"[DEBUG UpdateContextTool] Acquired lock, calling provider.update...")
                updated = await context_provider.update(
                    user_id=current_user_id,
                    updates=updates,
                    reason=reason,
                    agent_id=current_agent_id,
                )
            
            print(f"[DEBUG UpdateContextTool] Update successful, result={updated}")
            
            # Return brief confirmation
            keys = list(updates.keys())
            if len(keys) <= 3:
                return f"Updated: {', '.join(keys)}"
            else:
                return f"Updated {len(keys)} fields"
                
        except Exception as e:
            print(f"[DEBUG UpdateContextTool] ERROR: {e}")
            import traceback
            traceback.print_exc()
            return f"Error updating context: {str(e)}"
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": (
                            "REQUIRED object containing all data to save. "
                            "Example: {\"updates\": {\"running_level\": \"beginner\", \"age\": 47}, \"reason\": \"...\"}"
                        ),
                        "additionalProperties": True,
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation why this is being saved",
                    },
                },
                "required": ["updates", "reason"],
            },
        )
