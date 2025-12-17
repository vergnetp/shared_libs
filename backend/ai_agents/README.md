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

### @tool Decorator (Easy Way)

```python
from ai_agents import tool, Agent

@tool(description="Get current weather for a city")
async def get_weather(city: str) -> str:
    return f"Weather in {city}: Sunny, 72°F"

@tool(description="Calculate math expression")
def calculate(expression: str) -> str:
    return str(eval(expression))

# Use in agent
agent = Agent(
    role="You help with weather and math",
    provider="anthropic",
    api_key="...",
    tools=["get_weather", "calculate"],
)

response = await agent.chat("What's 25 * 4?")
```

### Class-Based (Full Control)

```python
from ai_agents import Tool, register_tool, ToolDefinition

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

## User Memory (Cross-Thread Facts)

```python
from ai_agents.store import UserMemoryStore, UserMemoryExtractor

memory = UserMemoryStore(conn)

# Store facts
await memory.set("user_123", "name", "Phil")
await memory.set("user_123", "timezone", "Europe/London")
await memory.set("user_123", "style", "concise", category="preferences")

# Retrieve
name = await memory.get("user_123", "name")
all_facts = await memory.get_all("user_123")

# Format for system prompt
context = memory.format_for_prompt(all_facts)

# Auto-extract from conversations
extractor = UserMemoryExtractor(provider, memory)
await extractor.extract_and_save("user_123", messages)
```

## Thread Features

```python
from ai_agents.store import ThreadStore

threads = ThreadStore(conn)

# Archive
await threads.archive(thread_id)
await threads.unarchive(thread_id)
archived = await threads.list_archived(user_id="user_123")

# Fork (copy thread + messages)
new_thread = await threads.fork(thread_id, title="Copy")

# Branch from specific message
branch = await threads.branch(thread_id, from_message_id="msg_xyz")

# Stats
stats = await threads.get_stats(thread_id)
# {"message_count": 42, "total_bytes": 15000, "forked_from": None}

# Search
results = await threads.search("project", user_id="user_123")
```

## Module Structure

```
ai_agents/
├── agent.py            # Simple Agent API
├── definition.py       # AgentDefinition + templates
├── runner.py           # Full AgentRunner
├── providers/          # Anthropic, OpenAI, Ollama
├── memory/             # Context strategies
├── store/
│   ├── threads.py      # + fork, archive, stats
│   ├── messages.py
│   ├── agents.py
│   └── user_memory.py  # Cross-thread facts
├── tools/
│   ├── decorator.py    # @tool decorator
│   └── builtin/
├── limits/             # Rate limiting + job queue
│   ├── rate_limiter.py # RPM/TPM with Redis option
│   └── job_queue.py    # Background jobs
├── guardrails/         # LLM-based safety
└── workers/            # Background jobs
```

## Rate Limiting (Optional Redis)

```python
from ai_agents import RateLimiter, get_rate_limiter

# In-memory (default) - good for single process
limiter = RateLimiter(rpm=60, tpm=100_000)

# Check before request
wait_time = await limiter.check("openai:gpt-4o", estimated_tokens=1000)
if wait_time > 0:
    await asyncio.sleep(wait_time)

# Record after request
await limiter.record("openai:gpt-4o", actual_tokens=1500)

# Or use acquire() which waits + records
await limiter.acquire("openai:gpt-4o", estimated_tokens=1000)

# Get limiter configured for specific provider/model
limiter = get_rate_limiter("anthropic", "claude-sonnet-4-20250514")
```

### With Redis (Multi-Process)

```python
from ai_agents import RateLimiter, RedisBackend
from processing.queue import QueueRedisConfig  # Your wrapper

redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
backend = RedisBackend(redis_config)

limiter = RateLimiter(rpm=60, tpm=100_000, backend=backend)
# Now rate limits are shared across all processes
```

## Job Queue (Optional Redis)

```python
from ai_agents import JobQueue

# In-memory (default)
queue = JobQueue()

# Register handlers
@queue.handler("summarize_thread")
async def handle_summarize(payload):
    thread_id = payload["thread_id"]
    # ... do summarization
    return {"summary": "..."}

@queue.handler("generate_title")
async def handle_title(payload):
    # ...
    return {"title": "..."}

# Enqueue jobs
job_id = await queue.enqueue("summarize_thread", {"thread_id": "abc"})

# Check status
job = await queue.get_status(job_id)
print(job.status)  # pending, processing, completed, failed

# Start background worker
await queue.start_worker()

# Stop worker
await queue.stop_worker()
```

### With Redis

```python
from ai_agents import JobQueue, RedisQueueBackend
from processing.queue import QueueRedisConfig

redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
backend = RedisQueueBackend(redis_config, queue_name="ai_agents")

queue = JobQueue(backend=backend)
# Jobs persist across restarts, shared across workers
```
