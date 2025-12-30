"""
Capability enforcement for agent tool dispatch.

DESIGN:
- Capabilities are checked ONCE, before any side effect
- Check happens at tool dispatch time, not in individual tools
- Centralized mapping of tool → required capability

USAGE:
    enforcer = CapabilityEnforcer(agent_capabilities=["moderate_content"])
    
    # Before executing tool:
    enforcer.require_for_tool("publish_document")  # Raises if no capability
    
    # Or wrap tool execution:
    result = await enforcer.execute_tool(tool_name, tool_fn, args)
"""

from typing import List, Dict, Set, Callable, Any, Optional
from dataclasses import dataclass, field


class CapabilityError(Exception):
    """Raised when agent lacks required capability for an action."""
    
    def __init__(self, capability: str, tool_name: str = None, agent_id: str = None):
        self.capability = capability
        self.tool_name = tool_name
        self.agent_id = agent_id
        msg = f"Agent lacks capability '{capability}'"
        if tool_name:
            msg += f" required for tool '{tool_name}'"
        super().__init__(msg)


# =============================================================================
# Capability → Tool Mapping
# =============================================================================

# Tools that require specific capabilities
# If a tool is not listed, it requires no special capability
TOOL_CAPABILITIES: Dict[str, str] = {
    # Content moderation
    "publish_document": "publish_content",
    "publish_message": "publish_content",
    "approve_submission": "moderate_content",
    "reject_submission": "moderate_content",
    
    # Status changes
    "update_document_status": "update_status",
    "update_thread_status": "update_status",
    "archive_thread": "update_status",
    
    # Document versioning
    "create_document_version": "create_document_version",
    "rollback_document": "create_document_version",
    
    # User management (admin-level)
    "create_user": "manage_users",
    "delete_user": "manage_users",
    "update_user_role": "manage_users",
    
    # Workspace management
    "create_workspace": "manage_workspaces",
    "delete_workspace": "manage_workspaces",
    "add_workspace_member": "manage_workspace_members",
    "remove_workspace_member": "manage_workspace_members",
}

# Capabilities that grant access to multiple tools
CAPABILITY_GRANTS: Dict[str, Set[str]] = {
    "admin": set(TOOL_CAPABILITIES.values()),  # Admin can do everything
}


# =============================================================================
# Enforcer
# =============================================================================

@dataclass
class CapabilityEnforcer:
    """
    Enforces capability requirements for tool execution.
    
    Create one per agent, then use to gate tool calls.
    """
    capabilities: List[str] = field(default_factory=list)
    agent_id: Optional[str] = None
    
    # Allow custom capability mappings
    custom_tool_capabilities: Dict[str, str] = field(default_factory=dict)
    
    def __post_init__(self):
        # Expand capability grants
        self._expanded_capabilities: Set[str] = set(self.capabilities)
        for cap in self.capabilities:
            if cap in CAPABILITY_GRANTS:
                self._expanded_capabilities.update(CAPABILITY_GRANTS[cap])
    
    def has_capability(self, capability: str) -> bool:
        """Check if agent has a capability."""
        return capability in self._expanded_capabilities
    
    def get_required_capability(self, tool_name: str) -> Optional[str]:
        """Get capability required for a tool, or None if unrestricted."""
        # Check custom mappings first
        if tool_name in self.custom_tool_capabilities:
            return self.custom_tool_capabilities[tool_name]
        return TOOL_CAPABILITIES.get(tool_name)
    
    def can_execute_tool(self, tool_name: str) -> bool:
        """Check if agent can execute a tool."""
        required = self.get_required_capability(tool_name)
        if required is None:
            return True  # No capability required
        return self.has_capability(required)
    
    def require_for_tool(self, tool_name: str) -> None:
        """
        Require capability for tool. Raises if missing.
        
        Call this BEFORE executing the tool.
        """
        required = self.get_required_capability(tool_name)
        if required and not self.has_capability(required):
            raise CapabilityError(
                capability=required,
                tool_name=tool_name,
                agent_id=self.agent_id,
            )
    
    async def execute_tool(
        self,
        tool_name: str,
        tool_fn: Callable,
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute tool with capability check.
        
        Raises CapabilityError if agent lacks required capability.
        """
        self.require_for_tool(tool_name)
        
        # Execute the tool
        if callable(tool_fn):
            result = tool_fn(*args, **kwargs)
            # Handle async
            if hasattr(result, "__await__"):
                result = await result
            return result
        return None
    
    def filter_allowed_tools(self, tool_names: List[str]) -> List[str]:
        """Filter tool list to only those agent can execute."""
        return [t for t in tool_names if self.can_execute_tool(t)]


# =============================================================================
# Factory
# =============================================================================

def create_enforcer_for_agent(agent: dict) -> CapabilityEnforcer:
    """Create enforcer from agent dict."""
    import json
    
    capabilities = agent.get("capabilities") or []
    if isinstance(capabilities, str):
        try:
            capabilities = json.loads(capabilities)
        except:
            capabilities = []
    
    return CapabilityEnforcer(
        capabilities=capabilities,
        agent_id=agent.get("id"),
    )


# =============================================================================
# Decorators for Tools
# =============================================================================

def requires_capability(capability: str):
    """
    Decorator to mark a tool as requiring a capability.
    
    Usage:
        @requires_capability("moderate_content")
        async def approve_submission(submission_id: str):
            ...
    
    The actual enforcement happens at dispatch time via CapabilityEnforcer.
    This decorator just marks the requirement for documentation/introspection.
    """
    def decorator(fn):
        fn._required_capability = capability
        return fn
    return decorator


def get_tool_capability(fn: Callable) -> Optional[str]:
    """Get capability required by a decorated tool function."""
    return getattr(fn, "_required_capability", None)
