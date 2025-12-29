# Shared Libraries

Modular Python libraries for AI applications, with maximum separation of concerns.

## Consistency with Embedder

**Problem:** Using different tokenizers/models causes silent bugs:
```python
# Dangerous - tokenizer counts don't match embedder's actual limits!
model_hub.set_default_embedder("bge-m3")  # 8192 tokens
count = count_tokens("text", model_name="minilm")  # 512 tokens - WRONG
```

**Solution:** Use `Embedder` class to bundle everything consistently:
```python
from embeddings import Embedder

embedder = Embedder("bge-m3")

# All methods use the same model's tokenizer
vectors = embedder.embed("Hello world")
count = embedder.count_tokens("Some text")
truncated = embedder.truncate_to_tokens(long_text, 1000)

# Properties match
print(embedder.dim)         # 1024
print(embedder.max_tokens)  # 8192

# Pass to other modules - everything stays consistent
pipeline = IngestionPipeline(embedder=embedder)
searcher = RAGSearcher(embedder=embedder, vector_store=store, llm_fn=my_llm)
```

## Module Dependency Graph

```
┌─────────────────┐
│   embeddings    │  ← Zero dependencies (core AI)
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐  ┌──────────┐
│vectordb│  │ingestion │  ← Both depend on embeddings
└───┬───┘  └────┬─────┘
    │           │
    └─────┬─────┘
          ▼
     ┌────────┐
     │  rag   │  ← Depends on vectordb, embeddings
     └────┬───┘
          │
          ▼
    ┌──────────┐
    │ai_agents │  ← Optionally uses rag, embeddings
    └──────────┘
```

## Modules

### embeddings/
Core AI module for text embeddings. **Zero dependencies on other modules.**

```python
from embeddings import Embedder

# Create consistent embedder (recommended)
embedder = Embedder("bge-m3")

# All methods guaranteed to use same model
vector = embedder.embed("Hello world")           # List[float], 1024 dims
vectors = embedder.embed(["text1", "text2"])     # List[List[float]]
count = embedder.count_tokens("Some text")       # Uses same tokenizer
truncated = embedder.truncate_to_tokens(text, 500)  # Safe truncation

# Properties
print(embedder.dim)         # 1024
print(embedder.max_tokens)  # 8192
print(embedder.model_name)  # "bge-m3"

# Rerank documents (uses cross-encoder)
results = embedder.rerank("query", ["doc1", "doc2"])  # [(idx, score), ...]

# Legacy API (still works, but no consistency guarantee)
from embeddings import embed, count_tokens
vector = embed("text", model_name="bge-m3")
```

**Available models:**
| Model | Dim | Max Tokens | Multilingual | Use Case |
|-------|-----|------------|--------------|----------|
| minilm | 384 | 512 | ✅ 50+ langs | Fast, general purpose |
| bge-m3 | 1024 | 8192 | ✅ 100+ langs | High quality |
| minilm-l6 | 384 | 512 | ❌ English | Fastest |
| ms-marco-tiny | - | 512 | ❌ English | Fast reranking |
| mmarco-multilingual | - | 512 | ✅ 14 langs | Multilingual reranking |

---

### Token Counting (Accurate vs Heuristic)

Two modes for counting tokens:

```python
from embeddings import TokenCounter, count_tokens, set_token_counter_mode

# ACCURATE MODE (default) - uses real tokenizers
count = count_tokens("Hello world", model="gpt-4")      # tiktoken
count = count_tokens("你好世界", model="bge-m3")        # transformers

# HEURISTIC MODE - fast estimation, no deps
set_token_counter_mode("heuristic")
count = count_tokens("Hello world")  # ~3 tokens (estimated)

# Direct access to counter
counter = TokenCounter()
counter.set_mode("accurate")  # or "heuristic"

# Truncate to fit token limit
truncated = counter.truncate("long text...", max_tokens=100, model="gpt-4")
```

**Model to tokenizer mapping:**
| Model Pattern | Tokenizer |
|---------------|-----------|
| gpt-*, claude-* | tiktoken cl100k_base |
| bge-*, minilm* | transformers AutoTokenizer |
| Unknown | Heuristic fallback |

**Performance:**
| Mode | First Call | Per Call | Accuracy |
|------|------------|----------|----------|
| Accurate (tiktoken) | ~100ms | <1ms | 100% |
| Accurate (transformers) | ~500ms | ~1ms | 100% |
| Heuristic | 0ms | <0.1ms | ~85% |

---

### vectordb/
Storage abstraction for vector databases.

```python
from vectordb import OpenSearchStore, MemoryStore, Document

# OpenSearch (production)
store = OpenSearchStore(
    host="localhost",
    port=9200,
    index="documents",
    dim=384,
)
await store.connect()

# Memory (testing)
store = MemoryStore()

# Save documents
docs = [
    Document(
        id="doc1",
        content="Hello world",
        embedding=embed("Hello world"),
        metadata={"entity_id": "123", "source": "file.pdf"},
    ),
]
await store.save(docs)

# Search
results = await store.search(
    query_embedding=embed("greeting"),
    top_k=10,
    filters={"entity_id": "123"},
)

for doc in results.documents:
    print(f"{doc.score:.3f}: {doc.content}")

# Delete
await store.delete_by_filter({"entity_id": "123"})
```

---

### ingestion/
Document processing: extract → chunk → embed.

