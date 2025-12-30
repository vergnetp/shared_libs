"""
Document upload and search endpoints for RAG.

Uses the simplified ai.documents module with:
- Auto-detected embeddings (BGE-M3 or MiniLM based on RAM)
- Cross-encoder reranking with language detection
- Extractive QA (tinyroberta) or LLM-based answers

Access controlled via document ownership and workspace visibility.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from typing import Optional, List

from ..deps import (
    get_db,
    get_attachment_store,
    get_document_store,
    get_document_store_for_ingest,
    get_cost_tracker,
    get_ai_models_status,
    AttachmentStore,
    Attachment,
    DocumentStore as VectorDocumentStore,
)
from ..stores import SecureDocumentStore
from ..auth import get_current_user, CurrentUser
from ..schemas import (
    DocumentUpload, DocumentResponse, SearchRequest, SearchResponse, SearchResult,
    ErrorResponse,
)
from ...config import get_settings

router = APIRouter(prefix="/documents", tags=["documents"])


# =============================================================================
# Status
# =============================================================================

@router.get("/status")
async def get_models_status():
    """
    Get AI models loading status.
    
    Returns ready=true when embeddings and reranker are loaded.
    """
    return get_ai_models_status()


@router.get("/ollama/status")
async def get_ollama_status():
    """Check Ollama availability and installed models."""
    try:
        from backend.ai.ollama import is_available_async, list_models_async, RECOMMENDED_MODELS, DEFAULT_MODEL
        
        available = await is_available_async()
        models = await list_models_async() if available else []
        
        return {
            "available": available,
            "models": models,
            "recommended": RECOMMENDED_MODELS,
            "default": DEFAULT_MODEL,
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e),
            "models": [],
            "recommended": {},
        }


@router.post("/ollama/pull")
async def pull_ollama_model(model: str = Query(default="qwen2.5:3b")):
    """Pull (download) an Ollama model."""
    try:
        from backend.ai.ollama import is_available_async, pull_model_async
        
        if not await is_available_async():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ollama is not running. Please start it first.",
            )
        
        success = await pull_model_async(model)
        
        if success:
            return {"success": True, "model": model, "message": f"Successfully pulled {model}"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to pull {model}",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# =============================================================================
# Upload
# =============================================================================

@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def upload_document(
    file: UploadFile = File(...),
    entity_id: str = Form(...),
    workspace_id: Optional[str] = Form(None),
    visibility: str = Form("private"),  # 'private' | 'workspace'
    title: Optional[str] = Form(None),
    tags: str = Form(""),  # Comma-separated
    metadata: str = Form("{}"),  # JSON string
    async_processing: bool = Form(False),  # Use job queue for processing
    current_user: CurrentUser = Depends(get_current_user),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
    db=Depends(get_db),
    cost_tracker=Depends(get_cost_tracker),
):
    """
    Upload a document for RAG.
    
    Supported formats: PDF, TXT, Markdown, images (with OCR)
    
    The document is:
    1. Stored as an attachment
    2. Extracted and chunked
    3. Embedded and indexed
    
    Args:
        workspace_id: If provided with visibility='workspace', document is shared
        visibility: 'private' (only owner) or 'workspace' (workspace members)
        async_processing: If true, process via job queue (returns immediately)
    
    Returns 503 if AI models are still loading (unless async_processing=true).
    """
    import json as json_lib
    import uuid as uuid_mod
    
    # For async processing, skip model check (worker will handle it)
    if not async_processing:
        # Check if models are ready
        model_status = get_ai_models_status()
        if not model_status["ready"]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "AI models are still loading. Please wait.",
                    "status": model_status,
                }
            )
    
    settings = get_settings()
    
    # Validate file size
    contents = await file.read()
    if len(contents) > settings.max_upload_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Max size: {settings.max_upload_size / 1024 / 1024:.1f}MB",
        )
    
    # Validate visibility/workspace
    if visibility == "workspace" and not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id required for workspace visibility",
        )
    
    # Parse metadata
    try:
        meta = json_lib.loads(metadata) if metadata else {}
    except json_lib.JSONDecodeError:
        meta = {}
    
    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    
    # Generate document ID upfront
    doc_id = str(uuid_mod.uuid4())
    
    # Create attachment record and save file
    attachment = Attachment(
        file_name=file.filename,
        file_type=file.content_type or "application/octet-stream",
        file_size=len(contents),
        content=contents,
    )
    attachment_path = await attachment_store.save(attachment, entity_id=doc_id)
    meta["attachment_path"] = attachment_path
    
    # Create document record in DB with pre-generated ID
    secure_store = SecureDocumentStore(db)
    doc_status = "pending" if async_processing else "processing"
    
    doc = await secure_store.create(
        filename=file.filename,
        user=current_user,
        id=doc_id,  # Use pre-generated ID directly
        workspace_id=workspace_id,
        visibility=visibility,
        content_type=file.content_type,
        size_bytes=len(contents),
        title=title or file.filename,
        tags=tag_list,
        metadata={
            **meta,
            "entity_id": entity_id,
            "status": doc_status,
        },
    )
    
    # Async processing via job queue
    if async_processing:
        try:
            from ..jobs import get_jobs, Tasks
            jobs = get_jobs()
            
            job_id = await jobs.enqueue(
                task=Tasks.DOCUMENT_INGEST,
                payload={
                    "document_id": doc_id,
                    "filename": file.filename,
                    "entity_id": entity_id,
                    "tags": tag_list,
                    "metadata": meta,
                },
                user=current_user,
                workspace_id=workspace_id,
            )
            
            from datetime import datetime, timezone
            return {
                "id": doc_id,
                "job_id": job_id,
                "status": "pending",
                "message": "Document uploaded. Processing in background.",
                "entity_id": entity_id,
                "filename": file.filename,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        except RuntimeError as e:
            # Job queue not initialized - explicit error, not silent fallback
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Async processing requested but job queue not available. "
                       "Set async_processing=false or configure REDIS_URL.",
            )
    
    # Sync processing (original behavior)
    # Get document store with models required
    doc_store = get_document_store_for_ingest()
    
    try:
        chunk_count = await doc_store.add(
            file_bytes=contents,
            filename=file.filename,
            entity_id=entity_id,
            tags=tag_list,
            metadata={
                "title": title or file.filename,
                **meta,
            },
            doc_id=doc_id,  # Use pre-generated ID
        )
    except Exception as e:
        # Clean up on failure
        await secure_store.delete(doc_id, user=current_user)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process document: {str(e)}",
        )
    
    # Update document with chunk count
    await db.execute(
        "UPDATE documents SET metadata = json_set(metadata, '$.chunk_count', ?, '$.status', 'processed') WHERE id = ?",
        (chunk_count, doc_id)
    )
    
    from datetime import datetime, timezone
    
    return DocumentResponse(
        id=doc_id,
        entity_id=entity_id,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
        chunk_count=chunk_count,
        title=title or file.filename,
        tags=tag_list,
        metadata=meta,
        created_at=datetime.now(timezone.utc),
    )


# =============================================================================
# List / Get / Delete
# =============================================================================

@router.get(
    "",
    response_model=list[DocumentResponse],
)
async def list_documents(
    entity_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    visibility: Optional[str] = None,
    tags: Optional[str] = Query(None, description="Comma-separated tags (match ANY)"),
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    List documents accessible to the current user.
    
    Shows own documents and workspace-visible documents in user's workspaces.
    """
    store = SecureDocumentStore(db)
    docs = await store.list(
        user=current_user,
        workspace_id=workspace_id,
        visibility=visibility,
        limit=limit,
    )
    
    # Filter by entity_id if provided
    if entity_id:
        docs = [d for d in docs if _parse_json(d.get("metadata"), {}).get("entity_id") == entity_id]
    
    # Filter by tags in Python (JSON column)
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        filtered = []
        for d in docs:
            doc_tags = _parse_json(d.get("tags"), [])
            if any(t in doc_tags for t in tag_list):
                filtered.append(d)
        docs = filtered
    
    return [_to_response(d) for d in docs]


