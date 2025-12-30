"""
Chat endpoints - thin wrapper around Agent class.

FastAPI only handles HTTP routing. Agent handles all logic.
All endpoints require authentication.
Access controlled via workspace membership.
"""

import json
import traceback
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ..deps import (
    get_db,
    Agent,
    get_cost_tracker,
    get_agent_provider,
    get_document_store,
    CostTracker,
    BudgetExceededError,
    ProviderRateLimitError,
    ThreadStore,
)
from ..auth import get_current_user, CurrentUser
from ..schemas import ChatRequest, ChatResponse, MessageResponse, MessageRole, ErrorResponse, SourceInfo
from ...config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])


def _parse_json(val, default=None):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except:
        return default


def _get_api_key(provider: str, settings) -> str:
    if provider == "anthropic":
        return settings.anthropic_api_key
    if provider == "groq":
        return settings.groq_api_key
    if provider == "ollama":
        return None  # Ollama doesn't need API key
    return settings.openai_api_key


def _msg_to_response(msg: dict) -> MessageResponse:
    return MessageResponse(
        id=msg.get("id", ""),
        thread_id=msg.get("thread_id"),
        role=MessageRole(msg.get("role", "assistant")),
        content=msg.get("content") or "",
        created_at=msg.get("created_at"),
        tool_calls=_parse_json(msg.get("tool_calls"), []),
        attachments=_parse_json(msg.get("attachments"), []),
        metadata=_parse_json(msg.get("metadata"), {}),
    )


