# Cloud LLM Clients

HTTP clients for LLM APIs with retry support, streaming, and unified interface.

## Supported Providers

| Provider | Client | Base URL |
|----------|--------|----------|
| OpenAI | `OpenAICompatClient` | `https://api.openai.com/v1` |
| Groq | `OpenAICompatClient` | `https://api.groq.com/openai/v1` |
| Together | `OpenAICompatClient` | `https://api.together.xyz/v1` |
| Anthropic | `AnthropicClient` | `https://api.anthropic.com` |

## Quick Start

### OpenAI

```python
from cloud.llm import OpenAICompatClient

client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
response = client.chat([
    {"role": "user", "content": "Hello!"}
])
print(response.content)
```

### Groq (OpenAI-compatible)

```python
from cloud.llm import OpenAICompatClient

client = OpenAICompatClient(
    api_key="gsk-...",
    base_url="https://api.groq.com/openai/v1",
    model="llama-3.3-70b-versatile"
)
response = client.chat(messages)
```

### Anthropic Claude

```python
from cloud.llm import AnthropicClient

client = AnthropicClient(api_key="sk-ant-...", model="claude-sonnet-4-20250514")
response = client.chat(
    messages=[{"role": "user", "content": "Hello!"}],
    system="You are a helpful assistant.",
)
print(response.content)
```

## Streaming

### Sync Streaming

```python
for chunk in client.chat_stream(messages):
    print(chunk, end="", flush=True)
```

### Async Streaming

```python
from cloud.llm import AsyncOpenAICompatClient

async with AsyncOpenAICompatClient(api_key="...") as client:
    async for chunk in client.chat_stream(messages):
        print(chunk, end="")
```

## Tool Calls

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

response = client.chat(messages, tools=tools)

if response.has_tool_calls:
    for tc in response.tool_calls:
        print(f"Call: {tc.name}({tc.arguments})")
        # Execute tool and continue conversation...
```

## Response Object

```python
@dataclass
class ChatResponse:
    content: str              # Text response
    model: str                # Model used
    input_tokens: int         # Prompt tokens
    output_tokens: int        # Completion tokens
    finish_reason: str        # "stop", "tool_calls", "length"
    tool_calls: list[ToolCall]  # Tool calls (if any)
    raw: dict                 # Original API response
    
    @property
    def has_tool_calls(self) -> bool: ...
    
    @property
    def total_tokens(self) -> int: ...
```

## Error Handling

```python
from cloud import LLMError, LLMRateLimitError, LLMAuthError

try:
    response = client.chat(messages)
except LLMRateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except LLMAuthError:
    print("Invalid API key")
except LLMError as e:
    print(f"API error: {e}")
```

## Configuration

```python
client = OpenAICompatClient(
    api_key="...",
    model="gpt-4o",
    timeout=120.0,      # Request timeout
    max_retries=3,      # Retry attempts (non-streaming only)
)
```

## Architecture

```
cloud.llm/
├── types.py          # ChatResponse, ToolCall dataclasses
├── openai_compat.py  # OpenAI/Groq/Together clients
└── anthropic.py      # Claude clients
```

### Design Decisions

1. **Raw HTTP, no SDK** - Full control, fewer dependencies
2. **Separate streaming** - Uses raw aiohttp/httpx (SSE needs line-by-line)
3. **No token counting** - Handled by `ai.tokens` module
4. **No XML parsing** - Provider quirks handled by `ai.providers`

### Relationship to `ai` Module

```
cloud.llm          →  Raw HTTP clients
    ↓
ai.providers       →  Wraps cloud.llm, adds:
                      - Token counting
                      - XML tool call parsing
                      - Provider normalization
    ↓
