"""Vector-based memory strategy using embeddings module."""

from typing import List, Callable, Optional

from .base import MemoryStrategy


class VectorMemory(MemoryStrategy):
    """
    Vector-based memory that retrieves semantically similar messages.
    
    Uses the embeddings module for encoding, stores in vectordb.
    
    Usage:
        from embeddings import embed
        from vectordb import MemoryStore
        
        store = MemoryStore()  # or OpenSearchStore
        memory = VectorMemory(
            embed_fn=embed,
            vector_store=store,
            top_k=10,
        )
        
        # Or with simple list storage (no vectordb dependency)
        memory = VectorMemory(embed_fn=embed, top_k=10)
    """
    
    def __init__(
        self,
        embed_fn: Callable[[str], List[float]],
        vector_store = None,
        top_k: int = 10,
        min_score: float = 0.5,
        **kwargs,  # Accept extra params from other strategies
    ):
        """
        Args:
            embed_fn: Function to embed text (from embeddings module)
            vector_store: Optional VectorStore instance
            top_k: Number of messages to retrieve
            min_score: Minimum similarity score
        """
        self.embed_fn = embed_fn
        self.vector_store = vector_store
        self.top_k = top_k
        self.min_score = min_score
        
        # Simple in-memory fallback if no vector store
        self._memory: List[dict] = []
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity."""
        import numpy as np
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    
    async def add(self, thread_id: str, role: str, content: str):
        """Add message to vector memory."""
        embedding = self.embed_fn(content)
        
        message = {
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "embedding": embedding,
        }
        
        if self.vector_store:
            from ...vectordb import Document
            doc = Document(
                id=f"{thread_id}_{len(self._memory)}",
                content=content,
                embedding=embedding,
                metadata={"thread_id": thread_id, "role": role},
            )
            await self.vector_store.save([doc])
        else:
            self._memory.append(message)
    
    async def select(
        self,
        messages: List[dict],
        current_input: str,
        max_tokens: int,
    ) -> List[dict]:
        """Select messages by semantic similarity to current input."""
        if not messages and not self._memory:
            return []
        
        # Embed current input
        query_embedding = self.embed_fn(current_input)
        
        if self.vector_store:
            # Use vector store search
            results = await self.vector_store.search(
                query_embedding=query_embedding,
                top_k=self.top_k,
                min_score=self.min_score,
            )
            
            return [
                {"role": doc.metadata.get("role", "user"), "content": doc.content}
                for doc in results.documents
            ]
        else:
            # Simple in-memory search
            scored = []
            
            for msg in (self._memory or messages):
                if "embedding" in msg:
                    score = self._cosine_similarity(query_embedding, msg["embedding"])
                else:
                    # Embed on the fly if needed
                    embedding = self.embed_fn(msg["content"])
                    score = self._cosine_similarity(query_embedding, embedding)
                
                if score >= self.min_score:
                    scored.append((score, msg))
            
            # Sort by score and take top_k
            scored.sort(key=lambda x: x[0], reverse=True)
            
            return [
                {"role": msg["role"], "content": msg["content"]}
                for _, msg in scored[:self.top_k]
            ]
    
    def clear(self, thread_id: str = None):
        """Clear memory."""
        if thread_id:
            self._memory = [m for m in self._memory if m.get("thread_id") != thread_id]
        else:
            self._memory = []
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
        **kwargs,  # Accept extra params from context builder
    ) -> list[dict]:
        """Build context using vector similarity search."""
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        # Get last user message for similarity search
        last_user_msg = None
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break
        
        if last_user_msg:
            # Select relevant messages by similarity
            selected = await self.select(messages, last_user_msg, max_tokens or 100000)
            result.extend(selected)
        else:
            # Fallback to last N messages
            for m in messages[-self.top_k:]:
                result.append({"role": m["role"], "content": m.get("content", "")})
        
        return result