@router.get("/tags")
async def list_all_tags(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get all unique tags across accessible documents."""
    store = SecureDocumentStore(db)
    docs = await store.list(user=current_user, limit=1000)
    
    all_tags = set()
    for doc in docs:
        tags = _parse_json(doc.get("tags"), [])
        all_tags.update(tags)
    
    return {"tags": sorted(all_tags)}


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_document(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get document by ID. Requires ownership or workspace visibility."""
    store = SecureDocumentStore(db)
    doc = await store.get(document_id, user=current_user)
    
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document not found: {document_id}",
        )
    
    return _to_response(doc)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_document(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
    db=Depends(get_db),
):
    """Delete a document and its chunks. Requires ownership or admin."""
    store = SecureDocumentStore(db)
    doc = await store.get(document_id, user=current_user)
    
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document not found: {document_id}",
        )
    
    # Delete from vector store (if models are loaded)
    try:
        doc_store = get_document_store()
        await doc_store.vector_store.delete_by_filter({"document_id": document_id})
    except Exception:
        pass  # Vector store not available, that's fine
    
    # Delete attachment using stored path
    try:
        import json
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        attachment_path = metadata.get("attachment_path")
        if attachment_path:
            await attachment_store.delete(attachment_path)
    except Exception:
        pass
    
    # Delete from database (secure store handles ownership check)
    await store.delete(document_id, user=current_user)


