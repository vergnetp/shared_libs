"""
Centralized model management for ML models.

Handles:
- Lazy loading with singleton pattern
- Model caching (disk + memory)
- Device management (CPU/GPU)
- Token counting and limits
- Consistency between tokenizer and embedder

Usage:
    from embeddings import Embedder
    
    # Consistent embedder (recommended)
    embedder = Embedder("bge-m3")
    vectors = embedder.embed("Hello world")
    count = embedder.count_tokens("Some text")
    dim = embedder.dim  # 1024
    
    # Pass to other modules - everything stays consistent
    pipeline = IngestionPipeline(embedder=embedder)
    searcher = RAGSearcher(embedder=embedder, ...)
    
    # Legacy API (still works)
    from embeddings import embed, count_tokens
    vectors = embed("Hello world")
"""

import os
import threading
from typing import List, Union, Optional, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod

import numpy as np


# Disable problematic PyTorch optimizations
os.environ.pop("PYTORCH_INIT_DEVICE", None)
os.environ["PYTORCH_DISABLE_LAZY_INIT"] = "1"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"


@dataclass
class ModelConfig:
    """Configuration for a model."""
    repo: str
    dim: int
    max_tokens: int
    type: str  # "embedder", "cross_encoder", "qa"
    multilingual: bool = False
    languages: str = "en"  # Description of language support
    

# Pre-configured models
MODELS = {
    # Embedders
    "bge-m3": ModelConfig(
        repo="BAAI/bge-m3",
        dim=1024,
        max_tokens=8192,
        type="embedder",
        multilingual=True,
        languages="100+ languages",
    ),
    "minilm": ModelConfig(
        repo="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dim=384,
        max_tokens=512,
        type="embedder",
        multilingual=True,
        languages="50+ languages",
    ),
    "minilm-l6": ModelConfig(
        repo="sentence-transformers/all-MiniLM-L6-v2",
        dim=384,
        max_tokens=512,
        type="embedder",
        multilingual=False,
        languages="en",
    ),
    
    # Cross-encoders (for reranking)
    "ms-marco-mini": ModelConfig(
        repo="cross-encoder/ms-marco-MiniLM-L-6-v2",
        dim=0,  # Not applicable
        max_tokens=512,
        type="cross_encoder",
        multilingual=False,
        languages="en",
    ),
    "ms-marco-tiny": ModelConfig(
        repo="cross-encoder/ms-marco-TinyBERT-L-2-v2",
        dim=0,
        max_tokens=512,
        type="cross_encoder",
        multilingual=False,
        languages="en",
    ),
    "mmarco-multilingual": ModelConfig(
        repo="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        dim=0,
        max_tokens=512,
        type="cross_encoder",
        multilingual=True,
        languages="14 languages (en, de, fr, es, it, pt, nl, pl, ru, zh, ja, ko, ar, hi)",
    ),
       
    
    # QA models
    "tinyroberta-squad": ModelConfig(
        repo="deepset/tinyroberta-squad2",
        dim=0,
        max_tokens=512,
        type="qa",
    ),
}


