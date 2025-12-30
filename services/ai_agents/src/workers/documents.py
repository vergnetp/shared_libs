"""
Document processing workers.

Task processors for:
- Document ingestion (parse/chunk/embed)
- Reindexing

CRITICAL:
1. Each job gets fresh DB connection via context manager
2. Workers verify resource scope before processing
3. Connections are properly closed on success/failure
"""

from typing import Dict, Any

from backend.app_kernel import get_logger, get_metrics
from backend.app_kernel.jobs import JobContext

from ..jobs import AgentJobContext
from ..authz import CurrentUser, verify_resource_scope, ScopeError


def _get_logger():
    """Get logger (lazy to avoid import issues)."""
    return get_logger()


def _get_metrics():
    """Get metrics (lazy to avoid import issues)."""
    return get_metrics()


# =============================================================================
# Document Ingestion
# =============================================================================

async def ingest_document(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Process document ingestion.
    
    Payload:
        document_id: ID of document record
        filename: Original filename
        entity_id: Entity ID for grouping
        tags: List of tags
        metadata: Document metadata
    """
    from ..deps import get_db_context, get_document_store, get_attachment_store
    
    logger = _get_logger()
    metrics = _get_metrics()
    
    # Extract app context from kernel context
    app_ctx = AgentJobContext.from_kernel_context(ctx)
    document_id = payload["document_id"]
    
    logger.info(
        f"Starting document ingestion: {document_id}",
        extra={
            "document_id": document_id,
            "workspace_id": app_ctx.workspace_id,
            "user_id": app_ctx.user_id,
            "job_id": ctx.job_id,
        }
    )
    
    # Create user for scope checks
    user = CurrentUser(id=app_ctx.user_id, role="user")
    
    # Use fresh connection for this job
    async with get_db_context() as db:
        try:
            # SCOPE VERIFY: Load and check document belongs to this user/workspace
            doc = await verify_resource_scope(
                db, user, "documents", document_id, 
                expected_workspace_id=app_ctx.workspace_id
            )
            
            # Load file content from attachment store
            attachment_store = get_attachment_store()
            meta = doc.get("metadata") or {}
            if isinstance(meta, str):
                import json
                meta = json.loads(meta)
            
            attachment_path = meta.get("attachment_path")
            if not attachment_path:
                raise ValueError(f"No attachment path for document: {document_id}")
            
            file_content = await attachment_store.load(attachment_path)
            if not file_content:
                raise ValueError(f"Could not load file: {attachment_path}")
            
            # Get document store and process
            doc_store = get_document_store()
            entity_id = payload.get("entity_id") or meta.get("entity_id")
            
            chunk_count = await doc_store.add(
                file_bytes=file_content,
                filename=payload.get("filename", doc.get("filename", "unknown")),
                entity_id=entity_id,
                tags=payload.get("tags", []),
                metadata={
                    "title": doc.get("title"),
                    **meta,
                },
                doc_id=document_id,
            )
            
            # SCOPE RE-CHECK: Verify document still exists before updating
            doc_check = await db.get_entity("documents", document_id)
            if not doc_check:
                logger.warning(f"Document deleted during processing: {document_id}")
                return {"status": "deleted", "chunk_count": 0}
            
            # Update document status
            meta["status"] = "processed"
            meta["chunk_count"] = chunk_count
            await db.save_entity("documents", {
                **doc_check,
                "chunk_count": chunk_count,
                "metadata": meta,
            })
            
            metrics.increment("documents_ingested")
            logger.info(
                f"Document ingested: {document_id}",
                extra={"chunk_count": chunk_count}
            )
            
            return {
                "document_id": document_id,
                "chunk_count": chunk_count,
                "status": "processed",
            }
            
        except ScopeError as e:
            logger.error(f"Scope check failed: {e}")
            metrics.increment("errors", endpoint="document_ingestion", error_type="scope_error")
            raise PermissionError(str(e))
            
        except Exception as e:
            logger.error(
                f"Document ingestion failed: {document_id}",
                extra={"error": str(e)},
            )
            metrics.increment("errors", endpoint="document_ingestion", error_type=type(e).__name__)
            raise


# =============================================================================
# Document Reindexing
# =============================================================================

async def reindex_document(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """Reindex an existing document."""
    from ..deps import get_db_context, get_document_store
    
    logger = _get_logger()
    metrics = _get_metrics()
    
    app_ctx = AgentJobContext.from_kernel_context(ctx)
    document_id = payload["document_id"]
    user = CurrentUser(id=app_ctx.user_id, role="user")
    
    async with get_db_context() as db:
        try:
            # Verify scope
            doc = await verify_resource_scope(
                db, user, "documents", document_id,
                expected_workspace_id=app_ctx.workspace_id
            )
            
            doc_store = get_document_store()
            await doc_store.reindex(document_id)
            
            metrics.increment("documents_reindexed")
            return {"document_id": document_id, "status": "reindexed"}
            
        except ScopeError as e:
            metrics.increment("errors", endpoint="document_reindex", error_type="scope_error")
            raise PermissionError(str(e))
