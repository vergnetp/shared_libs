"""
API-specific schemas for deploy_api.

These extend or customize the auto-generated schemas in _gen/schemas.py
for the specific API endpoints.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


# =============================================================================
# Workspace API Schemas
# =============================================================================

class WorkspaceCreateRequest(BaseModel):
    """Create workspace request."""
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z][a-z0-9_-]*$')


class WorkspaceAPIResponse(BaseModel):
    """Workspace API response."""
    id: str
    name: str
    owner_id: str
    plan: str = "free"
    role: Optional[str] = None  # User's role in the workspace
    created_at: datetime


class WorkspaceMemberAddRequest(BaseModel):
    """Add member to workspace."""
    user_id: str
    role: str = "member"  # member, admin, owner


class WorkspaceMemberAPIResponse(BaseModel):
    """Workspace member API response."""
    user_id: str
    workspace_id: str
    role: str
    joined_at: datetime


# =============================================================================
# Project API Schemas
# =============================================================================

class ProjectCreateRequest(BaseModel):
    """Create project request."""
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z][a-z0-9_-]*$')
    docker_hub_user: str
    version: str = "latest"


class ProjectUpdateRequest(BaseModel):
    """Update project request."""
    docker_hub_user: Optional[str] = None
    version: Optional[str] = None


class ProjectAPIResponse(BaseModel):
    """Project API response."""
    id: str
    workspace_id: str
    name: str
    docker_hub_user: str
    version: str
    services: Dict[str, Any] = {}
    environments: List[str] = []
    created_at: datetime
    updated_at: datetime
    created_by: str


# =============================================================================
# Service Schemas
# =============================================================================

class ServiceAddRequest(BaseModel):
    """Add a service to a project."""
    name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z][a-z0-9_-]*$')
    
    # For standard services (postgres, redis, etc) - just name is enough
    # For custom services:
    image: Optional[str] = None
    dockerfile: Optional[str] = None
    dockerfile_content: Optional[Dict[str, str]] = None
    git_repo: Optional[str] = None  # e.g., "github.com/user/repo@branch"
    
    # Resource config
    ports: Optional[List[str]] = None
    env_vars: Optional[Dict[str, str]] = None
    servers_count: int = 1
    server_zone: str = "lon1"
    cpu: int = 1
    memory: int = 1024
    
    # Optional
    domain: Optional[str] = None
    health_check: Optional[str] = None


class ServiceAPIResponse(BaseModel):
    """Service API response."""
    name: str
    image: Optional[str] = None
    dockerfile: Optional[str] = None
    git_repo: Optional[str] = None
    ports: List[str] = []
    servers_count: int = 1
    server_zone: str = "lon1"


# =============================================================================
# Credentials Schemas
# =============================================================================

class CredentialsSetRequest(BaseModel):
    """Set credentials for a project/env."""
    digitalocean_token: str
    docker_hub_user: Optional[str] = None
    docker_hub_password: Optional[str] = None
    
    # Optional service passwords (auto-generated if not provided)
    postgres_password: Optional[str] = None
    redis_password: Optional[str] = None


class CredentialsAPIResponse(BaseModel):
    """Credentials API response (no secrets exposed)."""
    workspace_id: str
    project_name: str
    env: str
    has_digitalocean: bool
    has_docker_hub: bool
    updated_at: datetime


# =============================================================================
# Deployment Schemas
# =============================================================================

class DeploymentTriggerRequest(BaseModel):
    """Trigger a deployment."""
    env: str = "prod"
    services: Optional[List[str]] = None  # None = all services
    force: bool = False


class DeploymentAPIResponse(BaseModel):
    """Deployment trigger response."""
    job_id: str
    workspace_id: str
    project_name: str
    env: str
    status: str  # queued, running, succeeded, failed
    triggered_by: str
    triggered_at: datetime


class DeploymentStatusResponse(BaseModel):
    """Deployment status with progress."""
    job_id: str
    status: str
    step: Optional[str] = None
    progress: Optional[int] = None  # 0-100
    logs: List[str] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeploymentHistoryResponse(BaseModel):
    """Deployment history list."""
    deployments: List[DeploymentAPIResponse]
    total: int


# =============================================================================
# Live Status Schemas
# =============================================================================

class ContainerStatusResponse(BaseModel):
    """Container status from live query."""
    name: str
    service: str
    server_ip: str
    status: str  # running, stopped, etc
    port: Optional[int] = None
    created_at: Optional[str] = None


class ProjectStatusResponse(BaseModel):
    """Project live status."""
    project_name: str
    env: str
    containers: List[ContainerStatusResponse]
    nginx_configured: bool
    healthy: bool


# =============================================================================
# Error Response
# =============================================================================

class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    code: Optional[str] = None
