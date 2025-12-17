# AI Agents

Provider-agnostic AI agent framework.

## Quick Start

```python
from ai_agents import Agent

agent = Agent(
    name="Assistant",
    role="You help users with their questions.",
    provider="anthropic",
    api_key="sk-ant-...",
)

response = await agent.chat("Hello!")
print(response)

# Continue conversation (same thread)
response = await agent.chat("Tell me more")

# Start new thread
agent.new_thread()
response = await agent.chat("Different topic")
```

## Agent Definition

Structure your agent's personality instead of writing raw system prompts:

```python
from ai_agents import Agent, AgentDefinition

definition = AgentDefinition(
    role="You are a property management assistant for vacation rental hosts.",
    goal="Help hosts run their vacation rentals efficiently.",
    constraints=[
        "Only answer questions about properties in the system",
        "Use tools to search documents before answering factual questions",
        "Be concise but friendly",
    ],
    personality={
        "tone": "friendly",
        "style": "practical",
    },
    examples=[
        {
            "user": "What's the checkout time?",
            "assistant": "The checkout time is 11 AM. Please ensure all guests have departed by then.",
        },
    ],
)

agent = Agent(
    definition=definition,
    provider="openai",
    api_key="sk-...",
    model="gpt-4o",
)

# See the compiled system prompt
print(agent.system_prompt)
```

## Templates

Pre-built definitions for common use cases:

```python
from ai_agents import Agent, AgentTemplates

# Generic assistant
agent = Agent(
    definition=AgentTemplates.assistant("Helper"),
    provider="anthropic",
    api_key="...",
)

# RAG assistant with document search
agent = Agent(
    definition=AgentTemplates.rag_assistant("DocBot", domain="knowledge base"),
    provider="openai",
    api_key="...",
    tools=["search_documents"],
)

# Property manager (Hostomatic)
agent = Agent(
    definition=AgentTemplates.property_manager(),
    provider="anthropic",
    api_key="...",
)
```

## Providers

```python
from ai_agents import Agent

# Anthropic
agent = Agent(role="...", provider="anthropic", api_key="...", model="claude-sonnet-4-20250514")

# OpenAI
agent = Agent(role="...", provider="openai", api_key="...", model="gpt-4o")

# Ollama (local)
agent = Agent(role="...", provider="ollama", model="llama3.1")
```

## Streaming

```python
async for chunk in agent.stream("Tell me a story"):
    print(chunk, end="", flush=True)
```

## With Database (Full Control)

For production with persistence:

```python
from ai_agents import Agent

agent = Agent(
    definition=AgentDefinition(...),
    provider="anthropic",
    api_key="...",
    conn=database_connection,  # Your db connection
    auth=auth_service,          # Your auth module
)

response = await agent.chat("Hello", user_id="user_123")
```

## Advanced: AgentRunner

For full control over the execution flow:

```python
from ai_agents import AgentRunner, get_provider, get_memory_strategy
from ai_agents.store import AgentStore, ThreadStore

# Setup
provider = get_provider("anthropic", api_key="...", model="claude-sonnet-4-20250514")
memory = get_memory_strategy("last_n", n=20)
judge = get_provider("openai", api_key="...", model="gpt-4o-mini")

runner = AgentRunner(
    conn=conn,
    auth=auth,
    provider=provider,
    memory=memory,
    judge_provider=judge,  # For injection detection
)

# Create agent in DB
agents = AgentStore(conn)
agent = await agents.create(
    name="assistant",
    system_prompt="You are helpful.",
)

# Create thread
threads = ThreadStore(conn)
thread = await threads.create(agent_id=agent["id"])

# Assign permission
await auth.assign_role(user_id, "owner", "thread", thread["id"])

# Run
response = await runner.run(thread["id"], user_id, "Hello!")
```

## Memory Strategies

```python
from ai_agents import Agent, get_memory_strategy

# Last N messages (default)
agent = Agent(..., memory_strategy="last_n", memory_params={"n": 20})

# First K + Last N (preserves initial context)
agent = Agent(..., memory_strategy="first_last", memory_params={"first": 2, "last": 10})

# Token window (fit as much as possible)
agent = Agent(..., memory_strategy="token_window", memory_params={"max_tokens": 100000})
```

## Tools

```python
from ai_agents import Agent, Tool, register_tool, ToolDefinition

class WeatherTool(Tool):
    name = "get_weather"
    description = "Get current weather for a city"
    
    async def execute(self, city: str) -> str:
        # Your implementation
        return f"Weather in {city}: Sunny, 72°F"
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"],
            },
        )

# Register
register_tool(WeatherTool())

# Use
agent = Agent(
    role="You help with weather questions",
    provider="anthropic",
    api_key="...",
    tools=["get_weather"],
)

response = await agent.chat("What's the weather in London?")
```

## Module Structure

```
ai_agents/
├── agent.py            # Simple Agent API
├── definition.py       # AgentDefinition + templates
├── runner.py           # Full AgentRunner
├── providers/          # Anthropic, OpenAI, Ollama
├── memory/             # Context strategies
├── store/              # CRUD (threads, messages, agents)
├── tools/              # Function calling
├── guardrails/         # LLM-based safety
└── workers/            # Background jobs
```
