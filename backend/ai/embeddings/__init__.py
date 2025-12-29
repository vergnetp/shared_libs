"""
Embeddings Module

Core AI module for text embeddings and token management.
Zero dependencies on other shared-libs modules.

Usage:
    # Recommended: Use Embedder for consistency
    from embeddings import Embedder
    
    embedder = Embedder("bge-m3")
    vectors = embedder.embed("Hello world")
    count = embedder.count_tokens("Some text")
    dim = embedder.dim  # 1024
    
    # Pass to other modules - everything stays consistent
    pipeline = IngestionPipeline(embedder=embedder)
    searcher = RAGSearcher(embedder=embedder, ...)
    
    # Token counting with model awareness
    from embeddings import count_tokens, TokenCounter
    
    count = count_tokens("Hello", model="gpt-4")      # Uses tiktoken
    count = count_tokens("Hello", model="bge-m3")     # Uses transformers
    count = count_tokens("Hello")                      # Uses heuristic
    
    # Switch to heuristic mode globally
    from embeddings import set_token_counter_mode
    set_token_counter_mode("heuristic")
"""

from .model_hub import (
    model_hub,
    ModelHub,
    ModelConfig,
    MODELS,
    Embedder,
    embed,
    get_token_limit,
    get_embedding_dim,
    rerank,
    ModelManager,
    model_manager,
)

from .tokenizer import (
    TokenCounter,
    CounterMode,
    token_counter,
    count_tokens,
    truncate_to_tokens,
    set_token_counter_mode,
    estimate_tokens,
)

__all__ = [
    # Embedder (recommended)
    "Embedder",
    # Model hub
    "model_hub",
    "ModelHub",
    "ModelConfig",
    "MODELS",
    # Model management
    "ModelManager",
    "model_manager",
    # Token counting
    "TokenCounter",
    "CounterMode",
    "token_counter",
    "count_tokens",
    "truncate_to_tokens",
    "set_token_counter_mode",
    "estimate_tokens",
    # Legacy functions
    "embed",
    "get_token_limit",
    "get_embedding_dim",
    "rerank",
]
