# Instructor Integration

Optional integration with [Instructor](https://github.com/jxnl/instructor) for guaranteed structured LLM outputs.

## Why Instructor?

Your XML parsing in `agent.py` exists because models (especially Llama/Groq/Ollama) output malformed JSON as XML-like tags. Instructor fixes this at the source by:

1. **Forcing schema compliance** - LLM outputs always match your Pydantic model
2. **Automatic retries** - Invalid outputs trigger automatic correction
3. **Works with all providers** - OpenAI, Anthropic, Groq, Ollama

## Installation

```bash
pip install instructor pydantic
```

## Quick Start

### Basic Structured Output

```python
from ai_agents.providers import OpenAIProvider, enable_instructor
from ai_agents.providers.instructor_support import StructuredResponse

# Wrap your provider
provider = OpenAIProvider(api_key="...")
provider = enable_instructor(provider)

# Get guaranteed structured output
result = await provider.complete_structured(
    messages=[{"role": "user", "content": "What's 2+2?"}],
    response_model=StructuredResponse,
)

print(result.content)  # "4"
print(result.tool_calls)  # []
```

### Guaranteed Tool Calls

```python
from ai_agents.providers import enable_instructor, ToolCallList

provider = enable_instructor(OpenAIProvider(api_key="..."))

# Define your tools
tools = [
    {
        "name": "search_documents",
        "description": "Search the knowledge base",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    }
]

# Get tool calls with guaranteed valid structure
result = await provider.complete_with_tools_structured(
    messages=[{"role": "user", "content": "Find docs about Python"}],
    tools=tools,
)

# result.tool_calls is always valid - no XML parsing needed
for tc in result.tool_calls:
    print(f"Call: {tc.name}({tc.arguments})")
```

### Convenience Function

```python
from ai_agents.providers import extract_tool_calls

# One-liner that handles setup internally
tool_calls = await extract_tool_calls(
    provider=my_provider,
    messages=[...],
    tools=[...],
)
# Returns: [{"id": "...", "name": "...", "arguments": {...}}]
```

### Custom Response Models

```python
from pydantic import BaseModel, Field
from typing import List

class MovieRecommendation(BaseModel):
    title: str = Field(description="Movie title")
    year: int = Field(description="Release year")
    reason: str = Field(description="Why this movie fits")

class MovieList(BaseModel):
    recommendations: List[MovieRecommendation]
    genre: str

result = await provider.complete_structured(
    messages=[{"role": "user", "content": "Recommend 3 sci-fi movies"}],
    response_model=MovieList,
)

for movie in result.recommendations:
    print(f"{movie.title} ({movie.year}): {movie.reason}")
```

## Available Models

| Model | Use Case |
|-------|----------|
| `ToolCallModel` | Single tool call |
| `ToolCallList` | Multiple tool calls |
| `TextResponse` | Plain text output |
| `StructuredResponse` | Text OR tool calls |
| `Classification` | Label + confidence |
| `ExtractedEntities` | Entity extraction |

## Provider Support

| Provider | Status | Notes |
|----------|--------|-------|
| OpenAI | ✅ | Full support |
| Anthropic | ✅ | Full support |
| Groq | ✅ | Uses OpenAI-compatible mode |
| Ollama | ✅ | Uses JSON mode (best for local) |

## Graceful Degradation

If Instructor isn't installed, the library falls back to regular completions:

```python
from ai_agents.providers import is_instructor_available

if is_instructor_available():
    # Use structured outputs
    result = await provider.complete_structured(...)
else:
    # Fall back to regular completion + XML parsing
    result = await provider.run(...)
```

## Integration with Agent

The `Agent` class can optionally use Instructor for tool call parsing. Enable it when creating the agent:

```python
from ai_agents import Agent
from ai_agents.providers import enable_instructor

agent = Agent(
    role="Assistant with tools",
    tools=["search_documents", "calculator"],
)

# Enable instructor on the provider
agent._provider = enable_instructor(agent._provider)

# Now tool calls from Llama/Groq will be guaranteed valid
response = await agent.chat("Search for Python docs")
```

## When NOT to Use Instructor

- **Simple chat without tools** - Regular completion is faster
- **Streaming responses** - Instructor doesn't support streaming (yet)
- **Very long outputs** - JSON schema adds token overhead
