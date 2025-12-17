"""
AI Agents Module

Provider-agnostic AI agent framework with:
- Multiple LLM providers (Anthropic, OpenAI, Ollama)
- Memory strategies for context management
- Tool/function calling
- Guardrails for safety
- Background workers for summarization

Usage:
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

from .providers import (
    LLMProvider,
    AnthropicProvider,
    OpenAIProvider,
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
)

from .tools import (
    Tool,
    register_tool,
    get_tool,
    execute_tool_calls,
)

from .runner import AgentRunner

__version__ = "0.1.0"

__all__ = [
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
    # Tools
    "Tool",
    "register_tool",
    "get_tool",
    "execute_tool_calls",
    # Runner
    "AgentRunner",
]
