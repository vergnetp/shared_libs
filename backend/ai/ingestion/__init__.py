"""
Ingestion Module

Document processing: extract → chunk → embed → prepare for storage.
Depends on: embeddings (for embed_fn)

Usage:
    from embeddings import embed
    from ingestion import IngestionPipeline, SentenceChunker
    
    pipeline = IngestionPipeline(
        embed_fn=embed,
        chunker=SentenceChunker(max_chars=900),
    )
    
    # Ingest a PDF
    result = pipeline.ingest_file(
        "document.pdf",
        metadata={"entity_id": "property_123"},
    )
    
    print(f"Created {result.chunk_count} chunks")
    
    # Chunks ready for vectordb
    from vectordb import OpenSearchStore, Document
    
    store = OpenSearchStore(...)
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
"""

from .pipeline import IngestionPipeline, IngestedDocument
from .extractors import PDFExtractor, ImageExtractor, ExtractedDocument, PageContent
from .chunkers import (
    Chunk,
    ChunkingStrategy,
    SentenceChunker,
    TokenChunker,
    CrossPageChunker,
    create_chunker,
)

__all__ = [
    # Pipeline
    "IngestionPipeline",
    "IngestedDocument",
    # Extractors
    "PDFExtractor",
    "ImageExtractor",
    "ExtractedDocument",
    "PageContent",
    # Chunkers
    "Chunk",
    "ChunkingStrategy",
    "SentenceChunker",
    "TokenChunker",
    "CrossPageChunker",
    "create_chunker",
]
