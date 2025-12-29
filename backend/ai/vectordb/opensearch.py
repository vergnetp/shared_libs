"""OpenSearch vector store implementation."""

import asyncio
from typing import List, Dict, Any, Optional
from opensearchpy import OpenSearch, AsyncOpenSearch
from opensearchpy.helpers import bulk, async_bulk

from .base import VectorStore, Document, SearchResult


class OpenSearchStore(VectorStore):
    """
    OpenSearch-backed vector store.
    
    Usage:
        store = OpenSearchStore(
            host="localhost",
            port=9200,
            index="documents",
            dim=384,
        )
        
        await store.connect()
        
        docs = [
            Document(id="1", content="Hello", embedding=[...], metadata={"entity_id": "abc"}),
        ]
        await store.save(docs)
        
        results = await store.search(
            query_embedding=[...],
            top_k=10,
            filters={"entity_id": "abc"},
        )
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 9200,
        index: str = "documents",
        dim: int = 384,
        user: str = None,
        password: str = None,
        use_ssl: bool = False,
        verify_certs: bool = False,
        async_mode: bool = True,
    ):
        self.host = host
        self.port = port
        self.index = index
        self.dim = dim
        self.user = user
        self.password = password
        self.use_ssl = use_ssl
        self.verify_certs = verify_certs
        self.async_mode = async_mode
        
        self._client = None
        self._sync_client = None
    
    def _get_client_config(self) -> dict:
        """Build client configuration."""
        config = {
            "hosts": [{"host": self.host, "port": self.port}],
            "use_ssl": self.use_ssl,
            "verify_certs": self.verify_certs,
        }
        
        if self.user and self.password:
            config["http_auth"] = (self.user, self.password)
        
        return config
    
    async def connect(self) -> bool:
        """Connect to OpenSearch and ensure index exists."""
        config = self._get_client_config()
        
        if self.async_mode:
            self._client = AsyncOpenSearch(**config)
        else:
            self._sync_client = OpenSearch(**config)
        
        # Ensure index exists
        await self._ensure_index()
        return True
    
    async def _ensure_index(self):
        """Create index if it doesn't exist."""
        client = self._client or self._sync_client
        
        exists = await self._call(client.indices.exists, index=self.index)
        if exists:
            return
        
        # Create index with mapping
        mapping = {
            "settings": {
                "index": {
                    "knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                }
            },
            "mappings": {
                "properties": {
                    "content": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": self.dim,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {
                                "ef_construction": 128,
                                "m": 16,
                            }
                        }
                    },
                    "metadata": {"type": "object", "enabled": True},
                }
            }
        }
        
        await self._call(client.indices.create, index=self.index, body=mapping)
    
    async def _call(self, method, **kwargs):
        """Call method (handles sync/async)."""
        if self.async_mode:
            return await method(**kwargs)
        else:
            return method(**kwargs)
    
    async def save(self, documents: List[Document]) -> int:
        """Save documents to OpenSearch."""
        if not documents:
            return 0
        
        client = self._client or self._sync_client
        
        actions = []
        for doc in documents:
            actions.append({
                "_index": self.index,
                "_id": doc.id,
                "_source": {
                    "content": doc.content,
                    "embedding": doc.embedding,
                    "metadata": doc.metadata,
                }
            })
        
        if self.async_mode:
            success, _ = await async_bulk(client, actions, refresh=True)
        else:
            success, _ = bulk(client, actions, refresh=True)
        
        return success
    
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filters: Dict[str, Any] = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        """Search for similar documents."""
        client = self._client or self._sync_client
        
        # Build query
        query = {
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": query_embedding,
                        "k": top_k,
                    }
                }
            }
        }
        
        # Add filters
        if filters:
            filter_clauses = []
            for key, value in filters.items():
                filter_clauses.append({
                    "term": {f"metadata.{key}": value}
                })
            
            query["query"] = {
                "bool": {
                    "must": [query["query"]],
                    "filter": filter_clauses,
                }
            }
        
        # Execute search
        response = await self._call(client.search, index=self.index, body=query)
        
        # Parse results
        documents = []
        for hit in response["hits"]["hits"]:
            score = hit.get("_score", 0.0)
            if score < min_score:
                continue
            
            source = hit["_source"]
            documents.append(Document(
                id=hit["_id"],
                content=source.get("content", ""),
                embedding=source.get("embedding", []),
                metadata=source.get("metadata", {}),
                score=score,
            ))
        
        return SearchResult(
            documents=documents,
            total=response["hits"]["total"]["value"],
            query_embedding=query_embedding,
        )
    
    async def get(self, doc_id: str) -> Optional[Document]:
        """Get document by ID."""
        client = self._client or self._sync_client
        
        try:
            response = await self._call(client.get, index=self.index, id=doc_id)
            source = response["_source"]
            return Document(
                id=response["_id"],
                content=source.get("content", ""),
                embedding=source.get("embedding", []),
                metadata=source.get("metadata", {}),
            )
        except:
            return None
    
    async def delete(self, doc_id: str) -> bool:
        """Delete document by ID."""
        client = self._client or self._sync_client
        
        try:
            await self._call(client.delete, index=self.index, id=doc_id, refresh=True)
            return True
        except:
            return False
    
    async def delete_by_filter(self, filters: Dict[str, Any]) -> int:
        """Delete documents matching filters."""
        client = self._client or self._sync_client
        
        filter_clauses = []
        for key, value in filters.items():
            filter_clauses.append({
                "term": {f"metadata.{key}": value}
            })
        
        query = {
            "query": {
                "bool": {
                    "filter": filter_clauses
                }
            }
        }
        
        response = await self._call(
            client.delete_by_query,
            index=self.index,
            body=query,
            refresh=True,
        )
        
        return response.get("deleted", 0)
    
    async def count(self, filters: Dict[str, Any] = None) -> int:
        """Count documents."""
        client = self._client or self._sync_client
        
        if filters:
            filter_clauses = []
            for key, value in filters.items():
                filter_clauses.append({
                    "term": {f"metadata.{key}": value}
                })
            
            body = {"query": {"bool": {"filter": filter_clauses}}}
        else:
            body = {"query": {"match_all": {}}}
        
        response = await self._call(client.count, index=self.index, body=body)
        return response["count"]
    
    async def clear(self) -> int:
        """Delete all documents."""
        client = self._client or self._sync_client
        
        count = await self.count()
        
        await self._call(
            client.delete_by_query,
            index=self.index,
            body={"query": {"match_all": {}}},
            refresh=True,
        )
        
        return count
    
    async def recreate_index(self, dim: int = None) -> bool:
        """Drop and recreate index (useful when changing dimensions)."""
        client = self._client or self._sync_client
        
        if dim:
            self.dim = dim
        
        # Delete if exists
        try:
            await self._call(client.indices.delete, index=self.index)
        except:
            pass
        
        # Recreate
        await self._ensure_index()
        return True
    
    async def close(self):
        """Close connection."""
        if self._client:
            await self._client.close()
        if self._sync_client:
            self._sync_client.close()
