"""RAG search orchestration."""

from typing import List, Dict, Any, Optional, Callable, Union
from dataclasses import dataclass, field

from .reranker import Reranker, mmr_select
from .context import ContextBuilder, format_context, create_token_counter


@dataclass
class SearchResult:
    """Result of a RAG search."""
    documents: List[Dict[str, Any]]
    query: str
    query_embedding: List[float] = None
    total_found: int = 0


@dataclass
class AnswerResult:
    """Result of a RAG question."""
    answer: str
    confidence: float
    sources: List[Dict[str, Any]]
    query: str
    context: str = ""


class RAGSearcher:
    """
    Orchestrates RAG search: embed → search → rerank → context → answer.
    
    Hallucination control:
    - assumptions: "forbidden" (default) or "allowed"
    - verification: None (default), "batch", or "detailed"
    
    Usage:
        from embeddings import Embedder
        from vectordb import OpenSearchStore
        
        embedder = Embedder("bge-m3")
        store = OpenSearchStore(dim=embedder.dim)
        await store.connect()
        
        # Safe defaults (no assumptions, no extra cost)
        searcher = RAGSearcher(
            vector_store=store,
            embedder=embedder,
            llm_fn=my_llm_call,
            # assumptions="forbidden"  ← default
            # verification=None        ← default
        )
        
        # With verification (+1 LLM call)
        searcher = RAGSearcher(
            vector_store=store,
            embedder=embedder,
            llm_fn=my_llm_call,
            verification="batch",
        )
        
        # Maximum safety (+3 LLM calls)
        searcher = RAGSearcher(
            vector_store=store,
            embedder=embedder,
            llm_fn=my_llm_call,
            assumptions="forbidden",
            verification="detailed",
        )
    """
    
    def __init__(
        self,
        vector_store,  # VectorStore instance
        embedder = None,  # Embedder instance (recommended)
        embed_fn: Callable[[str], List[float]] = None,
        rerank_fn: Callable[[str, List[str]], List[tuple]] = None,
        llm_fn: Callable[[List[Dict]], str] = None,
        llm_model: str = None,  # For accurate token counting (e.g., "gpt-4")
        count_tokens_fn: Callable[[str], int] = None,
        # Hallucination control
        assumptions: str = "forbidden",  # "forbidden" or "allowed"
        verification: str = None,        # None, "batch", or "detailed"
        # Search params
        top_k: int = 10,
        rerank_top_n: int = 20,
        context_max_tokens: int = 3000,
        use_mmr: bool = True,
        mmr_lambda: float = 0.7,
    ):
        """
        Args:
            vector_store: VectorStore instance for search
            embedder: Embedder instance (recommended for consistency)
            embed_fn: Function to embed query (legacy, use embedder instead)
            rerank_fn: Cross-encoder rerank function (legacy)
            llm_fn: Function to call LLM (takes messages, returns string)
            llm_model: LLM model name for accurate token counting
            count_tokens_fn: Custom token counter (overrides llm_model)
            assumptions: "forbidden" (say I don't know) or "allowed" (can guess)
            verification: None (no check), "batch" (+1 call), "detailed" (+3 calls)
            top_k: Number of final results
            rerank_top_n: Number to rerank before final selection
            context_max_tokens: Max tokens for context
            use_mmr: Whether to use MMR for diversity
            mmr_lambda: MMR balance parameter
        """
        self.vector_store = vector_store
        self.llm_fn = llm_fn
        self.llm_model = llm_model
        
        # Hallucination control
        self.assumptions = assumptions
        self.verification = verification
        
        # Use embedder if provided for embedding/reranking
        if embedder is not None:
            self.embedder = embedder
            self.embed_fn = embedder.embed
            self.rerank_fn = embedder.rerank
        else:
            self.embedder = None
            self.embed_fn = embed_fn
            self.rerank_fn = rerank_fn
        
        if self.embed_fn is None:
            raise ValueError("Either embedder or embed_fn is required")
        
        # Token counting for LLM context (NOT embedding model)
        # Priority: count_tokens_fn > llm_model > heuristic
        if count_tokens_fn is not None:
            self.count_tokens_fn = count_tokens_fn
        elif llm_model is not None:
            self.count_tokens_fn = create_token_counter(llm_model)
        else:
            import warnings
            warnings.warn(
                "No llm_model or count_tokens_fn provided. Using heuristic estimate "
                "which may be inaccurate for non-English text. "
                "Pass llm_model='gpt-4' for accurate counting.",
                UserWarning
            )
            self.count_tokens_fn = create_token_counter(None)  # Heuristic
        
        self.top_k = top_k
        self.rerank_top_n = rerank_top_n
        self.context_max_tokens = context_max_tokens
        self.use_mmr = use_mmr
        self.mmr_lambda = mmr_lambda
        
        self.reranker = Reranker(self.rerank_fn) if self.rerank_fn else None
        self.context_builder = ContextBuilder(
            max_tokens=context_max_tokens,
            count_fn=self.count_tokens_fn,
        )
    
    async def search(
        self,
        query: str,
        top_k: int = None,
        filters: Dict[str, Any] = None,
        entity_id: str = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        """
        Search for relevant documents.
        
        Args:
            query: Search query
            top_k: Number of results (default: self.top_k)
            filters: Metadata filters
            entity_id: Shorthand for {"entity_id": value} filter
            min_score: Minimum similarity score
            
        Returns:
            SearchResult with documents
        """
        top_k = top_k or self.top_k
        
        # Build filters
        if entity_id and not filters:
            filters = {"entity_id": entity_id}
        elif entity_id and filters:
            filters = {**filters, "entity_id": entity_id}
        
        # Embed query
        query_embedding = self.embed_fn(query)
        
        # Search vector store
        fetch_count = self.rerank_top_n if self.reranker else top_k
        
        results = await self.vector_store.search(
            query_embedding=query_embedding,
            top_k=fetch_count,
            filters=filters,
            min_score=min_score,
        )
        
        # Convert to dicts
        documents = []
        for doc in results.documents:
            documents.append({
                "id": doc.id,
                "content": doc.content,
                "embedding": doc.embedding,
                "metadata": doc.metadata,
                "score": doc.score,
            })
        
        # Rerank if available
        if self.reranker and documents:
            if self.use_mmr:
                documents = self.reranker.rerank_mmr(
                    query=query,
                    documents=documents,
                    query_embedding=query_embedding,
                    top_k=top_k,
                    rerank_top_n=self.rerank_top_n,
                    lambda_param=self.mmr_lambda,
                )
            else:
                documents = self.reranker.rerank(
                    query=query,
                    documents=documents,
                    top_k=top_k,
                )
        elif self.use_mmr and documents:
            # MMR without reranking
            documents = mmr_select(
                query_embedding=query_embedding,
                documents=documents,
                k=top_k,
                lambda_param=self.mmr_lambda,
            )
        else:
            documents = documents[:top_k]
        
        return SearchResult(
            documents=documents,
            query=query,
            query_embedding=query_embedding,
            total_found=results.total,
        )
    
    async def ask(
        self,
        question: str,
        filters: Dict[str, Any] = None,
        entity_id: str = None,
        system_prompt: str = None,
        assumptions: str = None,
        verification: str = None,
    ) -> AnswerResult:
        """
        Ask a question and get an answer from documents.
        
        Args:
            question: Question to answer
            filters: Metadata filters
            entity_id: Entity to search within
            system_prompt: Custom system prompt (overrides assumptions)
            assumptions: Override instance setting ("forbidden" or "allowed")
            verification: Override instance setting (None, "batch", "detailed")
            
        Returns:
            AnswerResult with answer and sources
        """
        if not self.llm_fn:
            raise ValueError("llm_fn required for ask()")
        
        # Use overrides or instance defaults
        assumptions = assumptions or self.assumptions
        verification = verification or self.verification
        
        # Search for relevant documents
        search_result = await self.search(
            query=question,
            filters=filters,
            entity_id=entity_id,
        )
        
        if not search_result.documents:
            return AnswerResult(
                answer="I couldn't find any relevant information to answer this question.",
                confidence=0.0,
                sources=[],
                query=question,
            )
        
        # Build context
        context = self.context_builder.build(search_result.documents)
        
        # Select prompt based on assumptions setting
        if system_prompt:
            prompt = system_prompt
        elif assumptions == "allowed":
            from .context import RAG_PROMPT_ALLOWED
            prompt = RAG_PROMPT_ALLOWED
        else:  # "forbidden" (default)
            from .context import RAG_PROMPT_FORBIDDEN
            prompt = RAG_PROMPT_FORBIDDEN
        
        # Build messages
        messages = build_rag_prompt(question, context, system_prompt=prompt)
        
        # Call LLM
        answer = await self.llm_fn(messages)
        
        # Extract sources
        sources = []
        for doc in search_result.documents:
            meta = doc.get("metadata", {})
            sources.append({
                "filename": meta.get("filename", "Unknown"),
                "page": meta.get("page_num"),
                "score": doc.get("score", 0),
                "excerpt": doc.get("content", "")[:200] + "...",
            })
        
        # Estimate confidence from scores
        avg_score = sum(d.get("score", 0) for d in search_result.documents) / len(search_result.documents)
        confidence = min(avg_score, 1.0)
        
        result = AnswerResult(
            answer=answer,
            confidence=confidence,
            sources=sources,
            query=question,
            context=context,
        )
        
        # Run verification if configured
        if verification:
            from .verification import Verifier
            
            verifier = Verifier(
                llm_fn=self.llm_fn,
                mode=verification,  # "batch" or "detailed"
            )
            
            verified = await verifier.verify(
                draft=result.answer,
                sources=search_result.documents,
            )
            
            result.answer = verified.verified_answer
            result.confidence = verified.confidence
            result.context = f"Verified ({verification}): {verified.supported_count}/{verified.total_claims} claims"
        
        return result
    
    async def search_only(
        self,
        query: str,
        top_k: int = 5,
        entity_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Simple search returning just content and metadata.
        
        Convenience method for tool integration.
        """
        result = await self.search(query, top_k=top_k, entity_id=entity_id)
        
        return [
            {
                "content": doc["content"],
                "source": doc.get("metadata", {}).get("filename", "Unknown"),
                "page": doc.get("metadata", {}).get("page_num"),
                "score": doc.get("score", 0),
            }
            for doc in result.documents
        ]