ai.Agent           →  High-level agent with tools, memory, etc.
```

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `OpenAICompatClient`

Synchronous OpenAI-compatible client for chat completions.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `chat` | `messages: list[dict]`, `temperature: float=0.7`, `max_tokens: int=4096`, `tools: list[dict]=None`, `model: str=None`, `**kwargs` | `ChatResponse` | Chat | Send a chat completion request with optional tools. |
| | `chat_stream` | `messages: list[dict]`, `temperature: float=0.7`, `max_tokens: int=4096`, `model: str=None`, `**kwargs` | `Iterator[str]` | Streaming | Stream chat completion, yielding text chunks. |
| | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `api_key: str`, `model: str="gpt-4o"`, `base_url: str=OPENAI_BASE_URL`, `timeout: float=120.0`, `max_retries: int=3` | | Initialization | Initialize with API key, model, and optional config. |
| | `__enter__` | | `OpenAICompatClient` | Context | Context manager entry. |
| | `__exit__` | `*args` | `None` | Context | Context manager exit, closes client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `AsyncOpenAICompatClient`

Asynchronous OpenAI-compatible client for chat completions.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `chat` | `messages: list[dict]`, `temperature: float=0.7`, `max_tokens: int=4096`, `tools: list[dict]=None`, `model: str=None`, `**kwargs` | `ChatResponse` | Chat | Send a chat completion request with optional tools. |
| `async` | `chat_stream` | `messages: list[dict]`, `temperature: float=0.7`, `max_tokens: int=4096`, `model: str=None`, `**kwargs` | `AsyncIterator[str]` | Streaming | Stream chat completion, yielding text chunks. |
| `async` | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `api_key: str`, `model: str="gpt-4o"`, `base_url: str=OPENAI_BASE_URL`, `timeout: float=120.0`, `max_retries: int=3` | | Initialization | Initialize with API key, model, and optional config. |
| `async` | `__aenter__` | | `AsyncOpenAICompatClient` | Context | Async context manager entry. |
| `async` | `__aexit__` | `*args` | `None` | Context | Async context manager exit, closes client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `AnthropicClient`

Synchronous Anthropic Claude client for chat completions.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `chat` | `messages: list[dict]`, `system: str=None`, `temperature: float=0.7`, `max_tokens: int=4096`, `tools: list[dict]=None`, `model: str=None`, `**kwargs` | `ChatResponse` | Chat | Send a chat completion request with optional system prompt and tools. |
| | `chat_stream` | `messages: list[dict]`, `system: str=None`, `temperature: float=0.7`, `max_tokens: int=4096`, `model: str=None`, `**kwargs` | `Iterator[str]` | Streaming | Stream chat completion, yielding text chunks. |
| | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `api_key: str`, `model: str="claude-sonnet-4-20250514"`, `timeout: float=120.0`, `max_retries: int=3` | | Initialization | Initialize with API key, model, and optional config. |
| | `__enter__` | | `AnthropicClient` | Context | Context manager entry. |
| | `__exit__` | `*args` | `None` | Context | Context manager exit, closes client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `AsyncAnthropicClient`

Asynchronous Anthropic Claude client for chat completions.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `chat` | `messages: list[dict]`, `system: str=None`, `temperature: float=0.7`, `max_tokens: int=4096`, `tools: list[dict]=None`, `model: str=None`, `**kwargs` | `ChatResponse` | Chat | Send a chat completion request with optional system prompt and tools. |
| `async` | `chat_stream` | `messages: list[dict]`, `system: str=None`, `temperature: float=0.7`, `max_tokens: int=4096`, `model: str=None`, `**kwargs` | `AsyncIterator[str]` | Streaming | Stream chat completion, yielding text chunks. |
| `async` | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `api_key: str`, `model: str="claude-sonnet-4-20250514"`, `timeout: float=120.0`, `max_retries: int=3` | | Initialization | Initialize with API key, model, and optional config. |
| `async` | `__aenter__` | | `AsyncAnthropicClient` | Context | Async context manager entry. |
| `async` | `__aexit__` | `*args` | `None` | Context | Async context manager exit, closes client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `ChatResponse`

Response from an LLM chat completion.

<details>
<summary><strong>Attributes</strong></summary>

| Attribute | Type | Description |
|-----------|------|-------------|
| `content` | `str` | Text content from the response. |
| `model` | `str` | Model that generated the response. |
| `input_tokens` | `int` | Number of input/prompt tokens. |
| `output_tokens` | `int` | Number of output/completion tokens. |
| `finish_reason` | `str` | Why generation stopped: 'stop', 'tool_calls', 'length'. |
| `tool_calls` | `list[ToolCall]` | Tool calls requested by the model. |
| `raw` | `dict` | Original API response. |

</details>

<br>

<details>
<summary><strong>Properties</strong></summary>

| Property | Returns | Description |
|----------|---------|-------------|
| `has_tool_calls` | `bool` | Check if response contains tool calls. |
| `total_tokens` | `int` | Total tokens used (input + output). |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px; margin-top: 24px;">

### class `ToolCall`

A tool/function call from the LLM.

<details>
<summary><strong>Attributes</strong></summary>

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | `str` | Unique identifier for this tool call. |
| `name` | `str` | Name of the tool to call. |
| `arguments` | `dict[str, Any]` | Arguments to pass to the tool. |

</details>

<br>

<details>
<summary><strong>Class Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `from_openai` | `tc: dict` | `ToolCall` | Parse OpenAI-style tool call. |
| `from_anthropic` | `block: dict` | `ToolCall` | Parse Anthropic-style tool_use block. |

</details>

<br>

<details>
<summary><strong>Instance Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `to_openai` | | `dict` | Convert to OpenAI format for message history. |

</details>

</div>
