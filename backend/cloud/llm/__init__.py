"""
LLM API Clients - Unified interface for LLM providers.

Provides sync and async clients for:
- OpenAI (GPT-4o, GPT-4, GPT-3.5)
- Anthropic Claude (Claude 4, Claude 3.5)
- Groq (Llama, Mixtral) - via OpenAI-compatible API
- Ollama (local models: Llama, Qwen, Mistral, etc.)
- Any OpenAI-compatible API

All clients include:
- Automatic retries with exponential backoff
- Connection pooling (async clients)
- Request tracing
- Unified error handling
- Tool/function calling support

Quick Start:
    # OpenAI
    from cloud.llm import OpenAICompatClient
    
    client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
    response = client.chat([{"role": "user", "content": "Hello!"}])
    print(response.content)
    
    # Anthropic Claude
    from cloud.llm import AsyncAnthropicClient
    
    async with AsyncAnthropicClient(api_key="sk-ant-...") as client:
        response = await client.chat(
            messages=[{"role": "user", "content": "Hello!"}],
            system="You are helpful.",
        )
        print(response.content)
    
    # Groq (OpenAI-compatible)
    from cloud.llm import AsyncOpenAICompatClient
    
    client = AsyncOpenAICompatClient(
        api_key="gsk-...",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile"
    )
    
    # Ollama (local models)
    from cloud.llm import AsyncOllamaClient
    
    async with AsyncOllamaClient(model="llama3.2") as client:
        response = await client.chat([{"role": "user", "content": "Hello!"}])
        print(response.content)
    
    # Streaming
    async for chunk in client.chat_stream(messages):
        print(chunk, end="", flush=True)
    
    # Tool calls
    response = await client.chat(messages, tools=[
        {"name": "get_weather", "description": "...", "parameters": {...}}
    ])
    if response.has_tool_calls:
        for tc in response.tool_calls:
            print(f"{tc.name}({tc.arguments})")

Shutdown:
    from cloud import close_all_cloud_clients
    
    # In FastAPI shutdown handler (pools are shared with other cloud clients)
    await close_all_cloud_clients()
"""

# Types
from .types import (
    ChatResponse,
    ChatMessage,
    ToolCall,
)

# Errors
from .errors import (
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
    LLMContextLengthError,
    LLMTimeoutError,
    LLMConnectionError,
    LLMContentFilterError,
)

# OpenAI-compatible clients (works with OpenAI, Groq, Azure, etc.)
from .openai_compat import (
    OpenAICompatClient,
    AsyncOpenAICompatClient,
)

# Anthropic Claude clients
from .anthropic import (
    AnthropicClient,
    AsyncAnthropicClient,
)

# Ollama clients (local models)
from .ollama import (
    OllamaClient,
    AsyncOllamaClient,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_RECOMMENDED_MODELS,
    get_default_model,
    get_recommended_models,
)


__all__ = [
    # Types
    "ChatResponse",
    "ChatMessage",
    "ToolCall",
    # Errors
    "LLMError",
    "LLMRateLimitError",
    "LLMAuthError",
    "LLMContextLengthError",
    "LLMTimeoutError",
    "LLMConnectionError",
    "LLMContentFilterError",
    # OpenAI-compatible
    "OpenAICompatClient",
    "AsyncOpenAICompatClient",
    # Anthropic
    "AnthropicClient",
    "AsyncAnthropicClient",
    # Ollama
    "OllamaClient",
    "AsyncOllamaClient",
    "OLLAMA_DEFAULT_MODEL",
    "OLLAMA_RECOMMENDED_MODELS",
    "get_default_model",
    "get_recommended_models",
]
