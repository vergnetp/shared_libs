"""
Deployment management routes.
"""

from fastapi import APIRouter, Depends, HTTPException, status

try:
    from backend.app_kernel.auth import get_current_user, UserIdentity
    from backend.app_kernel.access import require_workspace_member
    from backend.app_kernel.jobs import get_job_client
except ImportError:
    UserIdentity = dict
    def get_current_user(): pass
    def require_workspace_member(): pass
    def get_job_client(): pass

from ..schemas import (
    DeploymentTriggerRequest,
    DeploymentAPIResponse,
    DeploymentStatusResponse,
    DeploymentHistoryResponse,
    ProjectStatusResponse,
    ContainerStatusResponse,
    ErrorResponse,
)
from ..deps import (
    get_project_store,
    get_credentials_store,
    get_deployment_store,
)


router = APIRouter(prefix="/workspaces/{workspace_id}/projects/{project_name}", tags=["deployments"])


# =============================================================================
# Trigger Deployment
# =============================================================================

@router.post(
    "/deploy",
    response_model=DeploymentAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
    },
)
async def trigger_deployment(
    workspace_id: str,
    project_name: str,
    data: DeploymentTriggerRequest,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
    credentials_store=Depends(get_credentials_store),
    deployment_store=Depends(get_deployment_store),
):
    """
    Trigger a deployment.
    
    Returns immediately with a job_id. Poll /deploy/{job_id} for status.
    """
    # Verify project exists
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    # Verify credentials are set
    creds_exist = await credentials_store.exists(workspace_id, project_name, data.env)
    if not creds_exist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credentials not set for environment '{data.env}'. Set credentials first.",
        )
    
    # Get job client
    try:
        job_client = get_job_client()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue not available. Check Redis connection.",
        )
    
    # Enqueue deployment job
    job_id = await job_client.enqueue(
        "deploy",
        {
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": data.env,
            "services": data.services,
            "force": data.force,
            "triggered_by": current_user.id,
        },
    )
    
    # Record deployment run
    run = await deployment_store.create_run(
        job_id=job_id,
        workspace_id=workspace_id,
        project_name=project_name,
        env=data.env,
        triggered_by=current_user.id,
        services=data.services,
    )
    
    return DeploymentAPIResponse(
        job_id=job_id,
        workspace_id=workspace_id,
        project_name=project_name,
        env=data.env,
        status="queued",
        triggered_by=current_user.id,
        triggered_at=run["triggered_at"],
    )


# =============================================================================
# Deployment Status
# =============================================================================

@router.get(
    "/deploy/{job_id}",
    response_model=DeploymentStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_deployment_status(
    workspace_id: str,
    project_name: str,
    job_id: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    deployment_store=Depends(get_deployment_store),
):
    """Get deployment status and logs."""
    # Get deployment run
    run = await deployment_store.get_run(job_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    
    # Verify it belongs to this project
    if run["workspace_id"] != workspace_id or run["project_name"] != project_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    
    # Get job status from kernel
    try:
        job_client = get_job_client()
        job_status = await job_client.get_status(job_id)
    except RuntimeError:
        job_status = None
    
    # Merge job status with run record
    status_val = run["status"]
    step = None
    progress = None
    logs = []
    
    if job_status:
        status_val = job_status.get("status", status_val)
        step = job_status.get("step")
        progress = job_status.get("progress")
        logs = job_status.get("logs", [])
    
    return DeploymentStatusResponse(
        job_id=job_id,
        status=status_val,
        step=step,
        progress=progress,
        logs=logs,
        started_at=run.get("started_at"),
        completed_at=run.get("completed_at"),
        result=run.get("result"),
        error=run.get("error"),
    )


# =============================================================================
# Deployment History
# =============================================================================

@router.get(
    "/deployments",
    response_model=DeploymentHistoryResponse,
)
async def list_deployments(
    workspace_id: str,
    project_name: str,
    env: str = None,
    limit: int = 50,
    current_user: UserIdentity = Depends(require_workspace_member),
    deployment_store=Depends(get_deployment_store),
):
    """List deployment history for the project."""
    runs = await deployment_store.list_runs(
        workspace_id=workspace_id,
        project_name=project_name,
        env=env,
        limit=limit,
    )
    
    return DeploymentHistoryResponse(
        deployments=[
            DeploymentAPIResponse(
                job_id=r["job_id"],
                workspace_id=r["workspace_id"],
                project_name=r["project_name"],
                env=r["env"],
                status=r["status"],
                triggered_by=r["triggered_by"],
                triggered_at=r["triggered_at"],
            )
            for r in runs
        ],
        total=len(runs),
    )


# =============================================================================
# Live Status
# =============================================================================

@router.get(
    "/status/{env}",
    response_model=ProjectStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_live_status(
    workspace_id: str,
    project_name: str,
    env: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
    credentials_store=Depends(get_credentials_store),
):
    """
    Get live status of deployed containers.
    
    Queries actual running containers via the infra layer.
    """
    # Verify project exists
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    # Get credentials for querying servers
    creds = await credentials_store.get(workspace_id, project_name, env)
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credentials not set for environment '{env}'",
        )
    
    # Query live status via infra
    try:
        from infra.live_deployment_query import LiveDeploymentQuery
        
        live = LiveDeploymentQuery(
            user=workspace_id,
            project_name=project_name,
            env=env,
            credentials=creds,
        )
        
        containers_data = live.get_running_containers()
        
        containers = [
            ContainerStatusResponse(
                name=c.get("name", ""),
                service=c.get("service", ""),
                server_ip=c.get("server_ip", ""),
                status=c.get("status", "unknown"),
                port=c.get("port"),
                created_at=c.get("created_at"),
            )
            for c in containers_data
        ]
        
        nginx_ok = live.check_nginx_configured()
        healthy = all(c.status == "running" for c in containers)
        
    except ImportError:
        # Infra not available - return empty
        containers = []
        nginx_ok = False
        healthy = False
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying live status: {e}",
        )
    
    return ProjectStatusResponse(
        project_name=project_name,
        env=env,
        containers=containers,
        nginx_configured=nginx_ok,
        healthy=healthy,
    )


# =============================================================================
# Rollback
# =============================================================================

@router.post(
    "/rollback/{env}",
    response_model=DeploymentAPIResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": ErrorResponse}},
)
async def trigger_rollback(
    workspace_id: str,
    project_name: str,
    env: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    project_store=Depends(get_project_store),
    credentials_store=Depends(get_credentials_store),
    deployment_store=Depends(get_deployment_store),
):
    """
    Rollback to previous deployment.
    
    Uses the toggle strategy - switches back to the previous container version.
    """
    # Verify project and credentials
    project = await project_store.get(workspace_id, project_name)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    
    creds_exist = await credentials_store.exists(workspace_id, project_name, env)
    if not creds_exist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credentials not set for environment '{env}'",
        )
    
    # Get job client
    try:
        job_client = get_job_client()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue not available",
        )
    
    # Enqueue rollback job
    job_id = await job_client.enqueue(
        "rollback",
        {
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "triggered_by": current_user.id,
        },
    )
    
    # Record
    run = await deployment_store.create_run(
        job_id=job_id,
        workspace_id=workspace_id,
        project_name=project_name,
        env=env,
        triggered_by=current_user.id,
    )
    
    return DeploymentAPIResponse(
        job_id=job_id,
        workspace_id=workspace_id,
        project_name=project_name,
        env=env,
        status="queued",
        triggered_by=current_user.id,
        triggered_at=run["triggered_at"],
    )
