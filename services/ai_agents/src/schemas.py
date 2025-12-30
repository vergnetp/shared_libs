"""
Pydantic schemas for API request/response models.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
from enum import Enum


# =============================================================================
# Enums
# =============================================================================

class Provider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    OLLAMA = "ollama"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# =============================================================================
# Agent Schemas
# =============================================================================

class ContextSchemaField(BaseModel):
    """A field in the context schema."""
    description: str = Field(..., description="What this field stores")


class AgentCreate(BaseModel):
    """Create a new agent definition."""
    name: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., description="Agent's role/persona")
    # Ownership: specify workspace_id for shared agent, or omit for personal agent
    workspace_id: Optional[str] = Field(None, description="Workspace for shared agent. Omit for personal agent.")
    provider: Provider = Provider.ANTHROPIC
    model: Optional[str] = None
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(4096, ge=1, le=32768)
    system_prompt: Optional[str] = None
    tools: list[str] = Field(default_factory=list, description="Tool names to enable")
    guardrails: list[str] = Field(default_factory=list, description="Guardrail names")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities for privileged actions")
    context_schema: Optional[dict[str, str]] = Field(
        None, 
        description="Schema for user context. Keys are field names, values are descriptions. "
                    "Set to {} for auto mode (agent decides what to remember)."
    )
    memory_strategy: str = Field("last_n", description="Memory strategy: last_n, sliding_window, none")
    memory_params: dict[str, Any] = Field(default_factory=lambda: {"n": 20}, description="Memory strategy params")
    # Premium escalation
    premium_provider: Optional[Provider] = Field(None, description="Premium provider for complex queries")
    premium_model: Optional[str] = Field(None, description="Premium model for complex queries")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """Update agent fields."""
    name: Optional[str] = None
    role: Optional[str] = None
    provider: Optional[Provider] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1, le=32768)
    system_prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    guardrails: Optional[list[str]] = None
    context_schema: Optional[dict[str, str]] = Field(
        None,
        description="Schema for user context. Set to null to disable context."
    )
    memory_strategy: Optional[str] = Field(None, description="Memory strategy: last_n, sliding_window, none")
    memory_params: Optional[dict[str, Any]] = Field(None, description="Memory strategy params")
    # Premium escalation
    premium_provider: Optional[Provider] = Field(None, description="Premium provider for complex queries")
    premium_model: Optional[str] = Field(None, description="Premium model for complex queries")
    metadata: Optional[dict[str, Any]] = None


class AgentResponse(BaseModel):
    """Agent definition response."""
    id: str
    name: str
    role: str
    # Ownership
    owner_user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    provider: str
    model: Optional[str]
    temperature: float
    max_tokens: int
    system_prompt: Optional[str]
    tools: list[str]
    guardrails: list[str]
    capabilities: list[str] = Field(default_factory=list)
    context_schema: Optional[dict[str, str]] = None
    memory_strategy: str = "last_n"
    memory_params: dict[str, Any] = Field(default_factory=lambda: {"n": 20})
    # Premium escalation
    premium_provider: Optional[str] = None
    premium_model: Optional[str] = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime]


# =============================================================================
# Thread Schemas
# =============================================================================

class ThreadCreate(BaseModel):
    """Create a new conversation thread. User is determined from auth token."""
    agent_id: str
    workspace_id: Optional[str] = Field(None, description="Workspace this thread belongs to (defaults to user's default workspace)")
    title: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadUpdate(BaseModel):
    """Update thread fields."""
    title: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    archived: Optional[bool] = None


class ThreadResponse(BaseModel):
    """Thread response."""
    id: str
    agent_id: str
    workspace_id: str
    title: Optional[str]
    user_id: Optional[str]  # Who created the thread (audit)
    config: dict[str, Any]
    metadata: dict[str, Any]
    message_count: int
    total_bytes: int
    archived: bool
    created_at: datetime
    updated_at: Optional[datetime]


class ThreadFork(BaseModel):
    """Fork a thread. New thread owned by authenticated user's workspace."""
    title: Optional[str] = None
    workspace_id: Optional[str] = Field(None, description="Target workspace (default: same as source)")
    up_to_message_id: Optional[str] = None


class ThreadStats(BaseModel):
    """Thread statistics."""
    thread_id: str
    message_count: int
    user_messages: int
    assistant_messages: int
    tool_messages: int
    total_bytes: int
    archived: bool
    created_at: Optional[datetime]
    forked_from: Optional[str]


# =============================================================================
# Message Schemas
# =============================================================================

class MessageCreate(BaseModel):
    """Create a message (chat input)."""
    content: str = Field(..., min_length=1)
    attachments: list[str] = Field(default_factory=list, description="Attachment IDs")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallSchema(BaseModel):
    """Tool call in assistant message."""
    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultSchema(BaseModel):
    """Tool execution result for UI display."""
    tool_call_id: str
    content: str
    is_error: bool = False


