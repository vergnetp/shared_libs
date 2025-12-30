"""
Agent CRUD endpoints.

Agents can be:
- Personal (owner_user_id set): Only owner can access
- Shared (workspace_id set): All workspace members can access
"""

import json
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from ..deps import get_db, AgentStore, UserContextStore, get_document_store, DocumentStore, Agent
from ..auth import get_current_user, CurrentUser
from ..schemas import (
    AgentCreate, AgentUpdate, AgentResponse, ErrorResponse,
    ContextSchemaUpdate, ContextSchemaResponse,
    UserContextUpdate, UserContextResponse,
)
from ...config import get_settings

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
)
async def create_agent(
    data: AgentCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Create a new agent definition.
    
    If workspace_id is provided, creates a shared workspace agent.
    Otherwise, creates a personal agent owned by the current user.
    """
    settings = get_settings()
    
    store = AgentStore(db)
    
    try:
        if data.workspace_id:
            # Shared workspace agent
            agent = await store.create(
                name=data.name,
                user=current_user,
                workspace_id=data.workspace_id,
                system_prompt=data.system_prompt or f"You are {data.name}. {data.role}",
                model=data.model or settings.default_model,
                provider=data.provider.value,
                temperature=data.temperature,
                max_tokens=data.max_tokens,
                tools=data.tools,
                capabilities=data.capabilities,
                context_schema=data.context_schema,
                memory_strategy=data.memory_strategy,
                memory_params=data.memory_params,
                premium_provider=data.premium_provider.value if data.premium_provider else None,
                premium_model=data.premium_model,
                metadata=data.metadata,
            )
        else:
            # Personal agent
            agent = await store.create(
                name=data.name,
                user=current_user,
                owner_user_id=current_user.id,
                system_prompt=data.system_prompt or f"You are {data.name}. {data.role}",
                model=data.model or settings.default_model,
                provider=data.provider.value,
                temperature=data.temperature,
                max_tokens=data.max_tokens,
                tools=data.tools,
                capabilities=data.capabilities,
                context_schema=data.context_schema,
                memory_strategy=data.memory_strategy,
                memory_params=data.memory_params,
                premium_provider=data.premium_provider.value if data.premium_provider else None,
                premium_model=data.premium_model,
                metadata=data.metadata,
            )
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to create agent in this workspace",
            )
        
        return _to_response(agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "",
    response_model=list[AgentResponse],
)
async def list_agents(
    workspace_id: Optional[str] = None,
    include_personal: bool = True,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    List agents accessible to the current user.
    
    Returns personal agents and workspace agents user has access to.
    """
    store = AgentStore(db)
    agents = await store.list(
        user=current_user,
        workspace_id=workspace_id,
        include_personal=include_personal,
        limit=limit,
    )
    return [_to_response(a) for a in agents]


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_agent(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get agent by ID. Requires ownership or workspace membership."""
    store = AgentStore(db)
    agent = await store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    return _to_response(agent)


@router.patch(
    "/{agent_id}",
    response_model=AgentResponse,
    responses={404: {"model": ErrorResponse}},
)
async def update_agent(
    agent_id: str,
    data: AgentUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update agent fields. Requires ownership or workspace membership."""
    import logging
    logger = logging.getLogger(__name__)
    
    store = AgentStore(db)
    
    # Get existing - store enforces access
    agent = await store.get(agent_id, user=current_user)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    # Apply updates
    updates = data.model_dump(exclude_unset=True)
    logger.info(f"update_agent: agent_id={agent_id}, updates={updates}")
    
    if "provider" in updates:
        updates["provider"] = updates["provider"].value
    
    updated = await store.update(agent_id, user=current_user, **updates)
    
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    logger.info(f"update_agent: saved memory_params={updated.get('memory_params')}")
    return _to_response(updated)


@router.get(
    "/{agent_id}/full-prompt",
    responses={404: {"model": ErrorResponse}},
)
async def get_full_system_prompt(
    agent_id: str,
    stick_to_facts: Optional[bool] = None,
    objective_responses: Optional[bool] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get the full compiled system prompt including user context."""
    # First verify access
    store = AgentStore(db)
    agent_data = await store.get(agent_id, user=current_user)
    if not agent_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    try:
        agent = await Agent.from_store(
            agent_id=agent_id,
            conn=db,
            user_id=current_user.id,
            stick_to_facts=stick_to_facts,
            objective_responses=objective_responses,
        )
        return agent.get_prompt_info()
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent not found: {agent_id}",
            )
        raise


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_agent(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
    doc_store: DocumentStore = Depends(get_document_store),
):
    """Delete an agent and all its associated threads and documents."""
    store = AgentStore(db)
    
    # Check agent exists and user has access
    agent = await store.get(agent_id, user=current_user)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    # Delete all threads for this agent (note: secure store would filter these)
    # For now, use direct DB access since we've verified agent access
    threads = await db.find_entities(
        "threads",
        where_clause="[agent_id] = ?",
        params=(agent_id,),
    )
    for thread in threads:
        # Delete messages for thread
        await db.execute(
            "DELETE FROM messages WHERE thread_id = ?",
            (thread["id"],)
        )
        # Delete thread
        await db.delete_entity("threads", thread["id"])
    
    # Delete all documents for this agent
    docs = await db.find_entities(
        "documents",
        where_clause="[agent_id] = ?",
        params=(agent_id,),
    )
    for doc in docs:
        # Delete from vector store
        try:
            await doc_store.vector_store.delete_by_filter({"document_id": doc["id"]})
        except Exception:
            pass
        # Delete from database
        await db.delete_entity("documents", doc["id"])
    
    # Delete the agent
    await store.delete(agent_id, user=current_user)
    
    print(f"[delete_agent] Deleted agent {agent_id} with {len(threads)} threads and {len(docs)} documents")


@router.post(
    "/{agent_id}/clone",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def clone_agent(
    agent_id: str,
    name: Optional[str] = None,
    db=Depends(get_db),
):
    """Clone an agent with a new name."""
    store = AgentStore(db)
    
    agent = await store.get(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    # Create a copy with new name
    cloned = await store.create(
        name=name or f"{agent.get('name', 'Agent')} (copy)",
        system_prompt=agent.get("system_prompt", ""),
        model=agent.get("model", "claude-sonnet-4-20250514"),
        provider=agent.get("provider", "anthropic"),
        temperature=agent.get("temperature", 0.7),
        max_tokens=agent.get("max_tokens", 4096),
        tools=agent.get("tools", []),
        context_schema=_parse_json(agent.get("context_schema"), None),
        metadata=agent.get("metadata", {}),
    )
    return _to_response(cloned)


# =============================================================================
# Context Schema Endpoints
# =============================================================================

@router.get(
    "/{agent_id}/context-schema",
    response_model=ContextSchemaResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_context_schema(
    agent_id: str,
    db=Depends(get_db),
):
    """Get the context schema for an agent."""
    store = AgentStore(db)
    agent = await store.get(agent_id)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    schema = _parse_json(agent.get("context_schema"), None)
    
    return ContextSchemaResponse(
        agent_id=agent_id,
        schema=schema,
        enabled=schema is not None,
    )


@router.put(
    "/{agent_id}/context-schema",
    response_model=ContextSchemaResponse,
    responses={404: {"model": ErrorResponse}},
)
async def set_context_schema(
    agent_id: str,
    data: ContextSchemaUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Set the context schema for an agent (replaces existing).
    
    Pass an empty dict {} to enable auto mode (agent decides what to remember).
    """
    store = AgentStore(db)
    agent = await store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    await store.update(agent_id, user=current_user, context_schema=data.fields)
    
    return ContextSchemaResponse(
        agent_id=agent_id,
        schema=data.fields,
        enabled=True,
    )


@router.patch(
    "/{agent_id}/context-schema",
    response_model=ContextSchemaResponse,
    responses={404: {"model": ErrorResponse}},
)
async def update_context_schema(
    agent_id: str,
    data: ContextSchemaUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Add or update fields in the context schema.
    
    Existing fields not in the update are preserved.
    """
    store = AgentStore(db)
    agent = await store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    # Merge with existing
    existing = _parse_json(agent.get("context_schema"), {}) or {}
    merged = {**existing, **data.fields}
    
    await store.update(agent_id, user=current_user, context_schema=merged)
    
    return ContextSchemaResponse(
        agent_id=agent_id,
        schema=merged,
        enabled=True,
    )


@router.delete(
    "/{agent_id}/context-schema/{field_name}",
    response_model=ContextSchemaResponse,
    responses={404: {"model": ErrorResponse}},
)
async def remove_context_schema_field(
    agent_id: str,
    field_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Remove a field from the context schema."""
    store = AgentStore(db)
    agent = await store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    existing = _parse_json(agent.get("context_schema"), {}) or {}
    
    if field_name in existing:
        del existing[field_name]
        await store.update(agent_id, user=current_user, context_schema=existing if existing else None)
    
    return ContextSchemaResponse(
        agent_id=agent_id,
        schema=existing if existing else None,
        enabled=bool(existing),
    )


@router.delete(
    "/{agent_id}/context-schema",
    response_model=ContextSchemaResponse,
    responses={404: {"model": ErrorResponse}},
)
async def disable_context_schema(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Disable context for an agent (removes schema)."""
    store = AgentStore(db)
    agent = await store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    await store.update(agent_id, user=current_user, context_schema=None)
    
    return ContextSchemaResponse(
        agent_id=agent_id,
        schema=None,
        enabled=False,
    )


# =============================================================================
# User Context Endpoints
# =============================================================================

@router.get(
    "/{agent_id}/users/{user_id}/context",
    response_model=UserContextResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_user_context(
    agent_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get the current context for a user."""
    agent_store = AgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    context_store = UserContextStore(db)
    context = await context_store.get(user_id, user=current_user)
    
    return UserContextResponse(
        user_id=user_id,
        agent_id=agent_id,
        context=context or {},
    )


@router.put(
    "/{agent_id}/users/{user_id}/context",
    response_model=UserContextResponse,
    responses={404: {"model": ErrorResponse}},
)
async def set_user_context(
    agent_id: str,
    user_id: str,
    data: UserContextUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update context for a user (deep merge)."""
    agent_store = AgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    context_store = UserContextStore(db)
    updated = await context_store.update(user_id, data.updates, user=current_user, reason=data.reason)
    
    return UserContextResponse(
        user_id=user_id,
        agent_id=agent_id,
        context=updated,
    )


@router.delete(
    "/{agent_id}/users/{user_id}/context",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_user_context(
    agent_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete all context for a user."""
    agent_store = AgentStore(db)
    agent = await agent_store.get(agent_id, user=current_user)
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    
    context_store = UserContextStore(db)
    await context_store.delete(user_id, user=current_user)


def _parse_json(value, default):
    """Parse JSON string to Python object, handling already-parsed values."""
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


def _to_response(agent: dict) -> AgentResponse:
    """Convert agent dict to response model."""
    return AgentResponse(
        id=agent["id"],
        name=agent.get("name", ""),
        role=agent.get("role", ""),
        # Ownership
        owner_user_id=agent.get("owner_user_id"),
        workspace_id=agent.get("workspace_id"),
        provider=agent.get("provider", "anthropic"),
        model=agent.get("model"),
        temperature=agent.get("temperature", 0.7),
        max_tokens=agent.get("max_tokens", 4096),
        system_prompt=agent.get("system_prompt"),
        tools=_parse_json(agent.get("tools"), []),
        guardrails=_parse_json(agent.get("guardrails"), []),
        capabilities=_parse_json(agent.get("capabilities"), []),
        context_schema=_parse_json(agent.get("context_schema"), None),
        memory_strategy=agent.get("memory_strategy", "last_n"),
        memory_params=_parse_json(agent.get("memory_params"), {"n": 20}),
        premium_provider=agent.get("premium_provider"),
        premium_model=agent.get("premium_model"),
        metadata=_parse_json(agent.get("metadata"), {}),
        created_at=agent.get("created_at"),
        updated_at=agent.get("updated_at"),
    )


# =============================================================================
# Models Catalog (for UI dropdowns)
# =============================================================================

@router.get("/models/catalog")
async def get_models_catalog():
    """
    Get available models and providers for UI dropdowns.
    
    Returns providers, models grouped by provider, and defaults.
    """
    from backend.ai.ai_agents.model_config import get_models_catalog
    return get_models_catalog()