```python
from embeddings import Embedder
from ingestion import IngestionPipeline

embedder = Embedder("bge-m3")

# Pipeline auto-configures chunking based on embedder's token limit
pipeline = IngestionPipeline(embedder=embedder)

# Ingest PDF
result = pipeline.ingest_file(
    "document.pdf",
    metadata={"entity_id": "property_123"},
)

print(f"Created {result.chunk_count} chunks")

# Chunks ready for vectordb
from vectordb import OpenSearchStore, Document

store = OpenSearchStore(dim=embedder.dim)  # Dimension matches automatically
await store.connect()

docs = [
    Document(
        id=c["id"],
        content=c["content"],
        embedding=c["embedding"],
        metadata=c["metadata"],
    )
    for c in result.chunks
]
await store.save(docs)
```

**Chunking strategies:**
- `SentenceChunker` - Split by sentences, respect boundaries
- `TokenChunker` - Split by token count (for model limits)
- `CrossPageChunker` - Chunk across page boundaries

**Extractors:**
- `PDFExtractor` - Extract text from PDFs (PyMuPDF)
- `ImageExtractor` - OCR for images (easyocr/Google Vision)

---

### rag/
Search and question-answering over documents.

```python
from embeddings import Embedder
from vectordb import OpenSearchStore
from rag import RAGSearcher

embedder = Embedder("bge-m3")

store = OpenSearchStore(dim=embedder.dim)
await store.connect()

searcher = RAGSearcher(
    vector_store=store,
    embedder=embedder,  # Consistent embed + tokenizer + rerank
    llm_fn=my_llm_call,
)

# Search only
results = await searcher.search("query", entity_id="prop_123")
for doc in results.documents:
    print(doc["content"])

# Full Q&A
answer = await searcher.ask(
    "What is the monthly rent?",
    entity_id="prop_123",
)
print(answer.answer)
print(answer.sources)
```

**Features:**
- Cross-encoder reranking
- MMR for result diversity
- Automatic context preparation
- Token-aware truncation

**Hallucination Control:**
```python
# Safe defaults (no assumptions allowed, no extra cost)
searcher = RAGSearcher(
    vector_store=store,
    embedder=embedder,
    llm_fn=my_llm,
    # assumptions="forbidden"  ← default
    # verification=None        ← default
)

# With batch verification (+1 LLM call)
searcher = RAGSearcher(
    vector_store=store,
    embedder=embedder,
    llm_fn=my_llm,
    verification="batch",
)

# Maximum safety (+3 LLM calls)
searcher = RAGSearcher(
    vector_store=store,
    embedder=embedder,
    llm_fn=my_llm,
    assumptions="forbidden",
    verification="detailed",
)

# Per-request override
answer = await searcher.ask(
    "What's the rent?",
    entity_id="prop_123",
    verification="detailed",  # Override for this request
)
```

| Parameter | Values | Default | Cost |
|-----------|--------|---------|------|
| `assumptions` | `"forbidden"`, `"allowed"` | `"forbidden"` | Free |
| `verification` | `None`, `"batch"`, `"detailed"` | `None` | +1 / +3 calls |

---

### Integration with ai_agents

```python
from ai_agents import Agent, tool
from embeddings import Embedder
from vectordb import OpenSearchStore
from rag import RAGSearcher, create_rag_tools

# Single embedder for consistency
embedder = Embedder("bge-m3")

# Setup RAG with same embedder
store = OpenSearchStore(host="localhost", index="properties", dim=embedder.dim)
await store.connect()

searcher = RAGSearcher(
    vector_store=store,
    embedder=embedder,
    llm_fn=my_llm,
    assumptions="forbidden",   # No guessing
    verification="batch",      # Verify claims
)

# Create tools for agent
rag_tools = create_rag_tools(searcher, tool)

# Create agent with RAG capabilities
agent = Agent(
    role="Property management assistant with document access",
    provider="anthropic",
    api_key="...",
    tools=rag_tools,
)

# Agent can now search documents
response = await agent.chat(
    "What is the monthly rent for property 123?",
    user_id="user_abc",
)
```

---

## Installation

```bash
# Core (embeddings only)
pip install sentence-transformers numpy

# VectorDB
pip install opensearch-py

# Ingestion
pip install PyMuPDF  # PDF extraction
pip install easyocr  # Local OCR (optional)
pip install google-cloud-vision  # Google OCR (optional)

# Full stack
pip install sentence-transformers numpy opensearch-py PyMuPDF
```

---

## Architecture Principles

1. **Zero coupling** - Each module can be used independently
2. **Dependency injection** - Pass functions/objects, not import modules
3. **Abstract interfaces** - Easy to swap implementations
4. **Lazy loading** - Models loaded on first use
5. **Async-first** - All I/O operations are async
6. **Consistency by design** - `Embedder` class bundles model + tokenizer

---

## File Structure

```
shared_libs/
├── embeddings/
│   ├── __init__.py
│   └── model_hub.py      # Model loading + embedding
│
├── vectordb/
│   ├── __init__.py
│   ├── base.py           # Abstract interface
│   ├── opensearch.py     # OpenSearch backend
│   └── memory.py         # In-memory backend
│
├── ingestion/
│   ├── __init__.py
│   ├── pipeline.py       # Orchestration
│   ├── extractors/
│   │   ├── __init__.py
│   │   └── pdf.py        # PDF + Image extraction
│   └── chunkers/
│       ├── __init__.py
│       └── text.py       # Chunking strategies
│
├── rag/
│   ├── __init__.py
│   ├── searcher.py       # Main orchestration
│   ├── reranker.py       # Cross-encoder + MMR
│   ├── context.py        # Context preparation
│   └── tools.py          # ai_agents integration
│
└── ai_agents/            # (separate module)
    └── ...
```
