"""
AI Agents Module

Provider-agnostic AI agent framework with security, cost control, and reliability.

Simple Usage:
    from ai_agents import Agent
    
    agent = Agent(
        name="Assistant",
        role="You help users with their questions.",
        provider="anthropic",
        api_key="sk-...",
    )
    
    response = await agent.chat("Hello!")

With Fallback Providers:
    agent = Agent(
        role="Property assistant",
        providers=[
            {"provider": "anthropic", "api_key": "..."},
            {"provider": "openai", "api_key": "..."},
        ],
        fallback=True,
    )

With Cost Budget:
    agent = Agent(
        role="Property assistant",
        provider="openai",
        max_conversation_cost=0.50,  # Max $0.50 per conversation
        auto_degrade=True,           # Switch to cheaper model at 80%
    )
    
    # Check costs
    print(f"Conversation: ${agent.conversation_cost:.4f}")
    print(f"Total: ${agent.total_cost:.4f}")

Conversation Branching:
    agent2 = agent.fork()
    response1 = await agent.chat("Option A")
    response2 = await agent2.chat("Option B")

Security Audit:
    report = await agent.security_audit()
    print(f"Pass rate: {report.pass_rate:.1%}")
    print(f"Blocked: {agent.get_security_report()['total_blocked']}")

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
    ChatResult,
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
    KNOWLEDGE_PROMPTS,
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
    VectorMemory,
    get_memory_strategy,
)

from .context import (
    ContextProvider,
    ContextBuilder,
    DefaultContextProvider,
    InMemoryContextProvider,
    DefaultContextBuilder,
)

from .store import (
    ThreadStore,
    MessageStore,
    ThreadSafeMessageStore,
    AgentStore,
    UserContextStore,
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

# Concurrency utilities for multi-agent safety
from .concurrency import (
    get_lock,
    with_lock,
    user_context_lock,
    thread_lock,
    file_lock,
    LockManager,
    get_lock_manager,
    ThreadSafeTool,
    thread_safe_tool,
)

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

from .costs import (
    CostTracker,
    BudgetExceededError,
    calculate_cost,
    PROVIDER_COSTS,
)

from .model_config import (
    ModelInfo,
    get_model_info,
    get_max_context,
    get_max_output,
    get_default_model,
    list_models,
    reload_models,
    get_models_catalog,
    get_providers,
    get_models_by_provider,
)

from .security import (
    SecurityAuditLog,
    SecurityEvent,
    ThreatType,
    DetectionMethod,
    get_security_log,
    set_security_log,
)

from .testing import (
    run_security_audit,
    AuditReport,
    TestResult,
    AttackCategory,
    ATTACK_SUITE,
    get_attack_suite,
)

# Multi-agent orchestration
from .orchestration import (
    ParallelAgents,
    parallel_chat,
    Supervisor,
    SupervisorConfig,
    Pipeline,
    Debate,
)

__version__ = "0.3.0"

__all__ = [
    # Simple API
    "Agent",
    "create_agent",
    "AgentDefinition",
    "AgentTemplates",
    "KNOWLEDGE_PROMPTS",
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
    "BudgetExceededError",
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
    "VectorMemory",
    "get_memory_strategy",
    # Store
    "ThreadStore",
    "MessageStore",
    "ThreadSafeMessageStore",
    "AgentStore",
    "UserContextStore",
    # Context
    "ContextProvider",
    "ContextBuilder",
    "DefaultContextProvider",
    "InMemoryContextProvider",
    "DefaultContextBuilder",
    # Tools
    "Tool",
    "tool",
    "FunctionTool",
    "register_tool",
    "get_tool",
    "execute_tool_calls",
    # Runner
    "AgentRunner",
    # Concurrency (multi-agent safety)
    "get_lock",
    "with_lock",
    "user_context_lock",
    "thread_lock",
    "file_lock",
    "LockManager",
    "get_lock_manager",
    "ThreadSafeTool",
    "thread_safe_tool",
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
    # Cost tracking
    "CostTracker",
    "calculate_cost",
    "PROVIDER_COSTS",
    # Model configuration
    "ModelInfo",
    "get_model_info",
    "get_max_context",
    "get_max_output",
    "get_default_model",
    "list_models",
    "reload_models",
    "get_models_catalog",
    "get_providers",
    "get_models_by_provider",
    # Security
    "SecurityAuditLog",
    "SecurityEvent",
    "ThreatType",
    "DetectionMethod",
    "get_security_log",
    "set_security_log",
    # Testing
    "run_security_audit",
    "AuditReport",
    "TestResult",
    "AttackCategory",
    "ATTACK_SUITE",
    "get_attack_suite",
    # Multi-agent orchestration
    "ParallelAgents",
    "parallel_chat",
    "Supervisor",
    "SupervisorConfig",
    "Pipeline",
    "Debate",
]
