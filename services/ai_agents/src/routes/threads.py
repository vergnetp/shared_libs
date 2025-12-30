"""
Thread CRUD and management endpoints.

All endpoints require authentication.
Access is controlled via workspace membership - store layer enforces this.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional

from ..deps import get_db, ThreadStore, MessageStore, UserContextStore, WorkspaceStore
from ..auth import get_current_user, CurrentUser
from ..authz import get_or_create_default_workspace
from ..schemas import (
    ThreadCreate, ThreadUpdate, ThreadResponse, ThreadFork, ThreadStats,
    MessageResponse, ErrorResponse,
)

router = APIRouter(prefix="/threads", tags=["threads"])


# =============================================================================
# Helper Functions
# =============================================================================

def _to_response(thread: dict) -> ThreadResponse:
    """Convert thread dict to response model."""
    from datetime import datetime
    
    def parse_dt(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except:
                return None
        return None
    
    return ThreadResponse(
        id=thread.get("id", ""),
        agent_id=thread.get("agent_id", ""),
        workspace_id=thread.get("workspace_id", ""),
        title=thread.get("title"),
        user_id=thread.get("user_id"),
        config=thread.get("config") or {},
        metadata=thread.get("metadata") or {},
        message_count=thread.get("message_count", 0),
        total_bytes=thread.get("total_bytes", 0),
        archived=bool(thread.get("archived", False)),
        created_at=parse_dt(thread.get("created_at")) or datetime.utcnow(),
        updated_at=parse_dt(thread.get("updated_at")),
    )


def _msg_to_response(msg: dict) -> MessageResponse:
    """Convert message dict to response model."""
    from datetime import datetime
    from ..schemas import MessageRole
    import json
    
    def parse_json(val, default):
        if val is None:
            return default
        if isinstance(val, (list, dict)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except:
                return default
        return default
    
    def parse_dt(val):
        if val is None:
            return datetime.utcnow()
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except:
                return datetime.utcnow()
        return datetime.utcnow()
    
    return MessageResponse(
        id=msg.get("id", ""),
        thread_id=msg.get("thread_id", ""),
        role=MessageRole(msg.get("role", "user")),
        content=msg.get("content", ""),
        tool_calls=parse_json(msg.get("tool_calls"), []),
        tool_call_id=msg.get("tool_call_id"),
        attachments=parse_json(msg.get("attachments"), []),
        metadata=parse_json(msg.get("metadata"), {}),
        created_at=parse_dt(msg.get("created_at")),
    )


# =============================================================================
# Thread CRUD
# =============================================================================

@router.post(
    "",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    data: ThreadCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Create a new conversation thread.
    
    If workspace_id not provided, uses user's default workspace.
    Requires workspace membership.
    """
    # Use default workspace if not provided
    workspace_id = data.workspace_id
    if not workspace_id:
        workspace = await get_or_create_default_workspace(db, current_user)
        workspace_id = workspace["id"]
    
    store = ThreadStore(db)
    thread = await store.create(
        agent_id=data.agent_id,
        workspace_id=workspace_id,
        user=current_user,
        title=data.title,
        config=data.config,
        metadata=data.metadata,
    )
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this workspace",
        )
    
    return _to_response(thread)


@router.get(
    "",
    response_model=list[ThreadResponse],
)
async def list_threads(
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    List threads accessible to the current user.
    
    Filters by workspace and/or agent if provided.
    Only returns threads in workspaces where user is a member.
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"list_threads: user={current_user.id}, workspace_id={workspace_id}, agent_id={agent_id}")
    
    store = ThreadStore(db)
    
    threads = await store.list(
        user=current_user,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_archived=include_archived,
        limit=limit,
    )
    
    logger.info(f"list_threads: returning {len(threads)} threads")
    return [_to_response(t) for t in threads]


