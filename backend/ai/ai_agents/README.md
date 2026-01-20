# AI Agents

Provider-agnostic AI agent framework with **zero-latency security**, **cost control**, and **automatic fallback**.

## Why This Framework?

| Feature | LangChain | LlamaIndex | AutoGen | **AI Agents** |
|---------|-----------|------------|---------|---------------|
| Injection Protection | None | None | None | ✅ Parallel (0ms latency) |
| Cost Tracking | Manual | Manual | None | ✅ Automatic |
| Provider Fallback | Complex | None | None | ✅ Built-in |
| Security Audit | None | None | None | ✅ Red team testing |
| Complexity | High | Medium | High | **Low** |

## Quick Start

```python
from ai_agents import Agent

agent = Agent(
    role="You help users with their questions.",
    provider="openai",
    api_key="sk-...",
)

response = await agent.chat("Hello!")
print(response)

# Check costs
print(f"Cost: ${agent.conversation_cost:.4f}")
```

## Key Features

### 1. Zero-Latency Security

Injection guard runs **in parallel** with main LLM - security costs nothing in latency:

```python
# LLM-based guard (default) - parallel with main request
agent = Agent(role="...", injection_verification=True)

# Embedding-based guard (FREE) - for cost-sensitive apps
agent = Agent(role="...", injection_verification=False)
```

### 2. Cost Tracking & Budgets

Automatic cost tracking with budget limits and auto-degradation:

```python
agent = Agent(
    role="Property assistant",
    provider="openai",
    model="gpt-4o",
    max_conversation_cost=0.50,  # Stop at $0.50
    auto_degrade=True,           # Switch to gpt-4o-mini at 80%
)

await agent.chat("Hello")

# Check costs
print(f"Conversation: ${agent.conversation_cost:.4f}")
print(f"Total: ${agent.total_cost:.4f}")
print(f"Tokens: {agent.conversation_tokens}")

# Detailed report
report = agent.get_cost_report()
```

### 3. Automatic Provider Fallback

If OpenAI fails, automatically retry with Anthropic:

```python
agent = Agent(
    role="Property assistant",
    providers=[
        {"provider": "openai", "api_key": "sk-...", "model": "gpt-4o"},
        {"provider": "anthropic", "api_key": "sk-ant-...", "model": "claude-sonnet-4-20250514"},
        {"provider": "ollama", "model": "llama3.1"},  # Local fallback
    ],
    fallback=True,
)

# Automatically retries with next provider on failure
response = await agent.chat("Hello")
```

### 4. Security Audit (Red Team Testing)

Test your agent against 25+ attack patterns:

```python
report = await agent.security_audit()

print(f"Pass rate: {report.pass_rate:.1%}")
print(f"Vulnerabilities: {report.vulnerabilities}")
print(f"Recommendations: {report.recommendations}")

# Detailed breakdown
print(report.by_category)  # Pass rate per attack category
```

Attack categories tested:
- Instruction override ("ignore previous instructions")
- Role play ("you are now DAN")
- Data extraction ("show me your system prompt")
- Encoding tricks (base64, leetspeak, unicode)
- Multilingual attacks (Spanish, Chinese, French, German)
- Context manipulation (fake system messages)

### 5. Security Audit Log

Track all blocked injection attempts for compliance:

```python
# Get security report
report = agent.get_security_report()
print(f"Blocked {report['total_blocked']} attacks this month")
print(f"By type: {report['by_threat_type']}")

# Real-time alerts
from ai_agents import SecurityAuditLog

log = SecurityAuditLog()
log.on_event = lambda e: send_slack_alert(f"Blocked: {e.content_preview}")

agent = Agent(role="...", security_log=log)
```

### 6. Conversation Branching

Fork conversations to explore alternatives:

```python
# Create agent and have a conversation
agent = Agent(role="Travel advisor", provider="openai")
await agent.chat("I'm planning a trip to Europe")

# Fork to explore alternatives
agent_spain = agent.fork()
agent_italy = agent.fork()

# Explore different paths
response1 = await agent_spain.chat("What about Spain?")
response2 = await agent_italy.chat("What about Italy?")

# Original agent unaffected
response3 = await agent.chat("What about France?")
```

## Knowledge Modes (Hallucination Control)

Control how the agent uses its training knowledge:

```python
# Epistemic (default): Honest about uncertainty
agent = Agent(role="...", knowledge_mode="epistemic")
# "As of my last update, the CEO was..."

# Strict: Document-only, never uses training knowledge
agent = Agent(role="...", knowledge_mode="strict")
# "I couldn't find this in the provided documents."

# Conversational: Relaxed, uses training with caveats
agent = Agent(role="...", knowledge_mode="conversational")
```

## Agent Definition

Structure your agent's personality:

