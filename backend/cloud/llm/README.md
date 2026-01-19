# LLM API Clients

Unified interface for LLM providers with built-in resilience.

## Quick Start

### OpenAI

```python
from cloud.llm import OpenAICompatClient, AsyncOpenAICompatClient

# Sync
client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
response = client.chat([{"role": "user", "content": "Hello!"}])
print(response.content)

# Async
async with AsyncOpenAICompatClient(api_key="sk-...") as client:
    response = await client.chat([{"role": "user", "content": "Hello!"}])
    
    # Streaming
    async for chunk in client.chat_stream(messages):
        print(chunk, end="", flush=True)
```

### Anthropic Claude

```python
from cloud.llm import AnthropicClient, AsyncAnthropicClient

# Sync
client = AnthropicClient(api_key="sk-ant-...")
response = client.chat(
    messages=[{"role": "user", "content": "Hello!"}],
    system="You are helpful.",
)

# Async with streaming
async with AsyncAnthropicClient(api_key="...") as client:
    async for chunk in client.chat_stream(messages, system="Be concise."):
        print(chunk, end="")
```

### Groq (OpenAI-compatible)

```python
from cloud.llm import AsyncOpenAICompatClient

client = AsyncOpenAICompatClient(
    api_key="gsk-...",
    base_url="https://api.groq.com/openai/v1",
    model="llama-3.3-70b-versatile"
)

response = await client.chat(messages)
```

### Ollama (Local Models)

```python
from cloud.llm import OllamaClient, AsyncOllamaClient

# Check availability and models
client = OllamaClient()
if client.is_available():
    models = client.list_models()  # ["llama3.2:latest", "qwen2.5:3b", ...]

# Sync
client = OllamaClient(model="qwen2.5:3b")  # Best quality/size ratio
response = client.chat([{"role": "user", "content": "Hello!"}])
print(response.content)

# Async with streaming
async with AsyncOllamaClient(model="llama3.2") as client:
    async for chunk in client.chat_stream(messages):
        print(chunk, end="", flush=True)

# Model management
client = AsyncOllamaClient()
await client.ensure_model("llama3.2")  # Pull if not present
await client.pull_model("codellama:7b")  # Force pull
```

**Recommended models:**
| Model | Size | Context | Best for |
|-------|------|---------|----------|
| `qwen2.5:3b` | 1.9GB | 32K | General (default) |
| `llama3.2:3b` | 2.0GB | 128K | Long context |
| `codellama:7b` | 3.8GB | 16K | Code |

## Tool Calling

```python
tools = [
    {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"}
            },
            "required": ["location"]
        }
    }
]

response = await client.chat(messages, tools=tools)

if response.has_tool_calls:
    for tc in response.tool_calls:
        print(f"Call: {tc.name}({tc.arguments})")
```

## Response Object

```python
response = await client.chat(messages)

response.content        # str - Generated text
response.model          # str - Model used
response.input_tokens   # int - Prompt tokens
response.output_tokens  # int - Completion tokens
response.total_tokens   # int - Total tokens
response.finish_reason  # str - "stop", "tool_calls", etc.
response.tool_calls     # List[ToolCall] - Tool calls if any
response.has_tool_calls # bool - Quick check
response.raw            # dict - Raw API response
```

## Error Handling

```python
from cloud.llm import (
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
    LLMContextLengthError,
    LLMTimeoutError,
    LLMConnectionError,
)

try:
    response = await client.chat(messages)
except LLMRateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except LLMAuthError:
    print("Invalid API key")
except LLMContextLengthError as e:
    print(f"Context too long: {e.message}")
except LLMTimeoutError:
    print("Request timed out")
except LLMError as e:
    print(f"API error: {e.message} (status: {e.status_code})")
```

## Configuration

```python
# Custom timeout and retries
client = AsyncAnthropicClient(
    api_key="...",
    model="claude-sonnet-4-20250514",
    timeout=120.0,      # Default: 120s (LLM calls can be slow)
    max_retries=3,      # Default: 3
)
```

## Connection Pooling

