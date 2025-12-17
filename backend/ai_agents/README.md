# AI Agents

Provider-agnostic AI agent framework.

## Quick Start

```python
from ai_agents import AgentRunner, get_provider, get_memory_strategy

# Setup provider
provider = get_provider("anthropic", api_key="...", model="claude-sonnet-4-20250514")

# Setup memory strategy
memory = get_memory_strategy("last_n", n=20)

# Create runner
runner = AgentRunner(conn, auth, provider, memory)

# Create agent
from ai_agents import AgentStore
agents = AgentStore(conn)
agent = await agents.create(
    name="assistant",
    system_prompt="You are a helpful assistant.",
    model="claude-sonnet-4-20250514",
    provider="anthropic",
)

# Create thread
from ai_agents import ThreadStore
threads = ThreadStore(conn)
thread = await threads.create(agent_id=agent["id"])

# Assign user permission
await auth.assign_role(user_id, "owner", "thread", thread["id"])

# Run conversation
response = await runner.run(thread["id"], user_id, "Hello!")
print(response["content"])
```

## Providers

```python
from ai_agents import get_provider

# Anthropic
provider = get_provider("anthropic", api_key="...", model="claude-sonnet-4-20250514")

# OpenAI
provider = get_provider("openai", api_key="...", model="gpt-4o")

# Ollama (local)
provider = get_provider("ollama", model="llama3.1", base_url="http://localhost:11434")
```

## Memory Strategies

```python
from ai_agents import get_memory_strategy

# Last N messages
memory = get_memory_strategy("last_n", n=20)

# First K + Last N (preserves initial context)
memory = get_memory_strategy("first_last", first=2, last=10)

# Summarization-based
memory = get_memory_strategy("summarize", recent=10)

# Token-limited
memory = get_memory_strategy("token_window", max_tokens=100000)
```

## Tools

```python
from ai_agents import Tool, register_tool, ToolDefinition

class MyTool(Tool):
    name = "my_tool"
    description = "Does something useful"
    
    async def execute(self, query: str) -> str:
        return f"Result for: {query}"
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query"}
                },
                "required": ["query"]
            }
        )

# Register tool
register_tool(MyTool())

# Create agent with tools
agent = await agents.create(
    name="tool-user",
    system_prompt="You can use tools.",
    tools=["my_tool", "calculator"],
)
```

## Streaming

```python
from ai_agents import AgentRunner
from ai_agents.runner import stream_as_sse

# Stream response
async for chunk in runner.stream(thread_id, user_id, "Hello!"):
    print(chunk, end="", flush=True)

# FastAPI with SSE
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

@app.post("/chat/stream")
async def chat_stream(thread_id: str, content: str, user_id: str):
    return StreamingResponse(
        stream_as_sse(runner.stream(thread_id, user_id, content)),
        media_type="text/event-stream"
    )
```

## Background Workers

```python
from ai_agents.workers import queue_summarization, queue_title_generation

# After long conversations, queue summarization
await queue_summarization(thread_id)

# Auto-generate thread title
await queue_title_generation(thread_id)
```

## Module Structure

```
ai_agents/
├── core/           # Types, exceptions
├── providers/      # LLM providers (Anthropic, OpenAI, Ollama)
├── memory/         # Context window strategies
├── store/          # CRUD (pure, no auth)
├── context/        # Context building
├── tools/          # Function calling
├── attachments/    # Format files for providers
├── guardrails/     # Safety checks
├── runner/         # Orchestration (auth + persist + LLM)
└── workers/        # Background jobs
```

## Dependencies

```
pip install anthropic openai httpx
```
