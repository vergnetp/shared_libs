"""
RAG Module

Search and question-answering over documents.
Depends on: embeddings, vectordb

Usage:
    from embeddings import embed, rerank, count_tokens
    from vectordb import OpenSearchStore
    from rag import RAGSearcher
    
    # Setup
    store = OpenSearchStore(host="localhost", index="documents", dim=384)
    await store.connect()
    
    searcher = RAGSearcher(
        vector_store=store,
        embed_fn=embed,
        rerank_fn=rerank,
        llm_fn=my_llm_call,
        count_tokens_fn=count_tokens,
    )
    
    # Search only
    results = await searcher.search("query", entity_id="prop_123")
    for doc in results.documents:
        print(doc["content"])
    
    # Full Q&A
    answer = await searcher.ask("What is the monthly rent?", entity_id="prop_123")
    print(answer.answer)
    print(answer.sources)
    
Integration with ai_agents:
    from ai_agents import Agent, tool
    from rag import create_rag_tools
    
    tools = create_rag_tools(searcher, tool)
    
    agent = Agent(
        role="Property assistant with document access",
        provider="anthropic",
        api_key="...",
        tools=tools,
    )
    
    response = await agent.chat("What's the rent for property 123?")
"""

from .reranker import Reranker, mmr_select, cosine_similarity
from .context import (
    ContextBuilder, 
    format_context, 
    build_rag_prompt, 
    trim_to_tokens, 
    estimate_tokens, 
    create_token_counter,
    RAG_PROMPT_FORBIDDEN,
    RAG_PROMPT_ALLOWED,
    ANALYTICAL_PROMPT_FORBIDDEN,
    # Legacy aliases
    STRICT_RAG_PROMPT,
    ANALYTICAL_RAG_PROMPT,
)
from .searcher import RAGSearcher, SearchResult, AnswerResult
from .tools import create_rag_tools, create_simple_search_tool
from .verification import Verifier, QuickVerifier, VerifiedAnswer, Claim, VerificationMode
from .citation import (
    CitationGuardrail, 
    CitationCheckResult, 
    FailClosedGuardrail, 
    UncitedAnswerError,
)

__all__ = [
    # Searcher
    "RAGSearcher",
    "SearchResult",
    "AnswerResult",
    # Reranker
    "Reranker",
    "mmr_select",
    "cosine_similarity",
    # Context
    "ContextBuilder",
    "format_context",
    "build_rag_prompt",
    "trim_to_tokens",
    "estimate_tokens",
    "create_token_counter",
    # Prompts
    "RAG_PROMPT_FORBIDDEN",
    "RAG_PROMPT_ALLOWED",
    "ANALYTICAL_PROMPT_FORBIDDEN",
    "STRICT_RAG_PROMPT",       # Legacy alias
    "ANALYTICAL_RAG_PROMPT",   # Legacy alias
    # Verification
    "Verifier",
    "QuickVerifier",
    "VerifiedAnswer",
    "VerificationMode",
    "Claim",
    # Citation
    "CitationGuardrail",
    "CitationCheckResult",
    "FailClosedGuardrail",
    "UncitedAnswerError",
    # Tools
    "create_rag_tools",
    "create_simple_search_tool",
]
