"""Agent definition - compiles to system prompt."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentDefinition:
    """
    Rich agent definition that compiles to a system prompt.
    
    Instead of writing raw system prompts, define structured components
    that compile into a consistent, well-formatted prompt.
    
    Example:
        definition = AgentDefinition(
            role="You are a property management assistant",
            goal="Help property managers with their vacation rentals",
            constraints=[
                "Only answer questions about properties in the system",
                "Never make up information - use tools to search",
                "Be concise but friendly",
            ],
            personality={"tone": "friendly", "style": "concise"},
            examples=[
                {"user": "What's the checkout time?", "assistant": "The checkout time is 11 AM."},
            ],
        )
        
        system_prompt = definition.compile()
    """
    
    # Core identity
    role: str
    goal: Optional[str] = None
    
    # Behavioral constraints
    constraints: list[str] = field(default_factory=list)
    
    # Personality traits
    personality: dict = field(default_factory=dict)
    
    # Few-shot examples
    examples: list[dict] = field(default_factory=list)
    
    # Additional context
    context: Optional[str] = None
    
    # Tool usage instructions
    tool_instructions: Optional[str] = None
    
    def compile(self) -> str:
        """
        Compile definition into a system prompt.
        
        Returns:
            Formatted system prompt string
        """
        sections = []
        
        # Role (required)
        sections.append(f"# Role\n{self.role}")
        
        # Goal
        if self.goal:
            sections.append(f"# Goal\n{self.goal}")
        
        # Context
        if self.context:
            sections.append(f"# Context\n{self.context}")
        
        # Personality
        if self.personality:
            personality_lines = []
            for trait, value in self.personality.items():
                personality_lines.append(f"- {trait.replace('_', ' ').title()}: {value}")
            sections.append(f"# Personality\n" + "\n".join(personality_lines))
        
        # Constraints
        if self.constraints:
            constraint_lines = [f"- {c}" for c in self.constraints]
            sections.append(f"# Constraints\n" + "\n".join(constraint_lines))
        
        # Tool instructions
        if self.tool_instructions:
            sections.append(f"# Tool Usage\n{self.tool_instructions}")
        
        # Examples
        if self.examples:
            example_lines = []
            for i, ex in enumerate(self.examples, 1):
                example_lines.append(f"Example {i}:")
                example_lines.append(f"User: {ex.get('user', '')}")
                example_lines.append(f"Assistant: {ex.get('assistant', '')}")
                example_lines.append("")
            sections.append(f"# Examples\n" + "\n".join(example_lines))
        
        return "\n\n".join(sections)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "role": self.role,
            "goal": self.goal,
            "constraints": self.constraints,
            "personality": self.personality,
            "examples": self.examples,
            "context": self.context,
            "tool_instructions": self.tool_instructions,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentDefinition":
        """Create from dictionary."""
        return cls(
            role=data.get("role", ""),
            goal=data.get("goal"),
            constraints=data.get("constraints", []),
            personality=data.get("personality", {}),
            examples=data.get("examples", []),
            context=data.get("context"),
            tool_instructions=data.get("tool_instructions"),
        )
    
    @classmethod
    def from_system_prompt(cls, system_prompt: str, role: str = None) -> "AgentDefinition":
        """
        Create a minimal definition from an existing system prompt.
        
        Use when migrating from raw system prompts.
        """
        return cls(
            role=role or system_prompt[:100] + "..." if len(system_prompt) > 100 else system_prompt,
            context=system_prompt,
        )


# Pre-built templates
class AgentTemplates:
    """Common agent definition templates."""
    
    @staticmethod
    def assistant(name: str = "Assistant") -> AgentDefinition:
        """Generic helpful assistant."""
        return AgentDefinition(
            role=f"You are {name}, a helpful AI assistant.",
            goal="Help users accomplish their tasks efficiently and accurately.",
            personality={"tone": "friendly", "style": "concise"},
            constraints=[
                "Be helpful and accurate",
                "Admit when you don't know something",
                "Ask clarifying questions when needed",
            ],
        )
    
    @staticmethod
    def rag_assistant(name: str = "Assistant", domain: str = "documents") -> AgentDefinition:
        """Assistant with document search capabilities."""
        return AgentDefinition(
            role=f"You are {name}, an AI assistant with access to a {domain} knowledge base.",
            goal=f"Help users find information in the {domain} and answer their questions accurately.",
            personality={"tone": "professional", "style": "thorough"},
            constraints=[
                "Always search the knowledge base before answering factual questions",
                "Cite sources when providing information from documents",
                "Clearly distinguish between information from documents vs general knowledge",
                "If information isn't in the documents, say so clearly",
            ],
            tool_instructions=(
                "Use search_documents to find relevant information before answering. "
                "Use ask_documents for direct Q&A from the knowledge base."
            ),
        )
    
    @staticmethod
    def property_manager() -> AgentDefinition:
        """Property management assistant (Hostomatic)."""
        return AgentDefinition(
            role="You are a property management assistant for vacation rental hosts.",
            goal="Help property managers run their vacation rentals efficiently.",
            personality={"tone": "friendly", "style": "practical"},
            constraints=[
                "Focus on actionable advice",
                "Reference specific properties when relevant",
                "Use the knowledge base for property-specific information",
                "Be mindful of different time zones for check-in/check-out times",
            ],
            tool_instructions=(
                "Search documents for property-specific information like house rules, "
                "amenities, and local recommendations."
            ),
        )