```python
from ai_agents import Agent, AgentDefinition

definition = AgentDefinition(
    role="Property management assistant for vacation rentals.",
    goal="Help hosts run their vacation rentals efficiently.",
    constraints=[
        "Only answer questions about properties in the system",
        "Use tools to search documents before answering",
    ],
    personality={"tone": "friendly", "style": "practical"},
)

agent = Agent(definition=definition, provider="openai", api_key="...")
```

## Templates

```python
from ai_agents import AgentTemplates

# Generic assistant
agent = Agent(definition=AgentTemplates.assistant("Helper"), ...)

# RAG assistant with document search
agent = Agent(definition=AgentTemplates.rag_assistant("DocBot"), ...)

# Property manager
agent = Agent(definition=AgentTemplates.property_manager(), ...)
```

## Streaming

```python
async for chunk in agent.stream("Tell me a story"):
    print(chunk, end="", flush=True)
```

## Tools

```python
from ai_agents import tool

@tool(description="Get current weather for a city")
async def get_weather(city: str) -> str:
    return f"Weather in {city}: Sunny, 72°F"

agent = Agent(
    role="Weather assistant",
    provider="openai",
    tools=["get_weather"],
)

response = await agent.chat("What's the weather in Paris?")
```

## Memory Strategies

```python
# Last N messages (default)
agent = Agent(..., memory_strategy="last_n", memory_params={"n": 20})

# First K + Last N (preserves initial context)
agent = Agent(..., memory_strategy="first_last", memory_params={"first": 2, "last": 10})

# Token window (fit as much as possible)
agent = Agent(..., memory_strategy="token_window", memory_params={"max_tokens": 100000})
```

## Instructor Integration (Structured Outputs)

