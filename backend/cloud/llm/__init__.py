"""
LLM Clients - HTTP clients for LLM APIs.

Provides sync and async clients for:
- OpenAI and OpenAI-compatible APIs (Groq, Together, etc.)
- Anthropic Claude

All clients include:
- Automatic retries with exponential backoff (non-streaming)
- Configurable timeouts
- Streaming support (SSE parsing)

Quick Start:
    # OpenAI (sync)
    from cloud.llm import OpenAICompatClient
    
    client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
    response = client.chat([{"role": "user", "content": "Hello"}])
    print(response.content)
    
    # Groq (OpenAI-compatible)
    client = OpenAICompatClient(
        api_key="gsk-...",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile"
    )
    
    # Anthropic (sync)
    from cloud.llm import AnthropicClient
    
    client = AnthropicClient(api_key="sk-ant-...")
    response = client.chat(
        messages=[{"role": "user", "content": "Hello"}],
        system="You are helpful.",
    )
    
    # Streaming (sync)
    for chunk in client.chat_stream(messages):
        print(chunk, end="", flush=True)
    
    # Async usage
    from cloud.llm import AsyncOpenAICompatClient, AsyncAnthropicClient
    
    async with AsyncOpenAICompatClient(api_key="...") as client:
        response = await client.chat(messages)
        
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")

Tool Calls:
    response = client.chat(messages, tools=[
        {"name": "get_weather", "description": "...", "parameters": {...}}
    ])
    
    if response.has_tool_calls:
        for tc in response.tool_calls:
            print(f"Call {tc.name} with {tc.arguments}")

Note:
    These are raw HTTP clients. For higher-level features like:
    - Token counting
    - XML tool call parsing (Llama/Groq quirks)
    - Provider response normalization
    
    Use the `ai.providers` module which wraps these clients.
"""

# Types
from .types import (
    ChatResponse,
    ToolCall,
    StreamChunk,
)

# OpenAI-compatible (OpenAI, Groq, Together, etc.)
from .openai_compat import (
    OpenAICompatClient,
    AsyncOpenAICompatClient,
    OPENAI_BASE_URL,
)

# Anthropic Claude
from .anthropic import (
    AnthropicClient,
    AsyncAnthropicClient,
    ANTHROPIC_BASE_URL,
)


__all__ = [
    # Types
    "ChatResponse",
    "ToolCall",
    "StreamChunk",
    # OpenAI-compatible
    "OpenAICompatClient",
    "AsyncOpenAICompatClient",
    "OPENAI_BASE_URL",
    # Anthropic
    "AnthropicClient",
    "AsyncAnthropicClient",
    "ANTHROPIC_BASE_URL",
]


# Convenience aliases
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
TOGETHER_BASE_URL = "https://api.together.xyz/v1"