class MessageResponse(BaseModel):
    """Message response."""
    id: str
    thread_id: Optional[str] = None
    role: MessageRole
    content: str
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_call_id: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


# =============================================================================
# Chat Schemas
# =============================================================================

class ChatRequest(BaseModel):
    """Chat request (send message and get response)."""
    message: str = Field(..., min_length=1)
    attachments: list[str] = Field(default_factory=list)
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Per-request overrides (None = use agent default)
    stick_to_facts: Optional[bool] = Field(
        None, 
        description="Disables assumptions and educated guesses. None=use agent setting."
    )
    objective_responses: Optional[bool] = Field(
        None,
        description="Avoids taking sides, presents balanced perspectives. None=use agent setting."
    )
    temperature: Optional[float] = Field(
        None, ge=0, le=2,
        description="Override temperature for this request"
    )
    memory_strategy: Optional[str] = Field(
        None,
        description="Override memory strategy: 'last_n', 'none'. None=use agent setting."
    )
    memory_n: Optional[int] = Field(
        None, ge=1, le=100,
        description="Override number of messages to include (for last_n strategy). None=use agent setting."
    )


class SourceInfo(BaseModel):
    """Source citation from document search."""
    document_id: str = Field(..., description="ID of the source document")
    filename: str = Field(..., description="Original filename")
    page: Optional[int] = Field(None, description="Page number if applicable")
    chunk_preview: str = Field(..., description="Preview of the relevant text")
    score: Optional[float] = Field(None, description="Relevance score")
    download_url: Optional[str] = Field(None, description="URL to download the original document")


class ChatResponse(BaseModel):
    """Chat response (non-streaming)."""
    message: MessageResponse
    usage: dict[str, int] = Field(default_factory=dict)
    cost: float = 0.0
    duration_ms: int = 0
    context_enabled: bool = Field(False, description="Whether context is enabled for this agent")
    user_context: Optional[dict[str, Any]] = Field(None, description="Updated user context after this message")
    sources: list[SourceInfo] = Field(default_factory=list, description="Document sources cited in the response")
    tool_results: list[ToolResultSchema] = Field(default_factory=list, description="Tool execution results for UI display")


class StreamChunk(BaseModel):
    """Streaming response chunk."""
    type: str  # "content", "tool_call", "done", "error"
    content: Optional[str] = None
    tool_call: Optional[ToolCallSchema] = None
    usage: Optional[dict[str, int]] = None
    cost: Optional[float] = None
    error: Optional[str] = None


# =============================================================================
# Document Schemas
# =============================================================================

class DocumentUpload(BaseModel):
    """Document upload metadata."""
    entity_id: str = Field(..., description="Entity to associate with (e.g., property_id)")
    title: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    """Uploaded document info."""
    id: str
    entity_id: str
    filename: str
    content_type: str
    size_bytes: int
    chunk_count: int
    title: Optional[str]
    tags: list[str]
    # Ownership
    owner_user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    visibility: str = "private"  # 'private' | 'workspace'
    agent_id: Optional[str] = None
    metadata: dict[str, Any]
    created_at: Optional[datetime] = None


class SearchRequest(BaseModel):
    """Search documents."""
    query: str = Field(..., min_length=1)
    entity_id: Optional[str] = None
    top_k: int = Field(10, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Search result item."""
    document_id: str
    content: str
    score: float
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    """Search response."""
    results: list[SearchResult]
    total: int
    query: str


# =============================================================================
# Monitoring Schemas (app-specific metrics only)
# =============================================================================


class MetricsResponse(BaseModel):
    """Metrics response."""
    total_requests: int
    total_tokens: int
    total_cost: float
    agents: int
    threads: int
    messages: int
    documents: int


class CostBreakdown(BaseModel):
    """Cost breakdown by provider/model."""
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float


class UsageResponse(BaseModel):
    """Usage statistics."""
    period: str
    total_cost: float
    breakdown: list[CostBreakdown]
    budget_remaining: float
    budget_used_percent: float


# =============================================================================
# Error Schemas
# =============================================================================

class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


class ValidationError(BaseModel):
    """Validation error detail."""
    loc: list[str]
    msg: str
    type: str


# =============================================================================
# User Context Schemas
# =============================================================================

class ContextSchemaUpdate(BaseModel):
    """Update context schema fields."""
    fields: dict[str, str] = Field(
        ..., 
        description="Fields to add/update. Key is field name, value is description."
    )


class ContextSchemaResponse(BaseModel):
    """Context schema response."""
    agent_id: str
    schema: Optional[dict[str, str]]
    enabled: bool


class UserContextUpdate(BaseModel):
    """Update user context."""
    updates: dict[str, Any] = Field(..., description="Context updates (deep merged)")
    reason: str = Field(..., description="Reason for update")


class UserContextResponse(BaseModel):
    """User context response."""
    user_id: str
    agent_id: Optional[str]
    context: dict[str, Any]
    updated_at: Optional[datetime] = None