# =============================================================================
# Tags Management
# =============================================================================

@router.post("/{document_id}/tags")
async def add_document_tags(
    document_id: str,
    tags: List[str],
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Add tags to a document. Requires ownership or workspace membership."""
    store = SecureDocumentStore(db)
    doc = await store.get(document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Update in vector store (if available)
    try:
        doc_store = get_document_store()
        await doc_store.vector_store.update_by_filter(
            filters={"document_id": document_id},
            metadata={"tags": tags},
        )
    except Exception:
        pass  # Vector store not available
    
    # Update in database (source of truth)
    existing = _parse_json(doc.get("tags"), [])
    new_tags = list(set(existing + tags))
    await store.update(document_id, user=current_user, tags=new_tags)
    
    return {"tags": new_tags}


@router.delete("/{document_id}/tags")
async def remove_document_tags(
    document_id: str,
    tags: List[str],
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Remove tags from a document. Requires ownership or workspace membership."""
    store = SecureDocumentStore(db)
    doc = await store.get(document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    existing = _parse_json(doc.get("tags"), [])
    new_tags = [t for t in existing if t not in tags]
    
    # Update in vector store (if available)
    try:
        doc_store = get_document_store()
        await doc_store.vector_store.update_by_filter(
            filters={"document_id": document_id},
            metadata={"tags": new_tags},
        )
    except Exception:
        pass  # Vector store not available
    
    # Update in database (source of truth)
    await store.update(document_id, user=current_user, tags=new_tags)
    
    return {"tags": new_tags}


# =============================================================================
# Search
# =============================================================================

@router.post(
    "/search",
    response_model=SearchResponse,
)
async def search_documents(
    data: SearchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    doc_store: VectorDocumentStore = Depends(get_document_store),
):
    """
    Search documents using vector similarity + reranking.
    
    Optionally filter by entity_id or tags.
    
    Note: Search currently searches all indexed documents.
    Access control is enforced at upload time.
    """
    # Build filters
    filters = data.filters.copy() if data.filters else {}
    if data.entity_id:
        filters["entity_id"] = data.entity_id
    
    # Parse tags from filters if present
    tags = None
    if "tags" in filters:
        tags = filters.pop("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
    
    # Search
    result = await doc_store.search(
        query=data.query,
        top_k=data.top_k,
        filters=filters if filters else None,
        tags=tags,
    )
    
    # Format response
    results = []
    for chunk in result.chunks:
        results.append(SearchResult(
            document_id=chunk.metadata.get("document_id", ""),
            content=chunk.content,
            score=chunk.score,
            metadata=chunk.metadata,
        ))
    
    return SearchResponse(
        results=results,
        total=result.total,
        query=data.query,
    )


# =============================================================================
# Ask (RAG Q&A)
# =============================================================================

@router.post("/ask")
async def ask_documents(
    query: str,
    entity_id: Optional[str] = None,
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    provider: str = Query("tinyroberta", description="LLM provider for answer generation"),
    model: Optional[str] = Query(None, description="Model name (required for non-tinyroberta)"),
    current_user: CurrentUser = Depends(get_current_user),
    doc_store: VectorDocumentStore = Depends(get_document_store),
):
    """
    Ask a question and get an answer from documents.
    
    Provider options:
    - tinyroberta: Fast extractive QA (default, no hallucination)
    - ollama: Local LLM (requires model name, e.g., qwen2.5:3b)
    - anthropic: Claude (requires model name and API key)
    - openai: GPT (requires model name and API key)
    - groq: Fast inference (requires model name and API key)
    
    Note: Search currently searches all indexed documents.
    Access control is enforced at upload time.
    """
    settings = get_settings()
    
    # Build filters
    filters = {"entity_id": entity_id} if entity_id else None
    
    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    
    # Get LLM function based on provider
    llm_fn = None
    if provider != "tinyroberta":
        llm_fn = await _get_llm_fn(provider, model, settings)
    
    # Ask
    try:
        result = await doc_store.answer(
            question=query,
            filters=filters,
            tags=tag_list,
            llm_fn=llm_fn,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to answer: {str(e)}",
        )
    
    return {
        "answer": result.answer,
        "confidence": result.confidence,
        "sources": [
            {
                "document_id": s.metadata.get("document_id", ""),
                "content": s.content[:200] + "..." if len(s.content) > 200 else s.content,
                "score": s.score,
            }
            for s in result.sources
        ],
        "query": query,
        "provider": provider,
        "model": model or ("tinyroberta-squad2" if provider == "tinyroberta" else ""),
    }


async def _get_llm_fn(provider: str, model: str, settings):
    """Get LLM function for RAG answers."""
    from backend.ai.ai_agents.providers import get_provider
    
    # Get API key for provider
    api_key = None
    if provider == "anthropic":
        api_key = settings.anthropic_api_key
    elif provider == "openai":
        api_key = settings.openai_api_key
    elif provider == "groq":
        api_key = settings.groq_api_key
    # ollama doesn't need API key
    
    # Create provider instance
    llm = get_provider(provider, model=model, api_key=api_key)
    
    async def llm_fn(messages):
        """Call LLM with messages."""
        # Ensure messages have string content (not list)
        clean_messages = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                # Convert list content to string
                content = "\n".join(
                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                    for c in content
                )
            clean_messages.append({
                "role": m["role"],
                "content": content,
            })
        
        response = await llm.run(clean_messages)
        return response
    
    return llm_fn


# =============================================================================
# Agent-Scoped Documents (for Chat Integration)
# =============================================================================

@router.post(
    "/agent/{agent_id}/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def upload_agent_document(
    agent_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    tags: str = Form(""),
    metadata: str = Form("{}"),
    current_user: CurrentUser = Depends(get_current_user),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
    doc_store: VectorDocumentStore = Depends(get_document_store),
    db=Depends(get_db),
):
    """
    Upload a document scoped to an agent.
    
    Documents uploaded here can be searched via the search_documents tool
    when chatting with this agent.
    
    Requires access to the agent (owner or workspace member).
    """
    import json as json_lib
    from datetime import datetime, timezone
    from ..stores import SecureAgentStore
    
    # Check if agent exists and user has access
    agent_store = SecureAgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    
    # Check if models are ready
    model_status = get_ai_models_status()
    if not model_status["ready"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "AI models are still loading. Please wait.",
                "status": model_status,
            }
        )
    
    settings = get_settings()
    
    # Validate file size
    contents = await file.read()
    if len(contents) > settings.max_upload_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Max size: {settings.max_upload_size / 1024 / 1024:.1f}MB",
        )
    
    # Parse metadata
    try:
        meta = json_lib.loads(metadata) if metadata else {}
    except json_lib.JSONDecodeError:
        meta = {}
    
    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    
    # Create attachment record
    attachment = Attachment(
        file_name=file.filename,
        file_type=file.content_type or "application/octet-stream",
        file_size=len(contents),
        content=contents,
    )
    
    # Add to document store first (generates ID, handles extraction, chunking, embedding)
    try:
        print(f"[DEBUG upload_agent_document] Uploading for agent_id={agent_id}")
        doc_id = await doc_store.add(
            file_bytes=contents,
            filename=file.filename,
            entity_id=agent_id,  # Scope to agent
            tags=tag_list,
            metadata={
                "agent_id": agent_id,
                "title": title or file.filename,
                "filename": file.filename,
                **meta,
            },
        )
        print(f"[DEBUG upload_agent_document] DocumentStore returned doc_id={doc_id}")
        
        # Debug: check what's stored
        total_count = await doc_store.count()
        agent_count = await doc_store.count(filters={"agent_id": agent_id})
        print(f"[DEBUG upload_agent_document] After upload: total_count={total_count}, agent_count={agent_count}")
    except Exception as e:
        import traceback
        print(f"[ERROR upload_agent_document] Failed to add document: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process document: {str(e)}",
        )
    
    # Save attachment (using ID from store) - CAPTURE the returned path!
    attachment_path = await attachment_store.save(attachment, entity_id=doc_id)
    print(f"[DEBUG upload] Attachment saved at path={attachment_path}")
    
    # Get chunk count
    chunk_count = await doc_store.count(filters={"document_id": doc_id})
    
    # Save document metadata to secure store
    # Document inherits workspace from agent if agent is workspace-scoped
    meta["attachment_path"] = attachment_path  # Store path for download
    meta["entity_id"] = agent_id
    meta["chunk_count"] = chunk_count
    
    secure_store = SecureDocumentStore(db)
    doc = await secure_store.create(
        filename=file.filename,
        user=current_user,
        workspace_id=agent.get("workspace_id"),  # Inherit from agent
        visibility="workspace" if agent.get("workspace_id") else "private",
        content_type=file.content_type,
        size_bytes=len(contents),
        agent_id=agent_id,
        title=title or file.filename,
        tags=tag_list,
        metadata=meta,
    )
    
    # Update with correct doc_id from vector store
    if doc:
        await db.execute(
            "UPDATE documents SET id = ? WHERE id = ?",
            (doc_id, doc["id"])
        )
    
    return DocumentResponse(
        id=doc_id,
        entity_id=agent_id,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
        chunk_count=chunk_count,
        title=title or file.filename,
        tags=tag_list,
        metadata=meta,
        created_at=datetime.now(timezone.utc),
    )


@router.get(
    "/agent/{agent_id}",
    response_model=list[DocumentResponse],
)
async def list_agent_documents(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """List all documents uploaded for an agent. Requires agent access."""
    from ..stores import SecureAgentStore
    
    # Check if agent exists and user has access
    agent_store = SecureAgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    
    # List documents for this agent that user can access
    doc_store = SecureDocumentStore(db)
    docs = await doc_store.list(
        user=current_user,
        agent_id=agent_id,
    )
    
    return [_to_response(doc) for doc in docs]


@router.delete(
    "/agent/{agent_id}/{document_id}",
)
async def delete_agent_document(
    agent_id: str,
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
    doc_store: VectorDocumentStore = Depends(get_document_store),
    db=Depends(get_db),
):
    """Delete a document from an agent. Requires access to agent and document ownership."""
    from ..stores import SecureAgentStore
    
    # Check if agent exists and user has access
    agent_store = SecureAgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    
    # Check document access
    secure_doc_store = SecureDocumentStore(db)
    doc = await secure_doc_store.get(document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    
    # Delete from vector store
    try:
        await doc_store.vector_store.delete_by_filter({"document_id": document_id})
    except Exception:
        pass
    
    # Delete attachment using stored path
    try:
        import json
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        attachment_path = metadata.get("attachment_path")
        if attachment_path:
            await attachment_store.delete(attachment_path)
    except Exception:
        pass
    
    # Delete from database (secure store handles ownership check)
    await secure_doc_store.delete(document_id, user=current_user)
    
    return {"deleted": True, "document_id": document_id}


@router.get(
    "/agent/{agent_id}/{document_id}/download",
)
async def download_agent_document(
    agent_id: str,
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
    db=Depends(get_db),
):
    """Download the original document file. Requires access to document."""
    from fastapi.responses import Response, FileResponse
    import os
    
    store = SecureDocumentStore(db)
    doc = await store.get(document_id, user=current_user)
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    filename = doc.get("filename", "document")
    content_type = doc.get("content_type", "application/octet-stream")
    
    # Get stored attachment path from metadata
    metadata = doc.get("metadata") or {}
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    
    attachment_path = metadata.get("attachment_path")
    print(f"[DEBUG download] document_id={document_id}, attachment_path={attachment_path}")
    
    # Try using stored path first
    if attachment_path and hasattr(attachment_store, 'base_path'):
        file_path = os.path.join(attachment_store.base_path, attachment_path)
        print(f"[DEBUG download] Trying stored path: {file_path}, exists={os.path.exists(file_path)}")
        if os.path.exists(file_path):
            return FileResponse(path=file_path, media_type=content_type, filename=filename)
    
    # Fallback: try load method
    if hasattr(attachment_store, 'load') and attachment_path:
        try:
            print(f"[DEBUG download] Trying attachment_store.load({attachment_path})")
            content = await attachment_store.load(attachment_path)
            if content:
                return Response(
                    content=content,
                    media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )
        except Exception as e:
            print(f"[DEBUG download] load() failed: {e}")
    
    raise HTTPException(status_code=404, detail="File not found")


# =============================================================================
# Helpers
# =============================================================================

def _parse_json(value, default):
    """Parse JSON string to Python object."""
    import json
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


def _to_response(doc: dict) -> DocumentResponse:
    """Convert document dict to response model."""
    return DocumentResponse(
        id=doc["id"],
        entity_id=doc.get("entity_id", ""),
        filename=doc.get("filename", ""),
        content_type=doc.get("content_type", "application/octet-stream"),
        size_bytes=doc.get("size_bytes", 0),
        chunk_count=doc.get("chunk_count", 0),
        title=doc.get("title"),
        tags=_parse_json(doc.get("tags"), []),
        owner_user_id=doc.get("owner_user_id"),
        workspace_id=doc.get("workspace_id"),
        visibility=doc.get("visibility", "private"),
        agent_id=doc.get("agent_id"),
        metadata=_parse_json(doc.get("metadata"), {}),
        created_at=doc.get("created_at"),
    )
