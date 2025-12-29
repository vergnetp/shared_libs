"""
Reranking utilities - shared across all AI modules.

Cross-encoder reranking with language detection.
Skips reranking for unsupported languages (returns input order).

Usage:
    from ai.reranker import rerank, get_reranker
    
    # Simple rerank
    results = rerank("what is the rent?", ["The rent is $2000", "Lease terms..."], top_k=3)
    # Returns: [(0, 0.95), (1, 0.12)]  # (index, score)
    
    # Get reranker function
    reranker = get_reranker()
    results = reranker(query, documents, top_k=5)
"""

import sys
import subprocess
import threading
import numpy as np
from typing import List, Tuple, Callable, Optional


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


# Supported languages for mmarco-multilingual
SUPPORTED_LANGUAGES = {
    "en", "de", "fr", "es", "it", "pt", "nl", "pl",
    "ru", "zh", "ja", "ko", "ar", "hi"
}

MODEL_REPO = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

# Cached reranker
_reranker = None
_reranker_tried = False  # Track if we already tried to load
_langdetect_loaded = False
_loading = False
_load_error = None


def _ensure_package(package: str, pip_name: str = None):
    """Ensure package is installed."""
    try:
        __import__(package)
    except ImportError:
        pip_name = pip_name or package
        print(f"[ai.reranker] Installing {pip_name}...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            pip_name, "-q", "--disable-pip-version-check"
        ])


def get_status() -> dict:
    """Get reranker model loading status."""
    return {
        "ready": _reranker is not None,
        "loading": _loading,
        "model": "mmarco-multilingual" if _reranker else None,
        "error": str(_load_error) if _load_error else None,
    }


def preload_models(callback: Callable = None):
    """
    Pre-load reranker model in background thread.
    
    Args:
        callback: Optional callback when loading completes
    """
    global _loading
    
    if _reranker is not None or _loading:
        return
    
    def _load():
        global _loading
        try:
            _loading = True
            print("[ai.reranker] Pre-loading reranker model...")
            get_reranker()
            print("[ai.reranker] Reranker ready")
        except Exception as e:
            global _load_error
            _load_error = e
            print(f"[ai.reranker] Failed to load: {e}")
        finally:
            _loading = False
            if callback:
                callback(get_status())
    
    thread = threading.Thread(target=_load, daemon=True)
    thread.start()


def detect_language(text: str) -> str:
    """
    Detect language of text.
    
    Returns ISO 639-1 code (e.g., 'en', 'fr', 'zh').
    Falls back to 'en' if detection fails.
    """
    global _langdetect_loaded
    
    if not _langdetect_loaded:
        _ensure_package("langdetect")
        _langdetect_loaded = True
    
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "en"


def is_language_supported(text: str) -> bool:
    """Check if text language is supported by reranker."""
    lang = detect_language(text)
    return lang in SUPPORTED_LANGUAGES


def get_reranker(
    force_reload: bool = False,
) -> Callable[[str, List[str], int], List[Tuple[int, float]]]:
    """
    Get reranker function.
    
    Args:
        force_reload: Reload model even if cached
        
    Returns:
        Function(query, documents, top_k) -> [(index, score), ...]
    """
    global _reranker, _reranker_tried
    
    # Return cached reranker or None if we already tried and failed
    if _reranker_tried and not force_reload:
        return _reranker
    
    _reranker_tried = True
    _ensure_package("sentence_transformers", "sentence-transformers")
    
    try:
        from sentence_transformers import CrossEncoder
        
        print(f"[ai.reranker] Loading mmarco-multilingual ({len(SUPPORTED_LANGUAGES)} languages)")
        model = CrossEncoder(MODEL_REPO)
    except Exception as e:
        print(f"[ai.reranker] WARNING: Failed to load CrossEncoder: {e}")
        print(f"[ai.reranker] Falling back to cosine similarity (no reranking)")
        # Return None to indicate reranking is unavailable
        _reranker = None
        return None
    
    def reranker(
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[int, float]]:
        """Rerank documents by relevance to query."""
        if not documents:
            return []
        
        pairs = [[query, doc] for doc in documents]
        scores = model.predict(pairs)
        
        # Sort by score descending
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(int(idx), float(score)) for idx, score in ranked[:top_k]]
    
    _reranker = reranker
    return _reranker


def rerank(
    query: str,
    documents: List[str],
    top_k: int = 5,
    check_language: bool = True,
) -> List[Tuple[int, float]]:
    """
    Rerank documents by relevance to query.
    
    Uses cross-encoder for supported languages.
    Falls back to input order for unsupported languages.
    
    Args:
        query: Search query
        documents: List of document texts
        top_k: Number of results to return
        check_language: Whether to check language support
        
    Returns:
        List of (index, score) tuples, sorted by score descending
    """
    if not documents:
        return []
    
    # Check language support
    if check_language and not is_language_supported(query):
        # Return input order with dummy scores
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(documents)))]
    
    reranker = get_reranker()
    if reranker is None:
        # No reranker available, return input order with dummy scores
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(documents)))]
    return reranker(query, documents, top_k)


def rerank_with_diversity(
    query: str,
    documents: List[str],
    embeddings: List[List[float]] = None,
    top_k: int = 5,
    lambda_param: float = 0.7,
) -> List[Tuple[int, float]]:
    """
    Rerank with MMR (Maximal Marginal Relevance) for diversity.
    
    Balances relevance with diversity among selected results.
    
    Args:
        query: Search query
        documents: List of document texts
        embeddings: Document embeddings (for diversity calculation)
        top_k: Number of results
        lambda_param: Balance - 1.0 = pure relevance, 0.0 = pure diversity
        
    Returns:
        List of (index, score) tuples
    """
    if not documents:
        return []
    
    # First get relevance scores
    ranked = rerank(query, documents, top_k=min(top_k * 3, len(documents)))
    
    if embeddings is None or len(ranked) <= top_k:
        return ranked[:top_k]
    
    # MMR selection
    selected = []
    selected_indices = set()
    candidates = {idx: score for idx, score in ranked}
    
    for _ in range(top_k):
        best_score = float('-inf')
        best_idx = -1
        
        for idx, relevance in candidates.items():
            if idx in selected_indices:
                continue
            
            # Max similarity to already selected
            max_sim = 0.0
            if selected:
                for sel_idx, _ in selected:
                    sim = cosine_similarity(embeddings[idx], embeddings[sel_idx])
                    max_sim = max(max_sim, sim)
            
            # MMR score
            mmr = lambda_param * relevance - (1 - lambda_param) * max_sim
            
            if mmr > best_score:
                best_score = mmr
                best_idx = idx
        
        if best_idx >= 0:
            selected.append((best_idx, candidates[best_idx]))
            selected_indices.add(best_idx)
    
    return selected
