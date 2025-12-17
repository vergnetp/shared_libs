"""
AI Agents Module

Provider-agnostic AI agent framework.

Simple Usage:
    from ai_agents import Agent
    
    agent = Agent(
        name="Assistant",
        role="You help users with their questions.",
        provider="anthropic",
        api_key="sk-...",
    )
    
    response = await agent.chat("Hello!")

With Definition:
    from ai_agents import Agent, AgentDefinition
    
    definition = AgentDefinition(
        role="You are a property management assistant",
        goal="Help hosts manage vacation rentals",
        constraints=["Be concise", "Use tools to search documents"],
    )
    
    agent = Agent(definition=definition, provider="openai", api_key="sk-...")
    response = await agent.chat("What's the checkout time?")

Full Control:
    from ai_agents import AgentRunner, get_provider, get_memory_strategy
    
    provider = get_provider("anthropic", api_key="...", model="claude-sonnet-4-20250514")
    memory = get_memory_strategy("last_n", n=20)
    runner = AgentRunner(conn, auth, provider, memory)
    
    response = await runner.run(thread_id, user_id, "Hello!")
"""

from .core import (
    MessageRole,
    Message,
    ProviderResponse,
    ToolCall,
    ToolResult,
    AgentConfig,
    ThreadConfig,
    AgentError,
    ProviderError,
    ToolError,
    GuardrailError,
)

from .definition import (
    AgentDefinition,
    AgentTemplates,
)

from .agent import (
    Agent,
    create_agent,
)

from .providers import (
    LLMProvider,
    AnthropicProvider,
    OpenAIProvider,
    OpenAIAssistantProvider,
    OllamaProvider,
    get_provider,
)

from .memory import (
    MemoryStrategy,
    LastNMemory,
    FirstLastMemory,
    SummarizeMemory,
    TokenWindowMemory,
    get_memory_strategy,
)

from .store import (
    ThreadStore,
    MessageStore,
    AgentStore,
    UserMemoryStore,
    UserMemoryExtractor,
)

from .tools import (
    Tool,
    tool,
    FunctionTool,
    register_tool,
    get_tool,
    execute_tool_calls,
)

from .runner import AgentRunner

from .limits import (
    RateLimiter,
    InMemoryBackend,
    RedisBackend,
    get_rate_limiter,
    JobQueue,
    InMemoryQueueBackend,
    RedisQueueBackend,
    Job,
    JobStatus,
)

__version__ = "0.1.0"

__all__ = [
    # Simple API
    "Agent",
    "create_agent",
    "AgentDefinition",
    "AgentTemplates",
    # Core types
    "MessageRole",
    "Message",
    "ProviderResponse",
    "ToolCall",
    "ToolResult",
    "AgentConfig",
    "ThreadConfig",
    # Exceptions
    "AgentError",
    "ProviderError",
    "ToolError",
    "GuardrailError",
    # Providers
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "OpenAIAssistantProvider",
    "OllamaProvider",
    "get_provider",
    # Memory
    "MemoryStrategy",
    "LastNMemory",
    "FirstLastMemory",
    "SummarizeMemory",
    "TokenWindowMemory",
    "get_memory_strategy",
    # Store
    "ThreadStore",
    "MessageStore",
    "AgentStore",
    "UserMemoryStore",
    "UserMemoryExtractor",
    # Tools
    "Tool",
    "tool",
    "FunctionTool",
    "register_tool",
    "get_tool",
    "execute_tool_calls",
    # Runner
    "AgentRunner",
    # Limits (optional Redis)
    "RateLimiter",
    "InMemoryBackend",
    "RedisBackend",
    "get_rate_limiter",
    "JobQueue",
    "InMemoryQueueBackend",
    "RedisQueueBackend",
    "Job",
    "JobStatus",
]
