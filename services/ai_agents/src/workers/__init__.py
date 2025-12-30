"""
Background job workers.

Task processors for:
- Document ingestion and reindexing
- Memory management (summarization, compaction)
- Chat processing

All workers:
1. Use fresh DB connections per job
2. Verify resource scope before processing
3. Use app_kernel's JobContext for metadata
"""

from .documents import ingest_document, reindex_document
from .memory import summarize_thread, compact_memory
from .chat import process_chat

__all__ = [
    "ingest_document",
    "reindex_document",
    "summarize_thread",
    "compact_memory",
    "process_chat",
]
