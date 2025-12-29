"""
Document processing and storage for RAG.

Uses shared AI utilities (tokens, embeddings, reranker, vectordb).

Usage:
    from ai.documents import DocumentStore
    
    store = DocumentStore()
    
    # Add documents
    doc_id = await store.add(pdf_bytes, "contract.pdf")
    
    # Search
    results = await store.search("what is the rent?")
    
    # Answer (extractive QA or LLM)
    answer = await store.answer("what is the rent?", llm_fn=agent.chat)
    
    # Background model loading (optional - models load lazily anyway)
    from ai.documents import preload_models, get_model_status, wait_for_models
    
    preload_models()  # Start background loading at app startup
    status = get_model_status()  # Check if ready
    wait_for_models(timeout=30)  # Block until ready (for document ingest)
"""

from .store import DocumentStore, Chunk, SearchResult, AnswerResult
from .ingestion import extract_pdf, extract_image, chunk_text

# Re-export model management from embeddings
from ..embeddings import model_manager, ModelManager, Embedder
from ..reranker import get_reranker, preload_models as preload_reranker, get_status as get_reranker_status


def preload_models() -> None:
    """
    Start background loading of embedding and reranker models.
    
    Call this at app startup to avoid latency on first document operation.
    Models load lazily anyway, so this is optional but recommended.
    """
    model_manager.preload_async()
    preload_reranker()


def get_model_status() -> dict:
    """
    Get status of AI models.
    
    Returns:
        {
            "ready": True if all models loaded,
            "embeddings": {"ready": bool, "loading": bool, "model": str, "dim": int},
            "reranker": {"ready": bool, "loading": bool}
        }
    """
    status = model_manager.get_status()
    reranker_status = get_reranker_status()
    status["reranker"] = reranker_status
    status["ready"] = status["embeddings"]["ready"] and reranker_status["ready"]
    return status


def wait_for_models(timeout: float = 60) -> bool:
    """
    Wait for models to finish loading.
    
    Args:
        timeout: Max seconds to wait
        
    Returns:
        True if models are ready, False on timeout
    """
    return model_manager.wait_for_models(timeout=timeout)


def get_embedder() -> Embedder:
    """
    Get the embedder instance.
    
    Returns cached embedder, loading if necessary (blocking).
    """
    if model_manager.embedder is None:
        model_manager.preload_sync()
    return model_manager.embedder


__all__ = [
    # Core classes
    "DocumentStore",
    "Chunk",
    "SearchResult",
    "AnswerResult",
    # Ingestion
    "extract_pdf",
    "extract_image", 
    "chunk_text",
    # Model management
    "preload_models",
    "get_model_status",
    "wait_for_models",
    "get_embedder",
    "get_reranker",
    # Advanced access
    "model_manager",
    "ModelManager",
    "Embedder",
]
