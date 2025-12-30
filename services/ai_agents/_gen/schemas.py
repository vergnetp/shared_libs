"""
Pydantic schemas - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel


class WorkspaceBase(BaseModel):
    name: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class WorkspaceCreate(WorkspaceBase):
    pass

class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class WorkspaceResponse(WorkspaceBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WorkspaceMemberBase(BaseModel):
    user_id: str
    role: Optional[str] = 'member'

class WorkspaceMemberCreate(WorkspaceMemberBase):
    workspace_id: Optional[str] = None

class WorkspaceMemberUpdate(BaseModel):
    user_id: Optional[str] = None
    role: Optional[str] = None

class WorkspaceMemberResponse(WorkspaceMemberBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AgentBase(BaseModel):
    name: str
    system_prompt: Optional[str] = None
    model: Optional[str] = 'llama-3.3-70b-versatile'
    premium_model: Optional[str] = None
    temperature: Optional[float] = 0.5
    max_tokens: Optional[int] = 4096
    tools: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, Any]] = None
    memory_strategy: Optional[str] = 'last_n'
    memory_params: Optional[Dict[str, Any]] = None
    context_schema: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    owner_user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

class AgentCreate(AgentBase):
    workspace_id: Optional[str] = None

class AgentUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    premium_model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, Any]] = None
    memory_strategy: Optional[str] = None
    memory_params: Optional[Dict[str, Any]] = None
    context_schema: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    owner_user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

class AgentResponse(AgentBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ThreadBase(BaseModel):
    agent_id: str
    title: Optional[str] = None
    summary: Optional[str] = None
    turn_count: Optional[int] = None
    token_count: Optional[int] = None
    owner_user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ThreadCreate(ThreadBase):
    workspace_id: Optional[str] = None

class ThreadUpdate(BaseModel):
    agent_id: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    turn_count: Optional[int] = None
    token_count: Optional[int] = None
    owner_user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ThreadResponse(ThreadBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MessageBase(BaseModel):
    thread_id: str
    role: str
    content: Optional[str] = None
    tool_calls: Optional[Dict[str, Any]] = None
    tool_results: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    latency_ms: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class MessageCreate(MessageBase):
    pass

class MessageUpdate(BaseModel):
    thread_id: Optional[str] = None
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[Dict[str, Any]] = None
    tool_results: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    latency_ms: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class MessageResponse(MessageBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DocumentBase(BaseModel):
    agent_id: Optional[str] = None
    filename: str
    content_type: Optional[str] = None
    size: Optional[int] = None
    chunk_count: Optional[int] = None
    status: Optional[str] = 'pending'
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    processed_at: Optional[datetime] = None

class DocumentCreate(DocumentBase):
    workspace_id: Optional[str] = None

class DocumentUpdate(BaseModel):
    agent_id: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    chunk_count: Optional[int] = None
    status: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    processed_at: Optional[datetime] = None

class DocumentResponse(DocumentBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DocumentChunkBase(BaseModel):
    document_id: str
    chunk_index: int
    content: str
    embedding: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class DocumentChunkCreate(DocumentChunkBase):
    pass

class DocumentChunkUpdate(BaseModel):
    document_id: Optional[str] = None
    chunk_index: Optional[int] = None
    content: Optional[str] = None
    embedding: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class DocumentChunkResponse(DocumentChunkBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserContextBase(BaseModel):
    user_id: str
    context_type: str
    content: str
    metadata: Optional[Dict[str, Any]] = None
    expires_at: Optional[datetime] = None

class UserContextCreate(UserContextBase):
    workspace_id: Optional[str] = None

class UserContextUpdate(BaseModel):
    user_id: Optional[str] = None
    context_type: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    expires_at: Optional[datetime] = None

class UserContextResponse(UserContextBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AnalyticsDailyBase(BaseModel):
    date: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    message_count: Optional[int] = None
    thread_count: Optional[int] = None
    token_count: Optional[int] = None
    cost: Optional[float] = None
    avg_latency_ms: Optional[int] = None
    error_count: Optional[int] = None

class AnalyticsDailyCreate(AnalyticsDailyBase):
    workspace_id: Optional[str] = None

class AnalyticsDailyUpdate(BaseModel):
    date: Optional[str] = None
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    message_count: Optional[int] = None
    thread_count: Optional[int] = None
    token_count: Optional[int] = None
    cost: Optional[float] = None
    avg_latency_ms: Optional[int] = None
    error_count: Optional[int] = None

class AnalyticsDailyResponse(AnalyticsDailyBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

