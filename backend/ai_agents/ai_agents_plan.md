# AI Agents Module - Complete Design Plan

A provider-agnostic Python module for creating and managing AI agents with persistent state, built on top of existing RAG infrastructure.

---

## 1. Executive Summary

### What We're Building

An **"OpenAI Assistants API, but provider-agnostic and self-hosted"** that:

- Works with **any LLM provider** (OpenAI, Anthropic, Ollama, etc.)
- **Our database is the source of truth** (not OpenAI's 60-day expiring threads)
- Integrates with **existing RAG system** (OpenSearch, embeddings, reranker)
- Uses **Redis for queuing and rate limiting** from day one
- Supports **multiple memory strategies** (sliding window, summarize, vector)
- Enables **seamless provider switching** (cloud today, local Ollama tomorrow)

### Tech Stack

| Component | Technology | Status |
|-----------|------------|--------|
| Vector Search | OpenSearch | ✅ Existing |
| Embeddings | MiniLM (model_hub) | ✅ Existing |
| Reranking | Cross-encoder + MMR | ✅ Existing |
| Document Ingestion | chunk_creator + doc_ingestor | ✅ Existing |
| Database | PostgreSQL | ✅ Existing |
| Queue & Rate Limiting | Redis | 🔨 New |
| Agent Framework | This module | 🔨 New |

---

## 2. Core Philosophy

### The Problem with OpenAI Assistants

| Aspect | OpenAI Assistants | Problem |
|--------|-------------------|---------|
| Thread storage | Their servers | 60-day expiry, no control |
| Thread listing | Not available | You track IDs yourself |
| Cross-thread memory | Not available | Build yourself |
| Provider lock-in | 100% | Can't switch to Anthropic/Ollama |
| Memory strategy | `auto` or `last_messages` only | No summarize, no vector |
| Data ownership | Theirs | Compliance, privacy concerns |

### Our Solution

```
┌─────────────────────────────────────────────────────────────┐
│                    OUR SYSTEM (Full Control)                 │
├─────────────────────────────────────────────────────────────┤
│  PostgreSQL: Agents, Threads, Messages (source of truth)    │
│  Redis: Rate limiting, job queue, caching                   │
│  OpenSearch: Document RAG (existing)                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Re-inject context as needed
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              LLM PROVIDER (Compute Only)                     │
├─────────────────────────────────────────────────────────────┤
│  OpenAI / Anthropic / Ollama / Groq / etc.                  │
│  • Run inference                                            │
│  • Return response                                          │
│  • OpenAI thread = disposable cache (optional)              │
└─────────────────────────────────────────────────────────────┘
```

**Key insight:** OpenAI Assistants API becomes an **optional optimization**, not a dependency. We can use it when convenient (native tool calling, token caching) but don't rely on it for persistence.

---

## 3. Architecture Overview

### Module Structure

```
ai_agents/
├── __init__.py
├── definition.py              # AgentDefinition dataclass
├── agent.py                   # Main Agent class
├── runner.py                  # Execution loop
├── store.py                   # AgentStore (PostgreSQL operations)
│
├── providers/
│   ├── __init__.py
│   ├── base.py                # Abstract provider interface
│   ├── openai_assistants.py   # Uses Assistants API (optional)
│   ├── openai_completions.py  # Stateless completions
│   ├── anthropic.py           # Stateless
│   ├── ollama.py              # Stateless + tool injection
│   └── registry.py            # Model limits registry
│
├── memory/
│   ├── __init__.py
│   ├── base.py                # Abstract memory interface
│   ├── strategies.py          # last_messages, summarize, vector, etc.
│   └── context.py             # Context window management
│
├── tools/
│   ├── __init__.py
│   ├── decorator.py           # @tool decorator
│   ├── registry.py            # Tool registry
│   ├── parser.py              # Parse tool calls from text
│   └── builtin.py             # Built-in RAG tools
│
├── queue/
│   ├── __init__.py
│   ├── redis_queue.py         # Redis job queue
│   ├── rate_limiter.py        # Redis-based rate limiting
│   └── token_counter.py       # Token counting utilities
│
└── utils/
    ├── __init__.py
    └── exceptions.py
```

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLIENT                                      │
│                     agent.chat("What's the refund policy?")              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           AGENT RUNNER                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Load agent definition + thread history from PostgreSQL              │
│  2. Apply memory strategy (fit context window)                          │
│  3. Check rate limits (Redis)                                           │
│  4. Call LLM provider                                                   │
│  5. Execute tools if requested (loop)                                   │
│  6. Save messages to PostgreSQL                                         │
│  7. Return response                                                     │
└─────────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   PostgreSQL    │  │     Redis       │  │   LLM Provider  │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ • agents        │  │ • rate limits   │  │ • OpenAI        │
│ • threads       │  │ • job queue     │  │ • Anthropic     │
│ • messages      │  │ • token counts  │  │ • Ollama        │
│ • user_memory   │  │ • caching       │  │ • Groq          │
└─────────────────┘  └─────────────────┘  └─────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     EXISTING RAG (OpenSearch)                            │
├─────────────────────────────────────────────────────────────────────────┤
│  vectordb.py → searcher.py → reranker.py → doc_ingestor.py              │
│  (Wrapped as agent tools)                                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Database Schema

### PostgreSQL Tables

```sql
-- ============================================
-- AGENTS (like OpenAI's assistants.create)
-- ============================================
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    
    -- Definition (compiles to system prompt)
    role            TEXT NOT NULL,
    goal            TEXT,
    constraints     JSONB,              -- ["constraint 1", "constraint 2"]
    personality     JSONB,              -- {"tone": "friendly", "style": "concise"}
    examples        JSONB,              -- [{"user": "...", "assistant": "..."}]
    
    -- Tools
    tools           JSONB,              -- ["search_documents", "ask_documents"]
    
    -- Provider config
    provider        TEXT NOT NULL,      -- "openai", "anthropic", "ollama"
    model           TEXT NOT NULL,      -- "gpt-4o", "claude-3.5-sonnet"
    
    -- Memory config
    memory_strategy TEXT DEFAULT 'last_messages',
    memory_config   JSONB,              -- {"max_messages": 50, "preserve_first": 3}
    
    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    created_by      TEXT,               -- user_id who created
    is_active       BOOLEAN DEFAULT TRUE
);

-- ============================================
-- THREADS (like OpenAI's threads.create)
-- ============================================
CREATE TABLE threads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID REFERENCES agents(id) ON DELETE CASCADE,
    user_id         TEXT,               -- For multi-tenant
    
    -- Size tracking
    total_bytes     BIGINT DEFAULT 0,
    message_count   INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ DEFAULT NOW(),
    
    -- OpenAI cache (optional, can be null/stale)
    openai_thread_id TEXT,
    openai_thread_created_at TIMESTAMPTZ,
    
    -- Status
    archived        BOOLEAN DEFAULT FALSE,
    metadata        JSONB
);

-- ============================================
-- MESSAGES (like OpenAI's messages.create)
-- ============================================
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id       UUID REFERENCES threads(id) ON DELETE CASCADE,
    
    -- Content
    role            TEXT NOT NULL,      -- "system", "user", "assistant", "tool"
    content         TEXT,
    
    -- Tool calls (for assistant messages)
    tool_calls      JSONB,              -- [{"id": "...", "name": "...", "arguments": {...}}]
    
    -- Tool response (for tool messages)
    tool_call_id    TEXT,
    tool_name       TEXT,
    
    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    token_count     INTEGER,            -- Cached token count
    metadata        JSONB
);

-- ============================================
-- USER MEMORY (cross-thread, OpenAI doesn't have this)
-- ============================================
CREATE TABLE user_memory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    agent_id        UUID REFERENCES agents(id) ON DELETE CASCADE,
    
    key             TEXT NOT NULL,
    value           TEXT,
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(user_id, agent_id, key)
);

-- ============================================
-- INDEXES
-- ============================================
CREATE INDEX idx_threads_agent ON threads(agent_id);
CREATE INDEX idx_threads_user ON threads(user_id);
CREATE INDEX idx_threads_last_used ON threads(last_used_at);
CREATE INDEX idx_messages_thread ON messages(thread_id);
CREATE INDEX idx_messages_created ON messages(created_at);
CREATE INDEX idx_user_memory_user ON user_memory(user_id, agent_id);
```

### Size Limits

| Level | Limit | Rationale |
|-------|-------|-----------|
| Thread size | 50 MB | Prevent runaway threads |
| User total | 500 MB | Fair usage per user |
| Tenant total | Configurable | Billing tier (Hostomatic) |

---

## 5. Agent Definition

### Data Structure

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AgentDefinition:
    """
    Defines an agent's identity, goals, constraints, and behavior.
    Compiles to a system prompt for LLM providers.
    """
    name: str
    role: str                                    # "You are a chess teacher"
    goal: Optional[str] = None                   # "Help users improve..."
    constraints: list[str] = field(default_factory=list)
    personality: Optional[dict] = None           # {"tone": "friendly"}
    examples: list[dict] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    
    def compile(self) -> str:
        """Generate system prompt from definition."""
        sections = []
        
        # Role (required)
        sections.append(f"# Role\n{self.role}")
        
        # Goal
        if self.goal:
            sections.append(f"# Goal\n{self.goal}")
        
        # Constraints
        if self.constraints:
            items = "\n".join(f"- {c}" for c in self.constraints)
            sections.append(f"# Constraints\n{items}")
        
        # Personality
        if self.personality:
            tone = self.personality.get("tone", "professional")
            style = self.personality.get("style", "")
            sections.append(f"# Communication Style\nTone: {tone}")
            if style:
                sections.append(f"Style: {style}")
        
        # Examples (few-shot)
        if self.examples:
            examples_text = self._format_examples()
            sections.append(f"# Example Interactions\n{examples_text}")
        
        return "\n\n".join(sections)
```

### Usage Example

```python
hostomatic_assistant = AgentDefinition(
    name="Property Assistant",
    role="You are a helpful property management assistant for vacation rental hosts.",
    goal="Help hosts manage their properties efficiently by answering questions, "
         "searching documents, and providing actionable advice.",
    constraints=[
        "Always cite which document information comes from",
        "If you're unsure, say so rather than guessing",
        "Respect guest privacy - don't share personal details",
        "For legal or tax questions, recommend consulting a professional",
    ],
    personality={"tone": "friendly and professional", "style": "concise but thorough"},
    tools=["search_documents", "ask_documents", "list_properties"],
)
```

---

## 6. Provider Abstraction

### Provider Selection Logic

```
┌─────────────────────────────────────────────────────────────┐
│ Is provider OpenAI AND memory strategy native-compatible?   │
│ (last_messages or auto)                                     │
└─────────────────────────────────────────────────────────────┘
              │
       ┌──────┴──────┐
       ▼             ▼
    [YES]          [NO]
       │             │
       ▼             ▼
┌─────────────┐  ┌─────────────────────┐
│ Use OpenAI  │  │ Use Stateless       │
│ Assistants  │  │ (re-inject every    │
│ API         │  │  call)              │
└─────────────┘  └─────────────────────┘
```

### Base Provider Interface

```python
from abc import ABC, abstractmethod

class BaseProvider(ABC):
    """Abstract interface for LLM providers."""
    
    @abstractmethod
    async def run(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> ProviderResponse:
        ...
    
    @property
    @abstractmethod
    def supports_native_tools(self) -> bool:
        ...
```

### Tool Injection for Non-Native Providers (Ollama)

```python
TOOL_INJECTION_TEMPLATE = """
## Available Tools

You have access to the following tools:

{tool_definitions}

To use a tool, respond with EXACTLY this XML format:

<tool_call>
<n>tool_name_here</n>
<arguments>
{{"param1": "value1", "param2": "value2"}}
</arguments>
</tool_call>

Rules:
- Call only ONE tool at a time
- Wait for the result before calling another tool
- If you don't need a tool, respond normally without XML tags
"""
```

---

## 7. Model Limits Registry

```python
@dataclass
class ModelLimits:
    context_window: int      # Total tokens (input + output)
    max_output: int          # Max output tokens
    rpm: int                 # Requests per minute
    tpm: int                 # Tokens per minute
    supports_tools: bool
    supports_vision: bool
    cost_per_1m_input: float
    cost_per_1m_output: float


MODEL_REGISTRY = {
    "openai": {
        "gpt-4.1": ModelLimits(1_000_000, 32_000, 500, 30_000, True, True, 2.00, 8.00),
        "gpt-4o": ModelLimits(128_000, 16_000, 500, 30_000, True, True, 2.50, 10.00),
        "gpt-4o-mini": ModelLimits(128_000, 16_000, 500, 30_000, True, True, 0.15, 0.60),
    },
    "anthropic": {
        "claude-sonnet-4-20250514": ModelLimits(200_000, 8_000, 50, 40_000, True, True, 3.00, 15.00),
        "claude-3-5-sonnet-20241022": ModelLimits(200_000, 8_000, 50, 40_000, True, True, 3.00, 15.00),
        "claude-3-haiku-20240307": ModelLimits(200_000, 4_000, 50, 40_000, True, True, 0.25, 1.25),
    },
    "ollama": {
        "llama3.3:70b": ModelLimits(128_000, 2_000, 999_999, 999_999_999, False, False, 0, 0),
        "qwen2.5:72b": ModelLimits(128_000, 8_000, 999_999, 999_999_999, False, False, 0, 0),
    },
    "deepseek": {
        "deepseek-chat": ModelLimits(64_000, 8_000, 60, 100_000, True, False, 0.14, 0.28),
    },
    "google": {
        "gemini-2.0-flash": ModelLimits(1_000_000, 8_000, 60, 4_000_000, True, True, 0.075, 0.30),
        "gemini-2.5-pro": ModelLimits(1_000_000, 64_000, 60, 4_000_000, True, True, 1.25, 5.00),
    },
}
```

---

## 8. Memory Strategies

| Strategy | Description | Use Case | OpenAI Native |
|----------|-------------|----------|---------------|
| `last_messages` | Keep last N messages | General chat | ✅ Yes |
| `first_last` | Keep first M + last N | Preserve setup context | ❌ No |
| `summarize` | Compress old → summary | Very long conversations | ❌ No |
| `vector` | Retrieve relevant chunks | Knowledge-heavy agents | ❌ No |

```python
class MemoryStrategy(ABC):
    @abstractmethod
    def prepare_messages(
        self,
        system_prompt: str,
        history: list[Message],
        user_input: str,
        limits: ModelLimits,
    ) -> list[Message]:
        ...
    
    @property
    @abstractmethod
    def is_openai_native_compatible(self) -> bool:
        ...
```

---

## 9. Redis Integration

### Why Redis from Day One

| Requirement | Without Redis | With Redis |
|-------------|---------------|------------|
| Rate limiting across workers | ❌ Can't coordinate | ✅ Shared state |
| Job queue for long tasks | ❌ Block request | ✅ Background processing |
| Token counting across requests | ❌ Per-process only | ✅ Accurate TPM tracking |
| Scale to multiple servers | ❌ Impossible | ✅ Ready |

### Redis Keys

```python
# Rate limiting (sorted sets with timestamps)
RATE_KEY_RPM = "ratelimit:{provider}:{model}:rpm"
RATE_KEY_TPM = "ratelimit:{provider}:{model}:tpm"

# Job queue
JOB_QUEUE = "jobs:agent_runs"
JOB_RESULTS = "jobs:results:{job_id}"

# Caching
CACHE_THREAD = "cache:thread:{thread_id}"
CACHE_AGENT = "cache:agent:{agent_id}"
```

### Rate Limiter

```python
class RedisRateLimiter:
    """Sliding window rate limiter using Redis sorted sets."""
    
    async def wait_if_needed(self, estimated_tokens: int) -> float:
        """Wait if rate limits would be exceeded. Returns seconds waited."""
        ...
    
    async def record_request(self, tokens_used: int):
        """Record a completed request for rate tracking."""
        ...
```

### Job Queue

```python
class RedisJobQueue:
    """Simple job queue for background agent runs using Redis streams."""
    
    async def enqueue(self, thread_id: str, user_input: str) -> str:
        """Add job to queue, return job_id."""
        ...
    
    async def dequeue(self, consumer_name: str) -> dict | None:
        """Get next job (blocking)."""
        ...
    
    async def get_result(self, job_id: str, timeout: int = 30) -> dict | None:
        """Poll for job result."""
        ...
```

---

## 10. Thread Management

### OpenAI Thread as Disposable Cache

OpenAI threads expire after 60 days. We handle this transparently:

```python
class ThreadManager:
    async def get_or_create_openai_thread(self, thread_id: str) -> str | None:
        """Get valid OpenAI thread ID, recreating if expired."""
        
        thread = await self.db.get_thread(thread_id)
        
        # Recent enough to trust?
        if thread.openai_thread_id and thread.last_used_at:
            age = datetime.utcnow() - thread.last_used_at
            if age < timedelta(days=50):
                return thread.openai_thread_id
        
        # Verify or recreate
        if await self._is_valid_openai_thread(thread.openai_thread_id):
            return thread.openai_thread_id
        
        return await self._recreate_openai_thread(thread_id)
    
    async def _recreate_openai_thread(self, thread_id: str) -> str:
        """Rebuild OpenAI thread from our database."""
        messages = await self._get_messages(thread_id)
        
        openai_thread = await self.openai.beta.threads.create(
            messages=[{"role": m.role, "content": m.content} for m in messages]
        )
        
        await self.db.update_thread(thread_id, openai_thread_id=openai_thread.id)
        return openai_thread.id
```

---

## 11. Tool System

### Tool Decorator

```python
@tool(description="Search documents for relevant information")
async def search_documents(
    query: str,
    entity_id: str = None,
    top_k: int = 5,
) -> list[dict]:
    """Search the document knowledge base."""
    result = search_only(question=query, entity_id=entity_id, final_chunks=top_k)
    return result.get("hits", [])
```

### Built-in RAG Tools (Wrapping Existing System)

```python
# Wrap your existing RAG as agent tools

@tool(description="Search documents for information")
async def search_documents(query: str, entity_id: str = None, top_k: int = 5):
    """Uses existing searcher.search_only()"""
    ...

@tool(description="Ask a question and get an answer from documents")
async def ask_documents(question: str, entity_id: str = None):
    """Uses existing searcher.search()"""
    ...

@tool(description="Upload a document to the knowledge base")
async def upload_document(file_path: str, entity_id: str = None):
    """Uses existing doc_ingestor.ingest_file()"""
    ...

@tool(description="Delete a document from the knowledge base")
async def delete_document(file_hash: str):
    """Uses existing vectordb.delete_by_file_hash()"""
    ...

@tool(description="List all documents in the knowledge base")
async def list_documents(entity_id: str = None):
    """Uses existing vectordb.get()"""
    ...
```

---

## 12. Execution Flow

```
User Input → Load Thread/Agent → Apply Memory Strategy → 
Check Rate Limits (Redis) → Call LLM → Tool Calls? →
[YES: Execute Tools → Loop] / [NO: Continue] →
Record Usage (Redis) → Save Messages (PostgreSQL) → Response
```

### Agent Runner

```python
class AgentRunner:
    async def run(self, thread_id: str, user_input: str) -> AgentResponse:
        # 1. Load data from PostgreSQL
        thread = await self.store.get_thread(thread_id)
        agent = await self.store.get_agent(thread.agent_id)
        history = await self.store.get_messages(thread_id)
        
        # 2. Compile system prompt
        system_prompt = agent.definition.compile()
        
        # 3. Apply memory strategy
        messages = strategy.prepare_messages(system_prompt, history, user_input, limits)
        
        # 4. Tool execution loop
        for iteration in range(max_tool_iterations):
            # Check rate limits (Redis)
            await rate_limiter.wait_if_needed(estimated_tokens)
            
            # Call provider
            response = await provider.run(system_prompt, messages, tools)
            
            # Record usage (Redis)
            await rate_limiter.record_request(response.usage["total_tokens"])
            
            # No tool calls? Done
            if not response.tool_calls:
                break
            
            # Execute tools and loop
            results = await self._execute_tools(response.tool_calls)
            messages.extend(...)
        
        # 5. Save to PostgreSQL
        await self.store.add_message(thread_id, "user", user_input)
        await self.store.add_message(thread_id, "assistant", response.content)
        
        return response
```

---

## 13. Public API

### Simple Usage

```python
from ai_agents import Agent

agent = Agent(
    name="Property Assistant",
    role="You help property managers with their vacation rentals.",
    provider="openai",
    model="gpt-4o",
    tools=["search_documents", "ask_documents"],
)

response = await agent.chat("What documents do I have uploaded?")
```

### Full Control

```python
from ai_agents import AgentStore, AgentDefinition

store = AgentStore(
    database_url="postgresql://...",
    redis_url="redis://localhost:6379",
)

agent_id = await store.create_agent(
    definition=AgentDefinition(...),
    provider="anthropic",
    model="claude-sonnet-4-20250514",
    memory_strategy="summarize",
)

thread_id = await store.create_thread(agent_id=agent_id, user_id="user_123")

response = await store.run(thread_id, "What's the checkout time?")

# Switch provider later
await store.update_agent(agent_id, provider="ollama", model="llama3.3:70b")
```

---

## 14. Features Beyond OpenAI

| Feature | OpenAI Assistants | Our Module |
|---------|-------------------|------------|
| Thread listing | ❌ Track IDs yourself | ✅ Query database |
| Cross-thread memory | ❌ Not available | ✅ `user_memory` table |
| Fork/branch threads | ❌ Manual copy | ✅ Built-in |
| Custom truncation | ❌ `auto` or `last_messages` | ✅ Any strategy |
| Thread archiving | ❌ Manual before 60 days | ✅ Forever (your DB) |
| Provider switching | ❌ Locked to OpenAI | ✅ Change anytime |
| Cost tracking | ❌ Check billing | ✅ Per-thread, per-user |
| Rate limit coordination | ❌ Per-process | ✅ Redis shared |
| Background jobs | ❌ Not available | ✅ Redis queue |

---

## 15. Implementation Phases

| Phase | Scope | Week |
|-------|-------|------|
| **1. Foundation** | AgentDefinition, PostgreSQL schema, AgentStore CRUD | 1 |
| **2. Redis** | Connection, RateLimiter, token tracking | 1-2 |
| **3. Providers** | Base interface, OpenAI, Anthropic, registry | 2 |
| **4. Memory** | Strategies (last_messages, first_last, summarize) | 3 |
| **5. Tools** | Decorator, registry, RAG wrappers | 3-4 |
| **6. Runner** | Execution loop, tool calls, persistence | 4 |
| **7. OpenAI Assistants** | Optional native provider, thread recreation | 5 |
| **8. Ollama** | Tool injection, response parsing | 5 |
| **9. Job Queue** | RedisJobQueue, workers, status tracking | 6 |
| **10. Polish** | Error handling, logging, tests, docs | 6+ |

---

## 16. Dependencies

```toml
[project]
dependencies = [
    # Core
    "pydantic>=2.0",
    "sqlalchemy>=2.0",
    "asyncpg",
    "redis>=5.0",
    "tiktoken",
    
    # Providers
    "openai>=1.0",
    "anthropic>=0.20",
    "httpx",
    
    # Existing (already installed)
    # opensearch-py, torch, transformers, sentence-transformers
]
```

---

## 17. Summary

This module provides:

1. **Provider-agnostic agents** - Same code works with OpenAI, Anthropic, Ollama
2. **Full data ownership** - PostgreSQL as source of truth, no vendor lock-in
3. **Redis-powered scaling** - Rate limiting, job queue, caching from day one
4. **Existing RAG integration** - Wrap current OpenSearch/embeddings as tools
5. **Flexible memory** - Multiple strategies beyond OpenAI's limitations
6. **Future-proof** - Ready for local LLMs when hardware catches up

**Key insight:** OpenAI Assistants API is a nice optimization, not a dependency. We build the persistence layer ourselves and use providers purely for compute.
