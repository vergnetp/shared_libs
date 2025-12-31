"""
Pydantic schemas - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel


# =============================================================================
# Workspace
# =============================================================================

class WorkspaceBase(BaseModel):
    name: str
    owner_id: str
    plan: Optional[str] = "free"


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    owner_id: Optional[str] = None
    plan: Optional[str] = None


class WorkspaceResponse(WorkspaceBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# WorkspaceMember
# =============================================================================

class WorkspaceMemberBase(BaseModel):
    user_id: str
    role: Optional[str] = "member"
    joined_at: Optional[datetime] = None


class WorkspaceMemberCreate(WorkspaceMemberBase):
    workspace_id: Optional[str] = None


class WorkspaceMemberUpdate(BaseModel):
    user_id: Optional[str] = None
    role: Optional[str] = None
    joined_at: Optional[datetime] = None


class WorkspaceMemberResponse(WorkspaceMemberBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# Project
# =============================================================================

class ProjectBase(BaseModel):
    name: str
    docker_hub_user: str
    version: Optional[str] = "latest"
    config_json: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None


class ProjectCreate(ProjectBase):
    workspace_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    docker_hub_user: Optional[str] = None
    version: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None


class ProjectResponse(ProjectBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# Credential
# =============================================================================

class CredentialBase(BaseModel):
    project_name: str
    env: str
    encrypted_blob: Optional[str] = None


class CredentialCreate(CredentialBase):
    workspace_id: Optional[str] = None


class CredentialUpdate(BaseModel):
    project_name: Optional[str] = None
    env: Optional[str] = None
    encrypted_blob: Optional[str] = None


class CredentialResponse(CredentialBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# DeploymentRun
# =============================================================================

class DeploymentRunBase(BaseModel):
    job_id: str
    project_name: str
    env: str
    services: Optional[Dict[str, Any]] = None
    status: Optional[str] = "queued"
    triggered_by: str
    triggered_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeploymentRunCreate(DeploymentRunBase):
    workspace_id: Optional[str] = None


class DeploymentRunUpdate(BaseModel):
    job_id: Optional[str] = None
    project_name: Optional[str] = None
    env: Optional[str] = None
    services: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    triggered_by: Optional[str] = None
    triggered_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeploymentRunResponse(DeploymentRunBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# DeploymentState
# =============================================================================

class DeploymentStateBase(BaseModel):
    project_name: str
    env: str
    state_json: Optional[Dict[str, Any]] = None
    last_deployed_at: Optional[datetime] = None
    last_deployed_by: Optional[str] = None


class DeploymentStateCreate(DeploymentStateBase):
    workspace_id: Optional[str] = None


class DeploymentStateUpdate(BaseModel):
    project_name: Optional[str] = None
    env: Optional[str] = None
    state_json: Optional[Dict[str, Any]] = None
    last_deployed_at: Optional[datetime] = None
    last_deployed_by: Optional[str] = None


class DeploymentStateResponse(DeploymentStateBase):
    id: str
    workspace_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