@router.get(
    "/{thread_id}",
    response_model=ThreadResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_thread(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get thread by ID. Requires workspace membership."""
    store = ThreadStore(db)
    
    thread = await store.get(thread_id, user=current_user)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(thread)


@router.patch(
    "/{thread_id}",
    response_model=ThreadResponse,
    responses={404: {"model": ErrorResponse}},
)
async def update_thread(
    thread_id: str,
    data: ThreadUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update thread fields. Requires workspace membership."""
    store = ThreadStore(db)
    
    updates = data.model_dump(exclude_unset=True)
    updated = await store.update(thread_id, user=current_user, **updates)
    
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(updated)


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_thread(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete thread and all messages. Requires workspace membership."""
    store = ThreadStore(db)
    
    deleted = await store.delete(thread_id, user=current_user)
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )


# =============================================================================
# Messages
# =============================================================================

@router.get(
    "/{thread_id}/messages",
    response_model=list[MessageResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_messages(
    thread_id: str,
    limit: int = Query(100, ge=1, le=1000),
    before: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """List messages in a thread. Requires workspace membership."""
    msg_store = MessageStore(db)
    
    messages = await msg_store.list(
        thread_id=thread_id,
        user=current_user,
        limit=limit,
        before=before,
    )
    
    # If empty, check if thread exists
    if not messages:
        thread_store = ThreadStore(db)
        thread = await thread_store.get(thread_id, user=current_user)
        if not thread:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread not found: {thread_id}",
            )
    
    return [_msg_to_response(m) for m in messages]


# =============================================================================
# Archive Operations
# =============================================================================

@router.post(
    "/{thread_id}/archive",
    response_model=ThreadResponse,
    responses={404: {"model": ErrorResponse}},
)
async def archive_thread(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Archive a thread. Requires workspace membership."""
    store = ThreadStore(db)
    
    archived = await store.archive(thread_id, user=current_user)
    
    if not archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(archived)


@router.post(
    "/{thread_id}/unarchive",
    response_model=ThreadResponse,
    responses={404: {"model": ErrorResponse}},
)
async def unarchive_thread(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Unarchive a thread. Requires workspace membership."""
    store = ThreadStore(db)
    
    unarchived = await store.unarchive(thread_id, user=current_user)
    
    if not unarchived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(unarchived)


@router.get(
    "/archived",
    response_model=list[ThreadResponse],
)
async def list_archived(
    workspace_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """List archived threads accessible to current user."""
    store = ThreadStore(db)
    
    threads = await store.list_archived(
        user=current_user,
        workspace_id=workspace_id,
        limit=limit,
    )
    
    return [_to_response(t) for t in threads]


# =============================================================================
# Fork/Branch
# =============================================================================

@router.post(
    "/{thread_id}/fork",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def fork_thread(
    thread_id: str,
    data: ThreadFork,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Fork a thread. Requires membership in source workspace.
    
    If workspace_id provided, also requires membership in target workspace.
    """
    store = ThreadStore(db)
    
    forked = await store.fork(
        thread_id,
        user=current_user,
        workspace_id=data.workspace_id,
        title=data.title,
        up_to_message_id=data.up_to_message_id,
    )
    
    if not forked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(forked)


@router.post(
    "/{thread_id}/branch/{message_id}",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def branch_thread(
    thread_id: str,
    message_id: str,
    title: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Branch from a specific message. Requires workspace membership."""
    store = ThreadStore(db)
    
    branched = await store.branch(
        thread_id,
        message_id,
        user=current_user,
        title=title,
    )
    
    if not branched:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return _to_response(branched)


# =============================================================================
# Stats
# =============================================================================

@router.get(
    "/{thread_id}/stats",
    response_model=ThreadStats,
    responses={404: {"model": ErrorResponse}},
)
async def get_thread_stats(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get thread statistics. Requires workspace membership."""
    store = ThreadStore(db)
    
    stats = await store.get_stats(thread_id, user=current_user)
    
    if not stats:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    return ThreadStats(**stats)


# =============================================================================
# User Context
# =============================================================================

@router.get(
    "/{thread_id}/user-context",
)
async def get_user_context(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Get user context for the authenticated user.
    
    Requires access to the thread (workspace membership).
    """
    # Verify thread access
    thread_store = ThreadStore(db)
    thread = await thread_store.get(thread_id, user=current_user)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    # Check if agent has context enabled
    agent_data = await db.get_entity("agents", thread.get("agent_id"))
    context_schema = agent_data.get("context_schema") if agent_data else None
    if isinstance(context_schema, str):
        import json
        try:
            context_schema = json.loads(context_schema) if context_schema else None
        except:
            context_schema = None
    enabled = context_schema is not None
    
    # Get user context
    context_store = UserContextStore(db)
    context = await context_store.get(current_user.id, user=current_user)
    
    return {
        "user_id": current_user.id,
        "context": context or {},
        "enabled": enabled,
    }


@router.put(
    "/{thread_id}/user-context",
)
async def update_user_context(
    thread_id: str,
    updates: dict,
    reason: str = "Manual update",
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update user context (deep merge). Requires thread access."""
    # Verify thread access
    thread_store = ThreadStore(db)
    thread = await thread_store.get(thread_id, user=current_user)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    context_store = UserContextStore(db)
    updated = await context_store.update(
        current_user.id,
        updates,
        user=current_user,
        reason=reason,
    )
    
    return {"status": "ok", "context": updated}


@router.delete(
    "/{thread_id}/user-context",
    status_code=status.HTTP_200_OK,
)
async def clear_user_context(
    thread_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Clear all user context. Requires thread access."""
    # Verify thread access
    thread_store = ThreadStore(db)
    thread = await thread_store.get(thread_id, user=current_user)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    context_store = UserContextStore(db)
    await context_store.set(
        current_user.id,
        {},
        user=current_user,
        reason="Manual clear all",
    )
    
    return {"status": "ok", "context": {}}


@router.delete(
    "/{thread_id}/user-context/{field_name}",
    status_code=status.HTTP_200_OK,
)
async def delete_context_field(
    thread_id: str,
    field_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete a specific field from user context. Requires thread access."""
    # Verify thread access
    thread_store = ThreadStore(db)
    thread = await thread_store.get(thread_id, user=current_user)
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    
    context_store = UserContextStore(db)
    context = await context_store.get(current_user.id, user=current_user) or {}
    
    if field_name in context:
        del context[field_name]
        await context_store.set(
            current_user.id,
            context,
            user=current_user,
            reason=f"Deleted field: {field_name}",
        )
    
    return {"status": "ok", "context": context}
