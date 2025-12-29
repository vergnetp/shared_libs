"""Base tool interface."""

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field


@dataclass
class ToolDefinition:
    """Tool definition for LLM function calling."""
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })


class Tool(ABC):
    """Base class for tools."""
    
    name: str
    description: str
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """
        Execute the tool.
        
        Args:
            **kwargs: Tool arguments from LLM
            
        Returns:
            Tool result (will be converted to string for LLM)
        """
        ...
    
    @abstractmethod
    def get_definition(self) -> ToolDefinition:
        """Get tool definition for LLM."""
        ...
    
    def to_dict(self) -> dict:
        """Convert to dict format for providers."""
        defn = self.get_definition()
        return {
            "name": defn.name,
            "description": defn.description,
            "parameters": defn.parameters,
        }
