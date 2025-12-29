"""In-memory vector store for testing and development."""

import numpy as np
from typing import List, Dict, Any, Optional

from .base import VectorStore, Document, SearchResult


class MemoryStore(VectorStore):
    """
    In-memory vector store using numpy.
    
    Good for:
    - Unit testing
    - Development
    - Small datasets
    
    Usage:
        store = MemoryStore()
        
        docs = [Document(id="1", content="Hello", embedding=[...])]
        await store.save(docs)
        
        results = await store.search(query_embedding=[...], top_k=5)
    """
    
    def __init__(self):
        self._documents: Dict[str, Document] = {}
    
    async def save(self, documents: List[Document]) -> int:
        """Save documents to memory."""
        for doc in documents:
            self._documents[doc.id] = doc
        return len(documents)
    
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filters: Dict[str, Any] = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        """Search using cosine similarity."""
        if not self._documents:
            return SearchResult(documents=[], total=0, query_embedding=query_embedding)
        
        query_vec = np.array(query_embedding)
        query_norm = np.linalg.norm(query_vec)
        
        if query_norm == 0:
            return SearchResult(documents=[], total=0, query_embedding=query_embedding)
        
        results = []
        
        for doc in self._documents.values():
            # Apply filters
            if filters:
                match = True
                for key, value in filters.items():
                    if doc.metadata.get(key) != value:
                        match = False
                        break
                if not match:
                    continue
            
            # Calculate cosine similarity
            doc_vec = np.array(doc.embedding)
            doc_norm = np.linalg.norm(doc_vec)
            
            if doc_norm == 0:
                continue
            
            score = float(np.dot(query_vec, doc_vec) / (query_norm * doc_norm))
            
            if score >= min_score:
                # Create copy with score
                scored_doc = Document(
                    id=doc.id,
                    content=doc.content,
                    embedding=doc.embedding,
                    metadata=doc.metadata,
                    score=score,
                )
                results.append(scored_doc)
        
        # Sort by score descending
        results.sort(key=lambda d: d.score, reverse=True)
        
        return SearchResult(
            documents=results[:top_k],
            total=len(results),
            query_embedding=query_embedding,
        )
    
    async def get(self, doc_id: str) -> Optional[Document]:
        """Get document by ID."""
        return self._documents.get(doc_id)
    
    async def delete(self, doc_id: str) -> bool:
        """Delete document by ID."""
        if doc_id in self._documents:
            del self._documents[doc_id]
            return True
        return False
    
    async def delete_by_filter(self, filters: Dict[str, Any]) -> int:
        """Delete documents matching filters."""
        to_delete = []
        
        for doc_id, doc in self._documents.items():
            match = True
            for key, value in filters.items():
                if doc.metadata.get(key) != value:
                    match = False
                    break
            if match:
                to_delete.append(doc_id)
        
        for doc_id in to_delete:
            del self._documents[doc_id]
        
        return len(to_delete)
    
    async def count(self, filters: Dict[str, Any] = None) -> int:
        """Count documents."""
        if not filters:
            return len(self._documents)
        
        count = 0
        for doc in self._documents.values():
            match = True
            for key, value in filters.items():
                if doc.metadata.get(key) != value:
                    match = False
                    break
            if match:
                count += 1
        
        return count
    
    async def clear(self) -> int:
        """Clear all documents."""
        count = len(self._documents)
        self._documents.clear()
        return count
    
    async def update_by_filter(
        self,
        filters: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> int:
        """Update metadata for documents matching filters."""
        updated = 0
        
        for doc in self._documents.values():
            match = True
            for key, value in filters.items():
                if doc.metadata.get(key) != value:
                    match = False
                    break
            if match:
                doc.metadata.update(metadata)
                updated += 1
        
        return updated
