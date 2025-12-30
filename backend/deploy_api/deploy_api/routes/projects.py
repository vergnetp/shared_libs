"""
Project management routes.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, status

from backend.app_kernel.auth import get_current_user, UserIdentity
from backend.app_kernel.access import require_workspace_member

from ..schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ServiceAdd,
    ServiceResponse,
    CredentialsSet,
    CredentialsResponse,
    ErrorResponse,
)
from ..deps import get_project_store, get_credentials_store, get_workspace_store

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["projects"])


def _parse_config(project: dict) -> dict:
    """Parse config_json if needed."""
    config = project.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    return config


def _project_response(project: dict) -> ProjectResponse:
    """Convert DB project to response."""
    config = _parse_config(project)
    project_config = config.get("project", {})
    
    return ProjectResponse(
        id=project["id"],
        workspace_id=project["workspace_id"],
        name=project["name"],
        docker_hub_user=project["docker_hub_user"],
        version=project["version"],
        services=project_config.get("services", {}),
        environments=list(project_config.get("environments", {}).keys()),
        created_at=project["created_at"],
        updated_at=project["updated_at"],
        created_by=project["created_by"],
    )


# =============================================================================
# Projects CRUD
# =============================================================================

@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    workspace_id: str,
    data: ProjectCreate,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Create a new project in the workspace."""
    # Check if project already exists
    existing = await project_store.get(workspace_id, data.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project '{data.name}' already exists",
        )
    
    project = await project_store.create(
        workspace_id=workspace_id,
        name=data.name,
        docker_hub_user=data.docker_hub_user,
        version=data.version,
        created_by=current_user.id,
    )
    
    return _project_response(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    workspace_id: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """List all projects in the workspace."""
    projects = await project_store.list(workspace_id)
    return [_project_response(p) for p in projects]


@router.get(
    "/{project_name}",
    response_model=ProjectResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_project(
    workspace_id: str,
    project_name: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Get project details."""
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    return _project_response(project)


@router.patch(
    "/{project_name}",
    response_model=ProjectResponse,
    responses={404: {"model": ErrorResponse}},
)
async def update_project(
    workspace_id: str,
    project_name: str,
    data: ProjectUpdate,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Update project settings."""
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    
    project = await project_store.update(workspace_id, project_name, **updates)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    return _project_response(project)


@router.delete(
    "/{project_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_project(
    workspace_id: str,
    project_name: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Delete a project."""
    deleted = await project_store.delete(workspace_id, project_name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


# =============================================================================
# Services
# =============================================================================

@router.post(
    "/{project_name}/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def add_service(
    workspace_id: str,
    project_name: str,
    data: ServiceAdd,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Add a service to the project."""
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    # Build service config
    service_config = {}
    
    if data.image:
        service_config["image"] = data.image
    if data.dockerfile:
        service_config["dockerfile"] = data.dockerfile
    if data.dockerfile_content:
        service_config["dockerfile_content"] = data.dockerfile_content
    if data.git_repo:
        service_config["git_repo"] = data.git_repo
    if data.ports:
        service_config["ports"] = data.ports
    if data.env_vars:
        service_config["env_vars"] = data.env_vars
    if data.domain:
        service_config["domain"] = data.domain
    if data.health_check:
        service_config["health_check"] = data.health_check
    
    # Resource config
    service_config["servers_count"] = data.servers_count
    service_config["server_zone"] = data.server_zone
    service_config["cpu"] = data.cpu
    service_config["memory"] = data.memory
    
    await project_store.add_service(workspace_id, project_name, data.name, service_config)
    
    return ServiceResponse(
        name=data.name,
        image=data.image,
        dockerfile=data.dockerfile,
        git_repo=data.git_repo,
        ports=data.ports or [],
        servers_count=data.servers_count,
        server_zone=data.server_zone,
    )


@router.get(
    "/{project_name}/services",
    response_model=list[ServiceResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_services(
    workspace_id: str,
    project_name: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """List services in the project."""
    config = await project_store.get_config(workspace_id, project_name)
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    services = config.get("project", {}).get("services", {})
    
    return [
        ServiceResponse(
            name=name,
            image=svc.get("image"),
            dockerfile=svc.get("dockerfile"),
            git_repo=svc.get("git_repo"),
            ports=svc.get("ports", []),
            servers_count=svc.get("servers_count", 1),
            server_zone=svc.get("server_zone", "lon1"),
        )
        for name, svc in services.items()
    ]


@router.delete(
    "/{project_name}/services/{service_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def remove_service(
    workspace_id: str,
    project_name: str,
    service_name: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
):
    """Remove a service from the project."""
    removed = await project_store.remove_service(workspace_id, project_name, service_name)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project or service not found")


# =============================================================================
# Credentials
# =============================================================================

@router.put(
    "/{project_name}/credentials/{env}",
    response_model=CredentialsResponse,
    responses={404: {"model": ErrorResponse}},
)
async def set_credentials(
    workspace_id: str,
    project_name: str,
    env: str,
    data: CredentialsSet,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
    credentials_store=Depends(get_credentials_store),
):
    """Set credentials for a project environment."""
    # Verify project exists
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    # Store credentials
    creds = {
        "digitalocean_token": data.digitalocean_token,
    }
    if data.docker_hub_user:
        creds["docker_hub_user"] = data.docker_hub_user
    if data.docker_hub_password:
        creds["docker_hub_password"] = data.docker_hub_password
    if data.postgres_password:
        creds["postgres_password"] = data.postgres_password
    if data.redis_password:
        creds["redis_password"] = data.redis_password
    
    await credentials_store.set(workspace_id, project_name, env, creds)
    
    from datetime import datetime
    return CredentialsResponse(
        workspace_id=workspace_id,
        project_name=project_name,
        env=env,
        has_digitalocean=True,
        has_docker_hub=bool(data.docker_hub_user),
        updated_at=datetime.utcnow(),
    )


@router.get(
    "/{project_name}/credentials/{env}",
    response_model=CredentialsResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_credentials_status(
    workspace_id: str,
    project_name: str,
    env: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    credentials_store=Depends(get_credentials_store),
):
    """Check if credentials are set (doesn't return actual credentials)."""
    exists = await credentials_store.exists(workspace_id, project_name, env)
    
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credentials not set")
    
    creds = await credentials_store.get(workspace_id, project_name, env)
    
    from datetime import datetime
    return CredentialsResponse(
        workspace_id=workspace_id,
        project_name=project_name,
        env=env,
        has_digitalocean=bool(creds.get("digitalocean_token")),
        has_docker_hub=bool(creds.get("docker_hub_user")),
        updated_at=datetime.utcnow(),
    )


@router.delete(
    "/{project_name}/credentials/{env}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_credentials(
    workspace_id: str,
    project_name: str,
    env: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    credentials_store=Depends(get_credentials_store),
):
    """Delete credentials for a project environment."""
    await credentials_store.delete(workspace_id, project_name, env)
