"""
VectorDB Module

Storage abstraction for vector databases.
Depends on: embeddings (for Document type consistency)

Backends:
- OpenSearchStore: Production, distributed
- MemoryStore: Testing, development
- (Future) PostgresStore: pgvector

Usage:
    from vectordb import OpenSearchStore, Document
    
    store = OpenSearchStore(
        host="localhost",
        port=9200,
        index="documents",
        dim=384,
    )
    await store.connect()
    
    # Save documents
    docs = [
        Document(
            id="doc1",
            content="Hello world",
            embedding=embed("Hello world"),
            metadata={"entity_id": "abc", "source": "file.pdf"},
        ),
    ]
    await store.save(docs)
    
    # Search
    results = await store.search(
        query_embedding=embed("greeting"),
        top_k=10,
        filters={"entity_id": "abc"},
    )
    
    for doc in results.documents:
        print(f"{doc.score:.3f}: {doc.content}")
"""

from .base import VectorStore, Document, SearchResult
from .memory import MemoryStore

# Optional: OpenSearch (requires opensearch-py with async support)
try:
    from .opensearch import OpenSearchStore
except ImportError:
    OpenSearchStore = None

__all__ = [
    "VectorStore",
    "Document",
    "SearchResult",
    "OpenSearchStore",
    "MemoryStore",
]
