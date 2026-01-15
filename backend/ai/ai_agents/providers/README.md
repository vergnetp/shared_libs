# AI Agents - Providers

LLM provider implementations for the AI agents framework.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        ai.providers                              │
│  AnthropicProvider, OpenAIProvider, GroqProvider, etc.          │
│                            │                                     │
│                 ProviderResponse + token counting                │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        cloud.llm                                 │
│  AsyncAnthropicClient, AsyncOpenAICompatClient                  │
│                            │                                     │
│           HTTP calls, retries, circuit breaker, SSE             │
└─────────────────────────────────────────────────────────────────┘
```

## Provider Types

### Cloud.llm-based (HTTP clients)

| Provider | Backend Client | Notes |
|----------|---------------|-------|
| `AnthropicProvider` | `AsyncAnthropicClient` | Claude models |
| `OpenAIProvider` | `AsyncOpenAICompatClient` | GPT models |
| `GroqProvider` | `AsyncOpenAICompatClient` | Fast inference, Llama/Mixtral |

### SDK-based (complex state)

| Provider | SDK | Notes |
|----------|-----|-------|
| `OpenAIAssistantProvider` | `openai` | Threads, runs, polling |
| `OllamaProvider` | `httpx` | Local server, direct HTTP |

## Usage

```python
from ai.providers import get_provider

# Get provider by name
provider = get_provider("anthropic", api_key="sk-ant-...", model="claude-sonnet-4-20250514")

# Run completion
response = await provider.run(
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.7,
    max_tokens=4096,
    tools=[...],
)

print(response.content)
print(response.tool_calls)

# Stream
async for chunk in provider.stream(messages):
    print(chunk, end="")
```

## ProviderResponse

All providers return a standardized `ProviderResponse`:

```python
@dataclass
class ProviderResponse:
    content: str                    # Text response
    usage: dict                     # {"input": int, "output": int}
    model: str                      # Model name
    provider: str                   # Provider name
    tool_calls: list[dict]          # [{"id": str, "name": str, "arguments": dict}]
    finish_reason: str              # "stop", "tool_calls", "length"
    raw: Any                        # Original API response
```

## Token Counting

Providers use `ai.tokens` for accurate counting when available:

```python
from ai.tokens import count_tokens, estimate_tokens

# Accurate (requires tiktoken)
tokens = count_tokens("Hello world", model="gpt-4")

# Fast heuristic
tokens = estimate_tokens("Hello world")
```

## Adding New Providers

1. Create provider class extending `LLMProvider`
2. Implement `run()`, `stream()`, `count_tokens()`, `max_context_tokens`
3. Register in `registry.py`

```python
from .base import LLMProvider
from .registry import register_provider

class MyProvider(LLMProvider):
    name = "myprovider"
    
    async def run(self, messages, **kwargs):
        ...
    
    async def stream(self, messages, **kwargs):
        ...

register_provider("myprovider", MyProvider)
```

## Changes from SDK-based

### Before (SDK)
```python
import anthropic

client = anthropic.AsyncAnthropic(api_key=key)
response = await client.messages.create(...)
```

### After (cloud.llm)
```python
from ....cloud.llm import AsyncAnthropicClient

client = AsyncAnthropicClient(api_key=key)
response = await client.chat(messages)
# Returns ChatResponse dataclass, converted to ProviderResponse
```

Benefits:
- Centralized HTTP handling (retries, circuit breaker)
- Consistent error types (`cloud.LLMError`)
- No SDK dependency for simple cases
- Streaming uses raw aiohttp (proper SSE handling)