class ModelHub:
    """
    Singleton hub for managing ML models.
    
    Lazy loads models on first use, caches in memory.
    Thread-safe.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._models = {}
        self._tokenizers = {}
        self._model_locks = {}
        self._default_embedder = "minilm"
        self._default_cross_encoder = "ms-marco-tiny"
        self._device = None
        self._cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
        
        os.makedirs(self._cache_dir, exist_ok=True)
        self._initialized = True
    
    @property
    def device(self) -> str:
        """Get compute device (lazy detection)."""
        if self._device is None:
            try:
                import torch
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                self._device = "cpu"
        return self._device
    
    def set_default_embedder(self, model_name: str):
        """Set default embedder model."""
        if model_name not in MODELS:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(MODELS.keys())}")
        self._default_embedder = model_name
    
    def _get_lock(self, model_name: str) -> threading.Lock:
        """Get or create lock for a model."""
        if model_name not in self._model_locks:
            self._model_locks[model_name] = threading.Lock()
        return self._model_locks[model_name]
    
    def _load_embedder(self, model_name: str):
        """Load a sentence-transformers embedder."""
        from sentence_transformers import SentenceTransformer
        
        config = MODELS[model_name]
        model = SentenceTransformer(
            config.repo,
            cache_folder=self._cache_dir,
            device=self.device,
        )
        model.eval()
        return model
    
    def _load_cross_encoder(self, model_name: str):
        """Load a cross-encoder for reranking."""
        from sentence_transformers import CrossEncoder
        
        config = MODELS[model_name]
        model = CrossEncoder(
            config.repo,
            device=self.device,
        )
        model.model.eval()
        return model
    
    def _load_tokenizer(self, model_name: str):
        """Load tokenizer for a model."""
        from transformers import AutoTokenizer
        
        config = MODELS[model_name]
        return AutoTokenizer.from_pretrained(
            config.repo,
            cache_dir=self._cache_dir,
        )
    
    def get_model(self, model_name: str = None):
        """Get a model (lazy loads if needed)."""
        model_name = model_name or self._default_embedder
        
        if model_name not in MODELS:
            raise ValueError(f"Unknown model: {model_name}")
        
        with self._get_lock(model_name):
            if model_name not in self._models:
                config = MODELS[model_name]
                if config.type == "embedder":
                    self._models[model_name] = self._load_embedder(model_name)
                elif config.type == "cross_encoder":
                    self._models[model_name] = self._load_cross_encoder(model_name)
                else:
                    raise ValueError(f"Unknown model type: {config.type}")
            
            return self._models[model_name]
    
    def get_tokenizer(self, model_name: str = None):
        """Get tokenizer for a model."""
        model_name = model_name or self._default_embedder
        
        with self._get_lock(f"{model_name}_tokenizer"):
            if model_name not in self._tokenizers:
                self._tokenizers[model_name] = self._load_tokenizer(model_name)
            return self._tokenizers[model_name]
    
    def embed(
        self,
        texts: Union[str, List[str]],
        model_name: str = None,
        normalize: bool = True,
        batch_size: int = 32,
    ) -> Union[List[float], List[List[float]]]:
        """
        Embed text(s) to vectors.
        
        Args:
            texts: Single text or list of texts
            model_name: Model to use (default: default_embedder)
            normalize: Whether to L2 normalize embeddings
            batch_size: Batch size for encoding
            
        Returns:
            Single embedding or list of embeddings
        """
        model = self.get_model(model_name or self._default_embedder)
        
        single_input = isinstance(texts, str)
        if single_input:
            texts = [texts]
        
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        
        result = embeddings.tolist()
        return result[0] if single_input else result
    
    def count_tokens(self, text: str, model_name: str = None) -> int:
        """Count tokens in text."""
        tokenizer = self.get_tokenizer(model_name)
        return len(tokenizer.encode(text, add_special_tokens=False))
    
    def get_token_limit(self, model_name: str = None) -> int:
        """Get max token limit for a model."""
        model_name = model_name or self._default_embedder
        return MODELS[model_name].max_tokens
    
    def get_embedding_dim(self, model_name: str = None) -> int:
        """Get embedding dimension for a model."""
        model_name = model_name or self._default_embedder
        return MODELS[model_name].dim
    
    def rerank(
        self,
        query: str,
        documents: List[str],
        model_name: str = None,
        top_k: int = None,
    ) -> List[tuple]:
        """
        Rerank documents by relevance to query.
        
        Args:
            query: Search query
            documents: List of document texts
            model_name: Cross-encoder model to use
            top_k: Return top K results (None = all)
            
        Returns:
            List of (index, score) tuples, sorted by score descending
        """
        model_name = model_name or self._default_cross_encoder
        model = self.get_model(model_name)
        
        pairs = [[query, doc] for doc in documents]
        scores = model.predict(pairs)
        
        # Create (index, score) pairs and sort
        results = list(enumerate(scores))
        results.sort(key=lambda x: x[1], reverse=True)
        
        if top_k:
            results = results[:top_k]
        
        return results
    
    def unload(self, model_name: str = None):
        """Unload a model from memory."""
        if model_name:
            self._models.pop(model_name, None)
            self._tokenizers.pop(model_name, None)
        else:
            self._models.clear()
            self._tokenizers.clear()
    
    def loaded_models(self) -> List[str]:
        """List currently loaded models."""
        return list(self._models.keys())


# Global singleton instance
model_hub = ModelHub()


class Embedder:
    """
    Consistent embedder that bundles model, tokenizer, and config.
    
    Ensures you can't accidentally mix tokenizers from different models.
    Auto-selects multilingual reranker when using multilingual embedder.
    
    Usage:
        embedder = Embedder("bge-m3")
        
        # All methods use the same model
        vectors = embedder.embed("Hello world")
        count = embedder.count_tokens("Some text")
        
        # Properties
        print(embedder.dim)           # 1024
        print(embedder.max_tokens)    # 8192
        print(embedder.model_name)    # "bge-m3"
        print(embedder.multilingual)  # True
        
        # Pass to other modules
        pipeline = IngestionPipeline(embedder=embedder)
        searcher = RAGSearcher(embedder=embedder, ...)
    """
    
    def __init__(self, model_name: str = "minilm", reranker_name: str = None):
        """
        Args:
            model_name: Embedding model to use
            reranker_name: Cross-encoder for reranking (auto-selected if not provided)
        """
        if model_name not in MODELS:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(MODELS.keys())}")
        
        self.model_name = model_name
        self._config = MODELS[model_name]
        
        # Auto-select reranker based on embedder's language support
        if reranker_name is None:
            if self._config.multilingual:
                self.reranker_name = "mmarco-multilingual"
            else:
                self.reranker_name = "ms-marco-tiny"
        else:
            self.reranker_name = reranker_name
            # Warn if using English reranker with multilingual embedder
            reranker_config = MODELS.get(reranker_name)
            if (self._config.multilingual 
                and reranker_config 
                and not reranker_config.multilingual):
                import warnings
                warnings.warn(
                    f"Using English-only reranker '{reranker_name}' with multilingual "
                    f"embedder '{model_name}'. Consider 'mmarco-multilingual' for better results.",
                    UserWarning
                )
    
    @property
    def dim(self) -> int:
        """Embedding dimension."""
        return self._config.dim
    
    @property
    def multilingual(self) -> bool:
        """Whether this embedder supports multiple languages."""
        return self._config.multilingual
    
    @property
    def languages(self) -> str:
        """Description of supported languages."""
        return self._config.languages
    
    @property
    def max_tokens(self) -> int:
        """Maximum token limit."""
        return self._config.max_tokens
    
    def embed(
        self,
        texts: Union[str, List[str]],
        normalize: bool = True,
        batch_size: int = 32,
    ) -> Union[List[float], List[List[float]]]:
        """Embed text(s) using this model."""
        return model_hub.embed(
            texts,
            model_name=self.model_name,
            normalize=normalize,
            batch_size=batch_size,
        )
    
    def count_tokens(self, text: str) -> int:
        """
        Count tokens using this model's tokenizer.
        
        Uses accurate counting with the embedding model's tokenizer.
        """
        from .tokenizer import token_counter
        return token_counter.count(text, model=self.model_name)
    
    def truncate_to_tokens(self, text: str, max_tokens: int = None) -> str:
        """Truncate text to fit within token limit."""
        from .tokenizer import token_counter
        max_tokens = max_tokens or self.max_tokens
        return token_counter.truncate(text, max_tokens, model=self.model_name)
    
    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = None,
    ) -> List[tuple]:
        """Rerank documents using cross-encoder."""
        return model_hub.rerank(
            query,
            documents,
            model_name=self.reranker_name,
            top_k=top_k,
        )
    
    def __repr__(self) -> str:
        return f"Embedder(model={self.model_name}, dim={self.dim}, max_tokens={self.max_tokens})"


# Convenience functions (legacy API)
def embed(texts: Union[str, List[str]], **kwargs) -> Union[List[float], List[List[float]]]:
    """Embed text(s) using default model."""
    return model_hub.embed(texts, **kwargs)


def get_token_limit(model_name: str = None) -> int:
    """Get max token limit for model."""
    return model_hub.get_token_limit(model_name)


def get_embedding_dim(model_name: str = None) -> int:
    """Get embedding dimension for model."""
    return model_hub.get_embedding_dim(model_name)


def rerank(query: str, documents: List[str], **kwargs) -> List[tuple]:
    """Rerank documents by relevance."""
    return model_hub.rerank(query, documents, **kwargs)


# =============================================================================
# Model Manager (preloading and status tracking)
# =============================================================================

class ModelManager:
    """
    Manages AI model lifecycle: preloading, status tracking, and access.
    
    Usage:
        manager = ModelManager()
        manager.preload_async()  # Start background loading
        
        # Check status
        if manager.ready:
            embedder = manager.embedder
        
        # Or wait for models
        manager.wait_for_models(timeout=30)
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._embedder: Optional[Embedder] = None
        self._reranker = None
        self._ready = False
        self._loading = False
        self._error: Optional[str] = None
        self._load_event = threading.Event()
        self._initialized = True
    
    @property
    def ready(self) -> bool:
        """True if all models are loaded and ready."""
        return self._ready
    
    @property
    def loading(self) -> bool:
        """True if models are currently being loaded."""
        return self._loading
    
    @property
    def error(self) -> Optional[str]:
        """Error message if loading failed."""
        return self._error
    
    @property
    def embedder(self) -> Optional[Embedder]:
        """Get the loaded embedder, or None if not ready."""
        return self._embedder
    
    @property
    def reranker(self):
        """Get the loaded reranker, or None if not ready."""
        return self._reranker
    
    def get_status(self) -> dict:
        """Get detailed status of all models."""
        return {
            "ready": self._ready,
            "loading": self._loading,
            "error": self._error,
            "embeddings": {
                "ready": self._embedder is not None,
                "loading": self._loading and self._embedder is None,
                "error": None if self._embedder or self._loading else self._error,
                "model": self._embedder.model_name if self._embedder else None,
                "dim": self._embedder.dim if self._embedder else None,
            },
            "reranker": {
                "ready": self._reranker is not None,
                "loading": self._loading and self._reranker is None,
                "error": None if self._reranker or self._loading else self._error,
            },
        }
    
    def preload_sync(self) -> bool:
        """
        Load all models synchronously (blocking).
        
        Returns True if successful, False on error.
        """
        if self._ready:
            return True
        
        # Note: Don't check _loading here - we might be called from preload_async's background thread
        # where _loading is already True. Just proceed with loading.
        
        self._loading = True
        self._error = None
        
        try:
            # Load embedder
            print("[ModelManager] Loading embedding model...")
            self._embedder = Embedder()  # Auto-selects based on RAM
            _ = self._embedder.embed("warmup")  # Force model load
            print(f"[ModelManager] Embedder ready: {self._embedder.model_name}, dim={self._embedder.dim}")
            
            # Load reranker
            print("[ModelManager] Loading reranker...")
            from backend.ai.reranker import get_reranker
            self._reranker = get_reranker()
            print("[ModelManager] Reranker ready")
            
            self._ready = True
            print("[ModelManager] All models ready")
            return True
            
        except Exception as e:
            self._error = str(e)
            print(f"[ModelManager] Error loading models: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            self._loading = False
            self._load_event.set()
    
    def preload_async(self) -> None:
        """
        Load all models in background thread.
        
        Call wait_for_models() or check .ready to know when done.
        """
        print(f"[ModelManager] preload_async called, ready={self._ready}, loading={self._loading}")
        if self._ready or self._loading:
            print(f"[ModelManager] Skipping - already ready or loading")
            return
        
        self._loading = True
        self._load_event.clear()
        
        def _load():
            print("[ModelManager] Background thread starting")
            self.preload_sync()
            print("[ModelManager] Background thread finished")
        
        thread = threading.Thread(target=_load, daemon=True)
        thread.start()
        print("[ModelManager] Background thread started")
    
    def wait_for_models(self, timeout: float = None) -> bool:
        """
        Wait for models to finish loading.
        
        Args:
            timeout: Max seconds to wait (None = wait forever)
            
        Returns:
            True if models are ready, False if timeout or error
        """
        if self._ready:
            return True
        
        self._load_event.wait(timeout=timeout)
        return self._ready


# Global instance
model_manager = ModelManager()
