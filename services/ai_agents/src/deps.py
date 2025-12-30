"""
Dependency injection and shared_libs imports.

This module centralizes:
1. All shared_libs imports
2. FastAPI dependency injection functions
3. App-specific dependencies (NOT database - kernel handles that)

IMPORTANT: Database is managed by app_kernel. Use:
    - from backend.app_kernel.db import db_session_dependency  (for routes)
    - from backend.app_kernel.db import get_db_session          (for workers)
    - from backend.app_kernel.db import get_db_manager          (for health checks)
"""

from pathlib import Path
from typing import AsyncGenerator, Optional, Dict
from contextlib import asynccontextmanager

# =============================================================================
# Database - USE KERNEL'S (don't create our own!)
# =============================================================================
from backend.app_kernel.db import (
    db_session_dependency,  # FastAPI dependency
    get_db_session,         # Async context manager for workers
    get_db_manager,         # For health checks
    AsyncConnection,        # Type hint (re-exported from kernel)
)

# Backward-compatible aliases
get_db = db_session_dependency      # FastAPI dependency: Depends(get_db)
get_db_context = get_db_session     # Worker context: async with get_db_context() as db:

# =============================================================================
# AI Agents
# =============================================================================
from backend.ai.ai_agents import (
    Agent,
    AgentDefinition,
    tool,
    GuardrailError,
    ContextProvider,
    DefaultContextProvider,
    InMemoryContextProvider,
)
from backend.ai.ai_agents.store import (
    ThreadStore as _BaseThreadStore,
    MessageStore as _BaseMessageStore,
    AgentStore as _BaseAgentStore,
    UserContextStore as _BaseUserContextStore,
)

# =============================================================================
# Authorization & Secure Stores
# =============================================================================
from .authz import CurrentUser, is_admin, get_or_create_default_workspace
from .stores import (
    WorkspaceStore,
    SecureThreadStore,
    SecureMessageStore,
    SecureAgentStore,
    SecureDocumentStore,
    SecureUserContextStore,
)

# Re-export secure stores as the default
ThreadStore = SecureThreadStore
MessageStore = SecureMessageStore
AgentStore = SecureAgentStore
UserContextStore = SecureUserContextStore

from backend.ai.ai_agents.core import Message, ToolCall, ProviderRateLimitError
from backend.ai.ai_agents.costs import CostTracker, PROVIDER_COSTS, BudgetExceededError
from backend.ai.ai_agents.security import SecurityAuditLog, SecurityEvent
from backend.ai.ai_agents.testing import run_security_audit, AuditReport
from backend.ai.ai_agents.guardrails import ContentGuardrail, InjectionGuard
from backend.ai.ai_agents.providers import CascadingProvider

# =============================================================================
# Attachments
# =============================================================================
from backend.attachments import (
    Attachment,
    AttachmentStore,
    LocalStore as LocalAttachmentStore,
)

# =============================================================================
# RAG Pipeline
# =============================================================================
from backend.ai.documents import DocumentStore
from backend.ai.vectordb import MemoryStore, Document

# =============================================================================
# Configuration
# =============================================================================
from backend.config.base_config import BaseConfig


# =============================================================================
# App-Specific Instances (initialized in lifespan)
# 
# NOTE: Database is NOT here - kernel manages it
# =============================================================================

_attachment_store: Optional[AttachmentStore] = None
_cost_tracker: Optional[CostTracker] = None
_security_log: Optional[SecurityAuditLog] = None
_document_store: Optional[DocumentStore] = None
_vector_stores: Dict[str, MemoryStore] = {}


async def init_app_dependencies(config):
    """
    Initialize app-specific dependencies.
    
    Called from on_startup AFTER kernel has initialized database.
    
    NOTE: Do NOT init database here - kernel already did it.
    """
    global _attachment_store, _cost_tracker, _security_log
    
    # Attachments
    _attachment_store = LocalAttachmentStore(base_path=config.upload_dir)
    
    # Cost tracking
    _cost_tracker = CostTracker(
        max_conversation_cost=config.conversation_budget,
        max_total_cost=config.total_budget,
    )
    
    # Security logging
    _security_log = SecurityAuditLog()
    
    # Warm up providers
    _warmup_providers(config)
    
    # Pre-load AI models (background)
    preload_ai_models()


async def shutdown_app_dependencies():
    """
    Cleanup app-specific dependencies.
    
    Called from on_shutdown BEFORE kernel closes database.
    """
    global _attachment_store, _cost_tracker, _security_log, _document_store
    
    # Clear references
    _attachment_store = None
    _cost_tracker = None
    _security_log = None
    _document_store = None
    _vector_stores.clear()


def _warmup_providers(config):
    """Pre-initialize LLM providers."""
    from backend.ai.ai_agents.providers import get_provider
    
    # Create provider instances to trigger any startup work
    if config.anthropic_api_key:
        get_provider("anthropic", api_key=config.anthropic_api_key)
    if config.openai_api_key:
        get_provider("openai", api_key=config.openai_api_key)


def preload_ai_models():
    """Pre-load embedding and reranker models in background."""
    try:
        from backend.ai.documents import preload_models
        preload_models()
    except ImportError:
        pass


# =============================================================================
# FastAPI Dependencies
# =============================================================================

# get_db and get_db_context are defined at the top via kernel imports


def get_context_provider(db: AsyncConnection, schema: dict = None) -> DefaultContextProvider:
    """Get a context provider for chat."""
    return DefaultContextProvider(db, schema=schema)


def get_attachment_store() -> AttachmentStore:
    """Get attachment store."""
    if _attachment_store is None:
        raise RuntimeError("Attachment store not initialized")
    return _attachment_store