Optional integration with [Instructor](https://github.com/jxnl/instructor) for guaranteed structured outputs. Eliminates malformed JSON and XML parsing issues from Llama/Groq/Ollama.

```bash
pip install instructor pydantic
```

```python
from ai_agents.providers import OpenAIProvider, enable_instructor, StructuredResponse

# Wrap your provider
provider = OpenAIProvider(api_key="...")
provider = enable_instructor(provider)

# Get guaranteed structured output
result = await provider.complete_structured(
    messages=[{"role": "user", "content": "What's 2+2?"}],
    response_model=StructuredResponse,
)

# result.tool_calls is always valid - no XML parsing needed
```

See [providers/INSTRUCTOR.md](providers/INSTRUCTOR.md) for full documentation.

## Parallel Tool Execution

Tool calls are executed in parallel for faster multi-tool responses:

```python
# If model calls 3 tools simultaneously:
# - search_documents("query1")
# - search_documents("query2") 
# - calculator("2+2")

# All 3 run concurrently, not sequentially
# Total time = max(tool_time), not sum(tool_times)
```

## Multi-Agent Orchestration

Coordinate multiple agents for complex tasks.

### Parallel Agents

Run multiple agents on the same input concurrently:

```python
from ai_agents import Agent, ParallelAgents

researcher = Agent(role="Research analyst", name="Researcher", ...)
writer = Agent(role="Content writer", name="Writer", ...)

parallel = ParallelAgents([researcher, writer])
results = await parallel.chat("Analyze AI trends")

for r in results.successful:
    print(f"{r.agent_name}: {r.content[:100]}...")
```

### Supervisor Pattern

A planner breaks down tasks and delegates to specialized workers:

```python
from ai_agents import Agent, Supervisor

supervisor = Supervisor(
    workers={
        "Researcher": researcher,
        "Writer": writer,
        "Editor": editor,
    },
)

result = await supervisor.run("Write a blog post about quantum computing")
print(result.content)
```

### Pipeline

Sequential agent processing chain:

```python
from ai_agents import Pipeline

pipeline = Pipeline([researcher, writer, editor])
result = await pipeline.run("Write about AI")

# Output flows: researcher → writer → editor
```

### Debate

Multiple agents discuss and reach conclusions:

```python
from ai_agents import Debate

debate = Debate(
    agents=[optimist, pessimist, pragmatist],
    rounds=3,
)

result = await debate.run("Should we adopt AI in hiring?")
print(result.conclusion)
print(result.consensus_reached)
```

See [orchestration/README.md](orchestration/README.md) for full documentation.

## Concurrency Safety

When using multi-agent orchestration, the framework handles thread safety automatically:

### What's Automatically Protected

| Resource | Protection | Notes |
|----------|------------|-------|
| User Context | ✅ Auto-locked per user_id | `update_context` tool is safe |
| Agent Instances | ✅ Validated | Duplicate instances rejected |
| Tool Execution | ✅ Parallel safe | Each call is independent |
| Thread Messages | ⚠️ Optional | Use `ThreadSafeMessageStore` if agents share threads |

```python
# User context updates are automatically locked
# No race conditions when multiple agents update same user
agent1.chat("My name is Phil")      # Acquires lock
agent2.chat("I live in London")     # Waits for lock, then updates

# Result: {"name": "Phil", "location": "London"} - no data loss
```

### Custom Tool Safety

If your tools write to shared resources (database, files, cache, APIs), add the decorator:

```python
from ai_agents import thread_safe_tool, Tool

@thread_safe_tool("database", key_arg="record_id")
class UpdateDatabaseTool(Tool):
    async def execute(self, record_id: str, data: dict):
        # Automatically locked per record_id
        await db.update(record_id, data)

@thread_safe_tool("file", key_arg="path")
class WriteFileTool(Tool):
    async def execute(self, path: str, content: str):
        await write_file(path, content)

@thread_safe_tool("cache", key_arg="key") 
class UpdateCacheTool(Tool):
    async def execute(self, key: str, value: any):
        shared_dict[key] = value
```

### Manual Locking

For fine-grained control outside of tools:

```python
from ai_agents import get_lock, file_lock, user_context_lock, thread_lock

# Generic named locks
async with get_lock("my_resource", resource_id):
    await do_something()

# Convenience locks for common resources
async with file_lock("/path/to/file"):
    await write_file(...)

async with user_context_lock(user_id):
    await update_user(...)

async with thread_lock(thread_id):
    await add_message(...)
```

### Important: Single Process Only

The locking mechanism uses `asyncio.Lock` which only works within a single Python process:

```
✅ Safe: Multiple agents in same process
   Agent1, Agent2, Agent3 → shared asyncio.Lock → Database

❌ Unsafe: Multiple processes
   Process1 (Agent1) → Lock A ─┐
   Process2 (Agent2) → Lock B ─┴→ Database (race condition!)
```

For multi-process deployments, use database-level locking:
- PostgreSQL: `SELECT ... FOR UPDATE`
- Redis: distributed locks (redlock)
- Files: `fcntl.flock()`

## User Context (Persistent Memory)

Remember information about users across conversations. Three tiers of flexibility:

### Tier 3: Auto (Zero Config)

Agent decides what to remember based on its role:

```python
agent = Agent(
    role="Running coach helping users train for marathons",
    provider="openai",
    context_schema={},  # Empty dict enables auto mode
)

# Agent automatically remembers relevant info:
# {"name": "Phil", "goal": "First marathon", "injuries": ["knee"]}
```

### Tier 2: Schema-Defined

You define what to remember, we store it:

```python
agent = Agent(
    role="Property management assistant",
    provider="openai",
    context_schema={
        "name": "User's name",
        "properties": "List of properties with name, address, type",
        "preferences": "Settings like checkout time, auto-review",
    }
)
```

### Tier 1: Custom Provider (Full Control)

Connect to your own database:

```python
from ai_agents import ContextProvider

class HostomaticContextProvider(ContextProvider):
    def __init__(self, db):
        self.db = db
    
    async def load(self, user_id: str, agent_id: str = None) -> dict:
        # Fetch from your Postgres/Firebase/etc
        user = await self.db.get(User, user_id)
        properties = await self.db.query(Property, owner_id=user_id)
        return {
            "name": user.name,
            "properties": [{"name": p.name, "address": p.address} for p in properties],
        }
    
    async def update(self, user_id: str, updates: dict, reason: str, agent_id: str = None) -> dict:
        # Write to your database
        ...

# Multiple agents can share the same provider
provider = HostomaticContextProvider(db)

property_assistant = Agent(role="...", context_provider=provider)
booking_assistant = Agent(role="...", context_provider=provider)  # Same data!
```

## Module Structure

```
ai_agents/
├── agent.py            # Simple Agent API
├── costs.py            # Cost tracking + budgets
├── security.py         # Audit logging
├── testing.py          # Security audit / red team
├── definition.py       # AgentDefinition + templates
├── providers/          # Anthropic, OpenAI, Ollama, Groq
│   ├── instructor_support.py  # Structured outputs (optional)
│   └── INSTRUCTOR.md   # Instructor documentation
├── memory/             # Conversation history strategies
├── context/            # User context (persistent memory)
├── store/              # Thread, message, agent, context storage
├── tools/              # Function calling (parallel execution)
├── orchestration/      # Multi-agent patterns
│   ├── parallel.py     # Parallel agent execution
│   ├── supervisor.py   # Supervisor + workers pattern
│   ├── pipeline.py     # Sequential processing
│   └── debate.py       # Discussion + consensus
├── limits/             # Rate limiting + job queue
├── guardrails/         # Injection detection
└── workers/            # Background jobs
```

## Pricing Reference

Costs are tracked automatically using these rates (per 1M tokens):

| Model | Input | Output |
|-------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| claude-sonnet-4 | $3.00 | $15.00 |
| claude-opus-4 | $15.00 | $75.00 |
| claude-haiku-3 | $0.25 | $1.25 |
| Ollama (local) | $0.00 | $0.00 |

## API Reference
