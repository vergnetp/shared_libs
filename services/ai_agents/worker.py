#!/usr/bin/env python3
"""
Worker entrypoint for background job processing.

Run with: python -m services.ai_agents.worker
"""
import asyncio

from .config import get_settings
from .src.deps import init_app_dependencies, shutdown_app_dependencies
from .src.workers.documents import ingest_document, reindex_document
from .src.workers.memory import summarize_thread, compact_memory
from .src.workers.chat import process_chat


async def init():
    """Initialize app dependencies."""
    settings = get_settings()
    await init_app_dependencies(settings)


async def shutdown():
    """Cleanup app dependencies."""
    await shutdown_app_dependencies()


if __name__ == "__main__":
    from backend.app_kernel.jobs import run_worker
    
    asyncio.run(run_worker(
        tasks={
            "document_ingest": ingest_document,
            "document_reindex": reindex_document,
            "summarization": summarize_thread,
            "memory_compaction": compact_memory,
            "chat_response": process_chat,
        },
        init_app=init,
        shutdown_app=shutdown,
    ))