def get_cost_tracker() -> CostTracker:
    """Get cost tracker."""
    if _cost_tracker is None:
        raise RuntimeError("Cost tracker not initialized")
    return _cost_tracker


def get_security_log() -> SecurityAuditLog:
    """Get security audit log."""
    if _security_log is None:
        raise RuntimeError("Security log not initialized")
    return _security_log


def get_vector_store(model: str = "default") -> MemoryStore:
    """Get a vector store for the given model."""
    model_key = model.lower().replace("-", "_")
    if model_key not in _vector_stores:
        _vector_stores[model_key] = MemoryStore()
    return _vector_stores[model_key]


def get_document_store(require_models: bool = False) -> DocumentStore:
    """
    Get the shared document store (lazy init).
    
    Args:
        require_models: If True, wait for models to load (use for document ingest).
                       If False, return immediately with or without models (use for chat).
    """
    global _document_store
    
    if _document_store is None:
        from backend.app_kernel import get_logger
        from backend.ai.documents import get_model_status, wait_for_models, get_embedder, get_reranker
        logger = get_logger()
        
        try:
            if require_models:
                logger.info("Waiting for models to load (required for document processing)...")
                wait_for_models(timeout=120)
            
            status = get_model_status()
            
            if status["ready"]:
                _document_store = DocumentStore(
                    embed_fn=get_embedder().embed,
                    rerank_fn=get_reranker(),
                    preload_qa=True,
                )
                logger.info("Initialized DocumentStore with pre-loaded models")
            else:
                _document_store = DocumentStore(preload_qa=False)
                if require_models:
                    logger.warning("Initialized DocumentStore without models (timeout)")
                else:
                    logger.debug("Initialized DocumentStore (models loading in background)")
        except Exception as e:
            logger.warning(f"Error loading models: {e}")
            _document_store = DocumentStore(preload_qa=False)
    
    return _document_store


def get_document_store_for_ingest() -> DocumentStore:
    """Get document store with models required (for document ingest)."""
    return get_document_store(require_models=True)


# =============================================================================
# Entity Lookup Helpers
# =============================================================================
from fastapi import HTTPException


# Cached LLM providers (keyed by provider:model)
_providers: Dict = {}


def get_cached_provider(provider_name: str, model: str, api_key: str = None):
    """
    Get or create a cached LLM provider.
    
    Providers are stateless after init, safe to share across requests.
    """
    from backend.ai.ai_agents.providers import get_provider
    
    cache_key = f"{provider_name}:{model}"
    
    if cache_key not in _providers:
        _providers[cache_key] = get_provider(
            provider_name,
            model=model,
            api_key=api_key,
        )
    
    return _providers[cache_key]


def get_agent_provider(
    provider_name: str,
    model: str,
    api_key_fn,
    premium_provider: str = None,
    premium_model: str = None,
):
    """
    Get provider for an agent, optionally with cascading escalation.
    
    Args:
        provider_name: Primary provider name
        model: Primary model name
        api_key_fn: Function to get API key for a provider name
        premium_provider: Optional premium provider for escalation
        premium_model: Optional premium model for escalation
    
    Returns:
        LLMProvider (possibly CascadingProvider if premium configured)
    """
    fast = get_cached_provider(
        provider_name,
        model,
        api_key=api_key_fn(provider_name),
    )
    
    if premium_provider and premium_model:
        premium = get_cached_provider(
            premium_provider,
            premium_model,
            api_key=api_key_fn(premium_provider),
        )
        
        cascade_key = f"cascade:{provider_name}:{model}:{premium_provider}:{premium_model}"
        if cascade_key not in _providers:
            _providers[cascade_key] = CascadingProvider(fast=fast, premium=premium)
        return _providers[cascade_key]
    
    return fast


async def get_agent_or_404(db, agent_id: str) -> dict:
    """Get agent by ID or raise 404."""
    agent = await db.get_entity("agents", agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return agent


async def get_thread_or_404(db, thread_id: str) -> dict:
    """Get thread by ID or raise 404."""
    thread = await db.get_entity("threads", thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    return thread


async def get_document_or_404(db, document_id: str) -> dict:
    """Get document by ID or raise 404."""
    doc = await db.get_entity("documents", document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    return doc


def verify_document_ownership(doc: dict, agent_id: str):
    """Verify document belongs to agent or raise 403."""
    if doc.get("agent_id") != agent_id:
        raise HTTPException(status_code=403, detail="Document does not belong to this agent")


# =============================================================================
# RAG-enabled Agent Helper
# =============================================================================

async def create_rag_agent(definition: AgentDefinition, entity_id: str = None) -> Agent:
    """Create an agent with RAG tools attached."""
    doc_store = get_document_store()
    
    @tool
    async def search_documents(query: str) -> str:
        """Search documents for relevant information."""
        filters = {"entity_id": entity_id} if entity_id else None
        result = await doc_store.search(query, filters=filters)
        
        if not result.chunks:
            return "No relevant documents found."
        
        return "\n\n".join(
            f"[{c.metadata.get('filename', 'doc')}]: {c.content}"
            for c in result.chunks[:3]
        )
    
    return Agent(definition=definition, tools=[search_documents])


# =============================================================================
# AI Models Status
# =============================================================================

def get_ai_models_status() -> dict:
    """
    Get the status of AI models (embeddings, reranker).
    
    Returns dict with:
        - ready: True if all models are loaded
        - embeddings: Status of embedding model
        - reranker: Status of reranker model
    """
    try:
        from backend.ai.documents import get_model_status
        return get_model_status()
    except ImportError:
        return {
            "ready": False,
            "error": "backend.ai.documents not available",
            "embeddings": {"ready": False},
            "reranker": {"ready": False},
        }