async def _enqueue_chat(
    thread_id: str,
    message: str,
    user,
    workspace_id: str,
    agent_id: str,
    options: dict,
    db,
    stream: bool = False,
):
    """
    Enqueue chat for async processing.
    
    Returns immediately with job_id.
    Client can:
    - Poll GET /api/v1/jobs/{job_id} for result
    - Subscribe to WebSocket/SSE for streaming
    
    Error handling:
    - If job queue not initialized: Returns 503
    - If enqueue fails (Redis down): Rolls back message, returns 503
    """
    import uuid
    from datetime import datetime, timezone
    
    # Save user message first (so it's visible immediately)
    message_id = str(uuid.uuid4())
    await db.save_entity("messages", {
        "id": message_id,
        "thread_id": thread_id,
        "role": "user",
        "content": message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    
    # Enqueue job
    try:
        from ..jobs import get_jobs, Tasks
        jobs = get_jobs()
        
        job_id = await jobs.enqueue(
            task=Tasks.CHAT_RESPONSE,
            payload={
                "thread_id": thread_id,
                "message": message,
                "message_id": message_id,
                "stream": stream,
                "options": options,
            },
            user=user,
            workspace_id=workspace_id,
            agent_id=agent_id,
            thread_id=thread_id,
        )
        
        return {
            "status": "pending",
            "job_id": job_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "stream_channel": f"stream:{thread_id}:{message_id}" if stream else None,
            "poll_url": f"/api/v1/monitoring/jobs/{job_id}",
        }
        
    except RuntimeError as e:
        # Job queue not initialized - delete saved message and raise
        await db.delete_entity("messages", message_id)
        raise HTTPException(
            status_code=503,
            detail="Async processing not available. Set async_processing=false or configure Redis.",
        )
    except Exception as e:
        # Redis/enqueue failure - roll back message
        try:
            await db.delete_entity("messages", message_id)
        except:
            pass  # Best effort rollback
        raise HTTPException(
            status_code=503,
            detail=f"Failed to queue job: {str(e)}. Message was not saved.",
        )


@router.post("/{thread_id}", response_model=ChatResponse)
async def chat(
    thread_id: str,
    data: ChatRequest,
    async_processing: bool = False,  # Use job queue for processing
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
):
    """
    Send a message and get a response.
    
    Agent class handles all logic: history, context, tools, LLM calls, persistence.
    Access controlled via workspace membership.
    
    Args:
        async_processing: If true, enqueue job and return immediately.
                         Client polls /jobs/{job_id} or subscribes to stream.
    """
    settings = get_settings()
    
    # Check budget
    try:
        cost_tracker.check_budget()
    except BudgetExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))
    
    # Get thread - store enforces workspace membership
    store = ThreadStore(db)
    thread = await store.get(thread_id, user=current_user)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    
    agent_data = await db.get_entity("agents", thread["agent_id"])
    if not agent_data:
        raise HTTPException(status_code=404, detail=f"Agent not found")
    
    agent_id = thread["agent_id"]
    workspace_id = thread.get("workspace_id")
    user_id = current_user.id
    
    # === ASYNC PATH ===
    if async_processing:
        return await _enqueue_chat(
            thread_id=thread_id,
            message=data.message,
            user=current_user,
            workspace_id=workspace_id,
            agent_id=agent_id,
            options={
                "temperature": data.temperature,
                "memory_strategy": data.memory_strategy,
                "memory_n": data.memory_n,
                "stick_to_facts": data.stick_to_facts,
                "objective_responses": data.objective_responses,
            },
            db=db,
        )
    
    # === SYNC PATH (original behavior) ===
    
    # Get cached provider (expensive to create, safe to share)
    provider_name = agent_data.get("provider", "openai")
    model = agent_data.get("model", "gpt-4")
    premium_provider = agent_data.get("premium_provider")
    premium_model = agent_data.get("premium_model")
    
    cached_provider = get_agent_provider(
        provider_name,
        model,
        api_key_fn=lambda p: _get_api_key(p, settings),
        premium_provider=premium_provider,
        premium_model=premium_model,
    )
    
    # Set up document search context if search_documents tool is enabled
    tools_list = _parse_json(agent_data.get("tools"), [])
    sources = []
    if "search_documents" in tools_list:
        try:
            from backend.ai.ai_agents.tools import set_document_context, clear_sources
            doc_store = get_document_store()
            set_document_context(doc_store, agent_id)
            clear_sources()
        except Exception as e:
            print(f"[WARN chat] Could not set up document context: {e}")
    
    # Capability enforcement: filter tools to only those agent can use
    from ..capabilities import create_enforcer_for_agent, CapabilityError
    enforcer = create_enforcer_for_agent(agent_data)
    allowed_tools = enforcer.filter_allowed_tools(tools_list)
    
    if len(allowed_tools) < len(tools_list):
        # Some tools were filtered out - update agent_data for this request
        filtered_out = set(tools_list) - set(allowed_tools)
        print(f"[INFO chat] Filtered tools due to missing capabilities: {filtered_out}")
        agent_data = dict(agent_data)  # Don't mutate original
        agent_data["tools"] = allowed_tools
    
    # Load agent from thread - handles all config parsing and setup
    try:
        agent = await Agent.from_thread(
            thread_id=thread_id,
            conn=db,
            provider=cached_provider,
            stick_to_facts=data.stick_to_facts,
            objective_responses=data.objective_responses,
            temperature=data.temperature,
            memory_strategy=data.memory_strategy,
            memory_n=data.memory_n,
        )
        
        # Attach enforcer to agent for tool execution checks
        agent._capability_enforcer = enforcer
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise
    
    # Call Agent - it handles everything: save user msg, get history, call LLM, save response
    try:
        result = await agent.chat(
            data.message,
            user_id=current_user.id,  # Use authenticated user
        )
    except ProviderRateLimitError as e:
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded. Please wait a moment and try again.",
            headers={"Retry-After": "60"}
        )
    except Exception as e:
        import traceback
        print(f"[ERROR chat] Exception during agent.chat: {type(e).__name__}: {e}", flush=True)
        print(f"[ERROR chat] Traceback:\n{traceback.format_exc()}", flush=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")
    
    # Get sources if document search was used
    if "search_documents" in tools_list:
        try:
            from backend.ai.ai_agents.tools import get_sources
            raw_sources = get_sources()
            sources = [
                SourceInfo(
                    document_id=s.get("document_id", ""),
                    filename=s.get("filename", "Unknown"),
                    page=s.get("page"),
                    chunk_preview=s.get("chunk_preview", ""),
                    score=s.get("score"),
                    download_url=s.get("download_url"),
                )
                for s in raw_sources
            ]
        except Exception as e:
            print(f"[WARN chat] Could not get sources: {e}")
    
    # Update thread message count
    try:
        messages = await db.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
        thread["message_count"] = len(messages) if messages else 0
        await db.save_entity("threads", thread)
    except Exception:
        pass
    
    # Get final context if enabled
    try:
        final_context = None
        if agent._context_provider:
            final_context = await agent._context_provider.load(user_id)
        
        return ChatResponse(
            message=MessageResponse(
                id="",
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=result.content,
                tool_calls=result.tool_calls,
                metadata=result.to_metadata(),
            ),
            usage=result.usage,
            cost=result.cost,
            duration_ms=result.duration_ms,
            context_enabled=agent._context_provider is not None,
            user_context=final_context,
            sources=sources,
            tool_results=result.tool_results,
        )
    except Exception as e:
        import traceback
        print(f"[ERROR chat] Exception during response building: {type(e).__name__}: {e}", flush=True)
        print(f"[ERROR chat] Traceback:\n{traceback.format_exc()}", flush=True)
        raise HTTPException(status_code=500, detail=f"Response error: {str(e)}")


@router.post("/{thread_id}/stream")
async def chat_stream(
    thread_id: str,
    data: ChatRequest,
    async_processing: bool = False,  # Use job queue + Redis pub/sub
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Stream response using SSE.
    
    Access controlled via workspace membership.
    
    Args:
        async_processing: If true, enqueue job and return channel info.
                         Client subscribes to /chat/{thread_id}/subscribe/{channel_id}
    """
    settings = get_settings()
    
    # Store enforces workspace membership
    store = ThreadStore(db)
    thread = await store.get(thread_id, user=current_user)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    
    agent_data = await db.get_entity("agents", thread["agent_id"])
    if not agent_data:
        raise HTTPException(status_code=404, detail=f"Agent not found")
    
    workspace_id = thread.get("workspace_id")
    agent_id = thread["agent_id"]
    
    # === ASYNC PATH: Enqueue and return channel info ===
    if async_processing:
        return await _enqueue_chat(
            thread_id=thread_id,
            message=data.message,
            user=current_user,
            workspace_id=workspace_id,
            agent_id=agent_id,
            options={
                "temperature": data.temperature,
                "memory_strategy": data.memory_strategy,
                "memory_n": data.memory_n,
                "stick_to_facts": data.stick_to_facts,
                "objective_responses": data.objective_responses,
            },
            db=db,
            stream=True,  # Tell worker to publish chunks
        )
    
    # === SYNC PATH: Direct streaming (original behavior) ===
    
    # Get cached provider
    provider_name = agent_data.get("provider", "openai")
    model = agent_data.get("model", "gpt-4")
    premium_provider = agent_data.get("premium_provider")
    premium_model = agent_data.get("premium_model")
    
    cached_provider = get_agent_provider(
        provider_name,
        model,
        api_key_fn=lambda p: _get_api_key(p, settings),
        premium_provider=premium_provider,
        premium_model=premium_model,
    )
    
    # Capability enforcement: filter tools
    from ..capabilities import create_enforcer_for_agent
    enforcer = create_enforcer_for_agent(agent_data)
    tools_list = _parse_json(agent_data.get("tools"), [])
    allowed_tools = enforcer.filter_allowed_tools(tools_list)
    if len(allowed_tools) < len(tools_list):
        agent_data = dict(agent_data)
        agent_data["tools"] = allowed_tools
    
    # Load agent from thread
    agent = await Agent.from_thread(
        thread_id=thread_id,
        conn=db,
        provider=cached_provider,
        stick_to_facts=data.stick_to_facts,
        objective_responses=data.objective_responses,
        temperature=data.temperature,
    )
    agent._capability_enforcer = enforcer
    
    # Capture user_id before entering generator (can't use Depends inside generator)
    user_id = current_user.id
    
    async def generate():
        try:
            async for chunk in agent.stream(data.message, user_id=user_id):
                yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
        except ProviderRateLimitError:
            yield f"data: {json.dumps({'type': 'error', 'error': 'Rate limit exceeded. Please wait a moment and try again.'})}\n\n"
            return
        except Exception as e:
            print(f"[ERROR stream] Exception: {type(e).__name__}: {e}", flush=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/{thread_id}/subscribe/{channel_id}")
async def subscribe_to_stream(
    thread_id: str,
    channel_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Subscribe to async chat stream via SSE.
    
    Call this after POST /chat/{thread_id}/stream?async_processing=true
    The channel_id is returned in the response as part of stream_channel.
    
    This endpoint subscribes to Redis pub/sub and relays chunks as SSE.
    """
    import os
    
    # Verify thread access
    store = ThreadStore(db)
    thread = await store.get(thread_id, user=current_user)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise HTTPException(
            status_code=503,
            detail="Streaming subscription not available (Redis not configured)",
        )
    
    try:
        import redis.asyncio as redis_lib
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Streaming subscription not available (redis package not installed)",
        )
    
    channel = f"stream:{thread_id}:{channel_id}"
    
    async def generate():
        redis_client = redis_lib.from_url(redis_url)
        pubsub = redis_client.pubsub()
        
        try:
            await pubsub.subscribe(channel)
            
            # Timeout after 5 minutes
            import asyncio
            timeout = 300
            start = asyncio.get_event_loop().time()
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    
                    yield f"data: {data}\n\n"
                    
                    # Check for done/error
                    try:
                        parsed = json.loads(data)
                        if parsed.get("type") in ("done", "error"):
                            break
                    except:
                        pass
                
                # Check timeout
                if asyncio.get_event_loop().time() - start > timeout:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Stream timeout'})}\n\n"
                    break
                    
        finally:
            await pubsub.unsubscribe(channel)
            await redis_client.close()
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.websocket("/{thread_id}/ws")
async def chat_websocket(websocket: WebSocket, thread_id: str, token: str = None):
    """
    WebSocket for bidirectional streaming.
    
    Pass JWT token as query param: /chat/{thread_id}/ws?token=<jwt>
    Or in first message: {"type": "auth", "token": "<jwt>"}
    """
    await websocket.accept()
    settings = get_settings()
    
    try:
        from ..deps import _db_manager
        from ..auth import get_auth_service
        
        # Authenticate user
        user = None
        if not settings.auth_enabled:
            # Auth disabled - use default user
            from backend.auth import User
            user = User(id="default", email="dev@localhost")
        elif token:
            # Token from query param
            try:
                auth = get_auth_service()
                user = await auth.verify_token(token)
            except Exception as e:
                await websocket.send_json({"type": "error", "error": f"Authentication failed: {e}"})
                await websocket.close()
                return
        else:
            # Wait for auth message
            try:
                auth_msg = await websocket.receive_json()
                if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
                    await websocket.send_json({"type": "error", "error": "Authentication required. Send {type: 'auth', token: '<jwt>'}"})
                    await websocket.close()
                    return
                auth = get_auth_service()
                user = await auth.verify_token(auth_msg["token"])
                await websocket.send_json({"type": "auth_success"})
            except Exception as e:
                await websocket.send_json({"type": "error", "error": f"Authentication failed: {e}"})
                await websocket.close()
                return
        
        # Load config once with short-lived connection
        async with _db_manager as db:
            # Convert to CurrentUser for store access
            from ..authz import CurrentUser
            current_user = CurrentUser(
                id=user.id,
                role=getattr(user, "role", None) or user.metadata.get("role", "user"),
            )
            
            # Use secure store - enforces workspace membership
            store = ThreadStore(db)
            thread = await store.get(thread_id, user=current_user)
            if not thread:
                await websocket.send_json({"type": "error", "error": "Thread not found"})
                await websocket.close()
                return
            
            agent_data = await db.get_entity("agents", thread["agent_id"])
            if not agent_data:
                await websocket.send_json({"type": "error", "error": "Agent not found"})
                await websocket.close()
                return
        
        # Get cached provider
        provider_name = agent_data.get("provider", "openai")
        model = agent_data.get("model", "gpt-4")
        premium_provider = agent_data.get("premium_provider")
        premium_model = agent_data.get("premium_model")
        
        cached_provider = get_agent_provider(
            provider_name,
            model,
            api_key_fn=lambda p: _get_api_key(p, settings),
            premium_provider=premium_provider,
            premium_model=premium_model,
        )
        
        # Load agent using conn_factory for long-lived websocket
        agent = await Agent.from_thread(
            thread_id=thread_id,
            conn_factory=_db_manager,
            provider=cached_provider,
        )
        
        while True:
            try:
                data = await websocket.receive_json()
                message = data.get("message", "")
                if not message:
                    await websocket.send_json({"type": "error", "error": "Empty message"})
                    continue
                
                async for chunk in agent.stream(message, user_id=user.id):
                    await websocket.send_json({"type": "content", "content": chunk})
                
                await websocket.send_json({"type": "done"})
                
            except WebSocketDisconnect:
                break
            except ProviderRateLimitError:
                await websocket.send_json({
                    "type": "error", 
                    "error": "Rate limit exceeded. Please wait a moment and try again."
                })
            except Exception as e:
                print(f"[ERROR websocket] Exception: {type(e).__name__}: {e}", flush=True)
                await websocket.send_json({"type": "error", "error": str(e)})
                
    except WebSocketDisconnect:
        pass


@router.get("/{thread_id}/messages")
async def get_messages(
    thread_id: str,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get messages in a thread. Access controlled via workspace membership."""
    # Verify thread access via secure store
    store = ThreadStore(db)
    thread = await store.get(thread_id, user=current_user)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    
    messages = await db.find_entities(
        "messages",
        where_clause="[thread_id] = ?",
        params=(thread_id,),
        order_by="created_at ASC",
        limit=limit,
    )
    return {"messages": [_msg_to_response(m) for m in (messages or [])]}
