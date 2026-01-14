# Multi-Agent Orchestration

Patterns for coordinating multiple AI agents.

## Quick Start

```python
from ai_agents import Agent
from ai_agents.orchestration import ParallelAgents, Supervisor, Pipeline, Debate

# Create some agents
researcher = Agent(role="Research analyst", name="Researcher", provider="openai", api_key="...")
writer = Agent(role="Content writer", name="Writer", provider="openai", api_key="...")
editor = Agent(role="Editor", name="Editor", provider="openai", api_key="...")
```

## Patterns

### 1. Parallel Agents

Run multiple agents concurrently on the same input:

```python
from ai_agents.orchestration import ParallelAgents, parallel_chat

# Option 1: Class
parallel = ParallelAgents([researcher, writer, editor])
results = await parallel.chat("Analyze AI trends")

for r in results.successful:
    print(f"{r.agent_name}: {r.content[:100]}...")

# Option 2: Function
results = await parallel_chat(
    agents=[researcher, writer, editor],
    message="Analyze AI trends",
)

# Format for another agent
context = results.to_context(format="xml")
```

**Use when**: You need multiple perspectives on the same input (research, analysis, review).

### 2. Supervisor

A planner breaks down tasks and delegates to workers:

```python
from ai_agents.orchestration import Supervisor, SupervisorConfig

supervisor = Supervisor(
    workers={
        "Researcher": researcher,
        "Writer": writer,
        "Editor": editor,
    },
    config=SupervisorConfig(
        mode="selective",  # Planner chooses workers
        max_iterations=2,
    ),
)

result = await supervisor.run("Write a blog post about quantum computing")

print(result.content)  # Final synthesized output
print(result.plan)     # What was planned
print(result.worker_results)  # Individual outputs
```

**Use when**: Complex tasks need breakdown and coordination.

### 3. Pipeline

Sequential processing chain:

```python
from ai_agents.orchestration import Pipeline

pipeline = Pipeline([
    researcher,  # Step 1: Research
    writer,      # Step 2: Write from research
    editor,      # Step 3: Edit draft
])

result = await pipeline.run("Write about AI in healthcare")

for step in result.steps:
    print(f"{step.agent_name}: {step.duration_ms}ms")
```

With transformers between steps:

```python
pipeline = Pipeline(
    agents=[researcher, writer, editor],
    transformers={
        1: lambda x: f"Write an article based on:\\n{x}",
        2: lambda x: f"Edit and improve:\\n{x}",
    }
)
```

**Use when**: Linear processing with clear stages.

### 4. Debate

Multiple agents discuss and reach conclusions:

```python
from ai_agents.orchestration import Debate

debate = Debate(
    agents=[optimist, pessimist, pragmatist],
    rounds=3,
)

result = await debate.run("Should we adopt AI in hiring?")

print(result.conclusion)
print(result.consensus_reached)
print(result.to_transcript())
```

Pre-built debate setups:

```python
from ai_agents.orchestration.debate import create_pros_cons_debate

debate = create_pros_cons_debate(provider="openai", api_key="...")
result = await debate.run("Remote work: permanent or temporary?")
```

**Use when**: Exploring topic from multiple angles, generating balanced analysis.

## Pre-built Teams

### Research Team

```python
from ai_agents.orchestration.supervisor import create_research_team

team = create_research_team(provider="openai", api_key="sk-...")
result = await team.run("What are the implications of quantum computing for cryptography?")
```

Includes: Researcher, Analyst, Writer, Critic

### Expert Panel

```python
from ai_agents.orchestration.debate import create_expert_panel

panel = create_expert_panel(
    provider="anthropic",
    api_key="...",
    expert_roles=[
        "Economist focusing on market dynamics",
        "Sociologist focusing on social impact", 
        "Technologist focusing on feasibility",
    ],
)

result = await panel.run("Impact of universal basic income")
```

## Combining Patterns

```python
# Research team feeds into debate
research_result = await research_team.run("Current state of AI regulation")

debate = Debate(agents=[policy_expert, industry_expert, ethics_expert])
debate_result = await debate.run(
    topic="How should AI be regulated?",
    context=research_result.content,
)

# Pipeline with parallel step
parallel_analysts = ParallelAgents([technical_analyst, market_analyst])

async def analyze_step(input_text):
    results = await parallel_analysts.chat(input_text)
    return results.to_context()

# Use in pipeline via transformer
pipeline = Pipeline(
    agents=[researcher, synthesizer],
    transformers={
        1: lambda x: f"Analysis:\\n{await analyze_step(x)}\\n\\nSynthesize:",
    }
)
```

