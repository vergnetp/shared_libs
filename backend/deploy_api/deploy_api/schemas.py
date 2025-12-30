"""
Pydantic schemas for deploy API.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


# =============================================================================
# Workspaces
# =============================================================================

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z][a-z0-9_-]*$')
    

class WorkspaceResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    plan: str = "free"
    created_at: datetime


class WorkspaceMemberAdd(BaseModel):
    user_id: str
    role: str = "member"  # member, admin, owner


class WorkspaceMemberResponse(BaseModel):
    user_id: str
    workspace_id: str
    role: str
    joined_at: datetime


# =============================================================================
# Projects
# =============================================================================

class ServiceConfig(BaseModel):
    """Configuration for a single service."""
    name: str
    image: Optional[str] = None
    dockerfile: Optional[str] = None
    dockerfile_content: Optional[Dict[str, str]] = None
    git_repo: Optional[str] = None
    ports: Optional[List[str]] = None
    env_vars: Optional[Dict[str, str]] = None
    volumes: Optional[List[str]] = None
    servers_count: int = 1
    server_zone: str = "lon1"
    cpu: int = 1
    memory: int = 1024
    startup_order: int = 5
    health_check: Optional[str] = None
    domain: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z][a-z0-9_-]*$')
    docker_hub_user: str
    version: str = "latest"


class ProjectUpdate(BaseModel):
    docker_hub_user: Optional[str] = None
    version: Optional[str] = None


class ProjectResponse(BaseModel):
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


class ServiceAdd(BaseModel):
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


class ServiceResponse(BaseModel):
    name: str
    image: Optional[str]
    dockerfile: Optional[str]
    git_repo: Optional[str]
    ports: List[str] = []
    servers_count: int
    server_zone: str


# =============================================================================
# Credentials
# =============================================================================

class CredentialsSet(BaseModel):
    """Set credentials for a project/env."""
    digitalocean_token: str
    docker_hub_user: Optional[str] = None
    docker_hub_password: Optional[str] = None
    
    # Optional service passwords (auto-generated if not provided)
    postgres_password: Optional[str] = None
    redis_password: Optional[str] = None


class CredentialsResponse(BaseModel):
    workspace_id: str
    project_name: str
    env: str
    has_digitalocean: bool
    has_docker_hub: bool
    updated_at: datetime


# =============================================================================
# Deployments
# =============================================================================

class DeploymentTrigger(BaseModel):
    """Trigger a deployment."""
    env: str = "prod"
    services: Optional[List[str]] = None  # None = all services
    force: bool = False


class DeploymentResponse(BaseModel):
    job_id: str
    workspace_id: str
    project_name: str
    env: str
    status: str  # queued, running, succeeded, failed
    triggered_by: str
    triggered_at: datetime


class DeploymentStatus(BaseModel):
    job_id: str
    status: str
    step: Optional[str] = None
    progress: Optional[int] = None  # 0-100
    logs: List[str] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeploymentHistory(BaseModel):
    deployments: List[DeploymentResponse]
    total: int


# =============================================================================
# Live Status
# =============================================================================

class ContainerStatus(BaseModel):
    name: str
    service: str
    server_ip: str
    status: str  # running, stopped, etc
    port: Optional[int] = None
    created_at: Optional[str] = None


class ProjectStatus(BaseModel):
    project_name: str
    env: str
    containers: List[ContainerStatus]
    nginx_configured: bool
    healthy: bool


# =============================================================================
# Errors
# =============================================================================

class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
