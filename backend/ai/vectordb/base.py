"""Abstract interface for vector databases."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class Document:
    """A document chunk with embedding."""
    id: str
    content: str
    embedding: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0  # Set during search


@dataclass
class SearchResult:
    """Search results."""
    documents: List[Document]
    total: int
    query_embedding: List[float] = None


class VectorStore(ABC):
    """
    Abstract vector database interface.
    
    Implementations:
    - OpenSearchStore: Production, distributed
    - MemoryStore: Testing, development
    - PostgresStore: Future, pgvector
    """
    
    @abstractmethod
    async def save(self, documents: List[Document]) -> int:
        """
        Save documents with embeddings.
        
        Args:
            documents: List of Document objects
            
        Returns:
            Number of documents saved
        """
        pass
    
    @abstractmethod
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filters: Dict[str, Any] = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        """
        Search for similar documents.
        
        Args:
            query_embedding: Query vector
            top_k: Number of results
            filters: Metadata filters (e.g., {"entity_id": "123"})
            min_score: Minimum similarity score
            
        Returns:
            SearchResult with matching documents
        """
        pass
    
    @abstractmethod
    async def get(self, doc_id: str) -> Optional[Document]:
        """Get document by ID."""
        pass
    
    @abstractmethod
    async def delete(self, doc_id: str) -> bool:
        """Delete document by ID."""
        pass
    
    @abstractmethod
    async def delete_by_filter(self, filters: Dict[str, Any]) -> int:
        """
        Delete documents matching filters.
        
        Args:
            filters: Metadata filters (e.g., {"file_id": "abc"})
            
        Returns:
            Number of documents deleted
        """
        pass
    
    @abstractmethod
    async def count(self, filters: Dict[str, Any] = None) -> int:
        """Count documents, optionally filtered."""
        pass
    
    @abstractmethod
    async def clear(self) -> int:
        """Delete all documents. Returns count deleted."""
        pass
    
    # Optional methods with default implementations
    
    async def exists(self, doc_id: str) -> bool:
        """Check if document exists."""
        return await self.get(doc_id) is not None
    
    async def upsert(self, documents: List[Document]) -> int:
        """Insert or update documents."""
        # Default: just save (implementations may override for efficiency)
        return await self.save(documents)
    
    async def health_check(self) -> bool:
        """Check if store is healthy."""
        try:
            await self.count()
            return True
        except:
            return False
    
    async def update_by_filter(
        self,
        filters: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> int:
        """
        Update metadata for documents matching filters.
        
        Args:
            filters: Metadata filters to match
            metadata: New metadata to merge
            
        Returns:
            Number of documents updated
        """
        # Default: no-op, implementations should override
        return 0