## Configuration

### SupervisorConfig

```python
from ai_agents.orchestration import SupervisorConfig, WorkerSelectionMode

config = SupervisorConfig(
    mode=WorkerSelectionMode.SELECTIVE,  # or ALL, SEQUENTIAL
    max_workers_per_step=5,
    max_iterations=3,
    allow_replanning=True,
    planning_timeout=60.0,
    worker_timeout=120.0,
    synthesis_timeout=60.0,
    require_all_workers=False,
    min_successful_workers=1,
)
```

### Timeouts

All patterns support timeouts:

```python
parallel = ParallelAgents(agents, timeout=120.0)
pipeline = Pipeline(agents, step_timeout=60.0)
debate = Debate(agents, round_timeout=180.0)
supervisor = Supervisor(workers, config=SupervisorConfig(worker_timeout=120.0))
```

## Cost Tracking

All patterns track costs:

```python
result = await supervisor.run("Complex task")
print(f"Total cost: ${result.total_cost:.4f}")
print(f"Duration: {result.total_duration_ms}ms")
```

## Error Handling

```python
# Parallel: continues even if some fail
results = await parallel.chat("message")
if not results.all_success:
    for r in results.failed:
        print(f"{r.agent_name} failed: {r.error}")

# Pipeline: stops on first error (configurable)
pipeline = Pipeline(agents, stop_on_error=False)  # Continue on errors

# Supervisor: configurable
config = SupervisorConfig(
    require_all_workers=False,  # Don't fail if some workers fail
    min_successful_workers=2,   # Need at least 2 to succeed
)
```

## Concurrency Safety

Multi-agent orchestration handles concurrency automatically, but there are important considerations:

### Separate Agent Instances Required

```python
# WRONG - same instance causes state corruption
agent = Agent(role="...", ...)
parallel = ParallelAgents([agent, agent, agent])  # Raises ValueError!

# RIGHT - separate instances
parallel = ParallelAgents([
    Agent(role="Researcher", name="Researcher", ...),
    Agent(role="Writer", name="Writer", ...),
    Agent(role="Analyst", name="Analyst", ...),
])
```

### Shared Resources Are Locked

When multiple agents update the same user context, locks prevent race conditions:

```python
# Both agents updating same user - automatically serialized
agent1.chat("Remember my name is Phil")    # Gets lock
agent2.chat("Remember I live in London")   # Waits for lock

# Updates happen atomically, no data loss
```

### Custom Tool Safety

If your tools write to shared resources, make them thread-safe:

```python
from ai_agents import thread_safe_tool, get_lock

# Option 1: Decorator
@thread_safe_tool("my_resource", key_arg="resource_id")
class MyTool(Tool):
    async def execute(self, resource_id: str, data: dict):
        # Automatically locked per resource_id
        ...

# Option 2: Manual locking
class MyTool(Tool):
    async def execute(self, file_path: str, content: str):
        async with get_lock("file", file_path):
            await write_file(file_path, content)
```

### When to Use ThreadSafeMessageStore

If multiple agents write to the same thread:

```python
from ai_agents import ThreadSafeMessageStore

# Standard store - fine for single agent per thread
store = MessageStore(conn)

# Thread-safe store - for multi-agent same thread
store = ThreadSafeMessageStore(conn)
```

### Important: Single Process Only

The locking mechanism uses `asyncio.Lock` which works within a single Python process:

```
✅ Safe: Multiple agents in same process
   Agent1, Agent2, Agent3 → shared asyncio.Lock → Resource

❌ Unsafe: Multiple separate processes  
   Process1 (Agent1) → Lock A ─┐
   Process2 (Agent2) → Lock B ─┴→ Same Resource (race condition!)
```

For multi-process/distributed deployments, use external locking:
- Database: `SELECT ... FOR UPDATE` 
- Redis: distributed locks (redlock)
- Files: OS-level `fcntl.flock()`