Async clients use connection pooling automatically:
- All `AsyncOpenAICompatClient` instances to the same base_url share connections
- All `AsyncAnthropicClient` instances share connections to api.anthropic.com
- Auth is passed per-request (multi-tenant safe)

```python
# These share the same TCP connection pool
client1 = AsyncOpenAICompatClient(api_key="user1-key")
client2 = AsyncOpenAICompatClient(api_key="user2-key")

# Shutdown (in FastAPI lifespan)
from cloud import close_all_cloud_clients
await close_all_cloud_clients()
```

## Integration with ai_agents

The `ai.ai_agents.providers` module wraps these clients:

```python
from ai.ai_agents.providers import get_provider

# Uses cloud.llm.AsyncAnthropicClient internally
provider = get_provider("anthropic", api_key="...", model="claude-sonnet-4-20250514")
response = await provider.run(messages)

# Available providers:
# - anthropic → AsyncAnthropicClient
# - openai → AsyncOpenAICompatClient
# - groq → AsyncOpenAICompatClient (Groq URL)
# - ollama → AsyncOllamaClient (local server)
```

---

## API Reference

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `ChatResponse`

Unified response from LLM chat completion.

| Property | Type | Description |
|----------|------|-------------|
| `content` | `str` | Generated text |
| `model` | `str` | Model used |
| `input_tokens` | `int` | Prompt tokens |
| `output_tokens` | `int` | Completion tokens |
| `total_tokens` | `int` | Total tokens (property) |
| `finish_reason` | `str` | Stop reason |
| `tool_calls` | `List[ToolCall]` | Tool calls if any |
| `has_tool_calls` | `bool` | Quick check (property) |
| `raw` | `dict` | Raw API response |

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `ToolCall`

A tool/function call from the LLM.

| Property | Type | Description |
|----------|------|-------------|
| `id` | `str` | Call ID |
| `name` | `str` | Function name |
| `arguments` | `dict` | Parsed arguments |

| Method | Returns | Description |
|--------|---------|-------------|
| `from_openai(tc)` | `ToolCall` | Create from OpenAI format |
| `from_anthropic(block)` | `ToolCall` | Create from Anthropic format |

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `AsyncOpenAICompatClient`

Async OpenAI-compatible client. Works with OpenAI, Groq, Azure, etc.

<details>
<summary><strong>Public Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `chat` | `messages`, `model?`, `temperature?`, `max_tokens?`, `tools?`, `tool_choice?` | `ChatResponse` | Send chat completion |
| `chat_stream` | `messages`, `model?`, `temperature?`, `max_tokens?` | `AsyncIterator[str]` | Stream completion |
| `close` | | `None` | No-op (pool managed globally) |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `AsyncAnthropicClient`

Async Anthropic Claude client.

<details>
<summary><strong>Public Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `chat` | `messages`, `model?`, `system?`, `temperature?`, `max_tokens?`, `tools?`, `tool_choice?` | `ChatResponse` | Send chat completion |
| `chat_stream` | `messages`, `model?`, `system?`, `temperature?`, `max_tokens?` | `AsyncIterator[str]` | Stream completion |
| `close` | | `None` | No-op (pool managed globally) |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### Sync Clients

`OpenAICompatClient`, `AnthropicClient`, and `OllamaClient` have the same methods as their async counterparts, but synchronous.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `AsyncOllamaClient`

Async Ollama client for local LLM inference.

<details>
<summary><strong>Public Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `chat` | `messages`, `model?`, `system?`, `temperature?`, `max_tokens?`, `tools?` | `ChatResponse` | Send chat completion |
| `chat_stream` | `messages`, `model?`, `system?`, `temperature?`, `max_tokens?` | `AsyncIterator[str]` | Stream completion |
| `is_available` | | `bool` | Check if Ollama server is running |
| `list_models` | | `List[str]` | List installed models |
| `has_model` | `model_name` | `bool` | Check if model is installed |
| `pull_model` | `model_name` | `bool` | Download a model |
| `ensure_model` | `model_name` | `bool` | Pull model if not present |

</details>

</div>
