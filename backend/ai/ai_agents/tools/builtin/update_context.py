"""Update context tool - allows agents to remember information about users."""

from typing import Any, Optional
from ..base import Tool, ToolDefinition


# Global reference to context provider - set by Agent when tools are configured
_context_provider = None
_current_user_id = None
_current_agent_id = None


def set_context_tool_provider(
    provider: Any, 
    user_id: str, 
    agent_id: Optional[str] = None
):
    """
    Configure the update_context tool with a provider.
    
    Called by Agent before each chat to set up the tool.
    """
    global _context_provider, _current_user_id, _current_agent_id
    _context_provider = provider
    _current_user_id = user_id
    _current_agent_id = agent_id


def clear_context_tool_provider():
    """Clear the context provider (for testing)."""
    global _context_provider, _current_user_id, _current_agent_id
    _context_provider = None
    _current_user_id = None
    _current_agent_id = None


class UpdateContextTool(Tool):
    """
    Tool for agents to update persistent user context.
    
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
    
    async def execute(self, updates: dict = None, reason: str = "User information update", **kwargs) -> str:
        """
        Update user context.
        
        Args:
            updates: Dict of updates to merge into context
            reason: Brief explanation of why this update is being made
            **kwargs: Additional fields (for LLMs that don't wrap in 'updates')
            
        Returns:
            Confirmation message
        """
        global _context_provider, _current_user_id, _current_agent_id
        
        print(f"[DEBUG UpdateContextTool] execute called with updates={updates}, reason={reason}, kwargs={kwargs}")
        print(f"[DEBUG UpdateContextTool] provider={_context_provider}, user_id={_current_user_id}")
        
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
        
        if _context_provider is None:
            print("[DEBUG UpdateContextTool] ERROR: Context provider not configured")
            return "Error: Context provider not configured"
        
        if _current_user_id is None:
            print("[DEBUG UpdateContextTool] ERROR: User ID not set")
            return "Error: User ID not set"
        
        try:
            print(f"[DEBUG UpdateContextTool] Calling provider.update...")
            updated = await _context_provider.update(
                user_id=_current_user_id,
                updates=updates,
                reason=reason,
                agent_id=_current_agent_id,
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
