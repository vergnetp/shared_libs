"""Reranking and MMR diversification for RAG."""

import numpy as np
from typing import List, Callable, Optional


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a = np.array(a)
    b = np.array(b)
    
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a, b) / (norm_a * norm_b))


def mmr_select(
    query_embedding: List[float],
    documents: List[dict],
    embedding_key: str = "embedding",
    k: int = 5,
    lambda_param: float = 0.7,
) -> List[dict]:
    """
    Maximal Marginal Relevance selection for diverse results.
    
    Balances relevance to query with diversity among selected documents.
    
    Args:
        query_embedding: Query vector
        documents: List of documents with embeddings
        embedding_key: Key to access embedding in document dict
        k: Number of documents to select
        lambda_param: Balance between relevance (1.0) and diversity (0.0)
        
    Returns:
        Selected documents in order of selection
    """
    if not documents:
        return []
    
    if len(documents) <= k:
        return documents
    
    # Calculate relevance scores
    relevance_scores = []
    for doc in documents:
        score = cosine_similarity(query_embedding, doc[embedding_key])
        relevance_scores.append(score)
    
    selected = []
    selected_indices = set()
    
    for _ in range(k):
        best_score = -float('inf')
        best_idx = -1
        
        for i, doc in enumerate(documents):
            if i in selected_indices:
                continue
            
            # Relevance to query
            relevance = relevance_scores[i]
            
            # Maximum similarity to already selected
            max_sim = 0.0
            if selected:
                for sel_doc in selected:
                    sim = cosine_similarity(doc[embedding_key], sel_doc[embedding_key])
                    max_sim = max(max_sim, sim)
            
            # MMR score
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim
            
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        
        if best_idx >= 0:
            selected.append(documents[best_idx])
            selected_indices.add(best_idx)
    
    return selected


class Reranker:
    """
    Rerank search results using cross-encoder.
    
    Usage:
        from embeddings import model_hub
        
        reranker = Reranker(model_hub.rerank)
        
        # Rerank results
        reranked = reranker.rerank(query, documents, top_k=5)
        
        # With MMR for diversity
        diverse = reranker.rerank_mmr(
            query, 
            documents,
            query_embedding=query_vec,
            top_k=5,
            lambda_param=0.7,
        )
    """
    
    def __init__(
        self,
        rerank_fn: Callable[[str, List[str]], List[tuple]] = None,
    ):
        """
        Args:
            rerank_fn: Function that takes (query, documents) and returns
                       list of (index, score) tuples sorted by score desc
        """
        self.rerank_fn = rerank_fn
    
    def rerank(
        self,
        query: str,
        documents: List[dict],
        content_key: str = "content",
        top_k: int = 10,
    ) -> List[dict]:
        """
        Rerank documents by relevance to query.
        
        Args:
            query: Search query
            documents: List of document dicts
            content_key: Key to access text content
            top_k: Number of results to return
            
        Returns:
            Reranked documents with updated scores
        """
        if not documents or not self.rerank_fn:
            return documents[:top_k]
        
        # Get texts
        texts = [doc[content_key] for doc in documents]
        
        # Rerank
        results = self.rerank_fn(query, texts, top_k=top_k)
        
        # Build reranked list
        reranked = []
        for idx, score in results:
            doc = documents[idx].copy()
            doc["rerank_score"] = float(score)
            reranked.append(doc)
        
        return reranked
    
    def rerank_mmr(
        self,
        query: str,
        documents: List[dict],
        query_embedding: List[float],
        content_key: str = "content",
        embedding_key: str = "embedding",
        top_k: int = 10,
        rerank_top_n: int = 20,
        lambda_param: float = 0.7,
    ) -> List[dict]:
        """
        Rerank with cross-encoder, then apply MMR for diversity.
        
        Args:
            query: Search query
            documents: List of document dicts
            query_embedding: Query vector for MMR
            content_key: Key for text content
            embedding_key: Key for embedding vector
            top_k: Final number of results
            rerank_top_n: Rerank this many before MMR
            lambda_param: MMR balance (1.0 = relevance, 0.0 = diversity)
            
        Returns:
            Diverse, relevant documents
        """
        # First rerank to get best candidates
        reranked = self.rerank(query, documents, content_key, top_k=rerank_top_n)
        
        # Then apply MMR for diversity
        diverse = mmr_select(
            query_embedding=query_embedding,
            documents=reranked,
            embedding_key=embedding_key,
            k=top_k,
            lambda_param=lambda_param,
        )
        
        return diverse
