"""
Deployment worker - runs actual deployments via infra code.

This is the bridge between deploy_api and your existing infra library.
"""
import asyncio
import traceback
from typing import Dict, Any

from backend.app_kernel import get_logger
from backend.app_kernel.jobs import get_job_client

logger = get_logger()


async def run_deployment(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a deployment using the infra Deployer.
    
    This runs your existing deployment code as a background job.
    
    Args:
        job_id: Job ID for status updates
        payload: Deployment parameters:
            - workspace_id: Tenant ID (maps to infra's "user")
            - project_name: Project name
            - env: Environment (prod, uat, dev)
            - services: Optional list of services to deploy
            - force: Force rebuild
            - triggered_by: User who triggered
    
    Returns:
        Deployment result
    """
    workspace_id = payload["workspace_id"]
    project_name = payload["project_name"]
    env = payload["env"]
    services = payload.get("services")
    force = payload.get("force", False)
    triggered_by = payload.get("triggered_by", "system")
    
    logger.info(
        f"Starting deployment",
        extra={
            "job_id": job_id,
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "services": services,
            "triggered_by": triggered_by,
        }
    )
    
    try:
        # Update job progress
        job_client = get_job_client()
        await job_client.update_progress(job_id, step="initializing", progress=5)
        
        # Import infra modules
        from backend.infra.deployer import Deployer
        from backend.infra.config_storage import ConfigStorage
        
        # Get credentials from DB
        from .deps import get_credentials_store_sync, get_db_sync
        db = get_db_sync()
        creds_store = get_credentials_store_sync(db)
        credentials = await creds_store.get(workspace_id, project_name, env)
        
        if not credentials:
            raise ValueError(f"No credentials found for {workspace_id}/{project_name}/{env}")
        
        await job_client.update_progress(job_id, step="loading_config", progress=10)
        
        # Create deployer
        # workspace_id maps to infra's "user" parameter
        deployer = Deployer(user=workspace_id, project_name=project_name)
        
        await job_client.update_progress(job_id, step="building_images", progress=20)
        
        # Run deployment
        # This is your existing infra code
        if services:
            # Deploy specific services
            result = deployer.deploy_services(
                env=env,
                services=services,
                force_rebuild=force,
                credentials=credentials,
            )
        else:
            # Deploy all services
            result = deployer.deploy(
                env=env,
                force_rebuild=force,
                credentials=credentials,
            )
        
        await job_client.update_progress(job_id, step="completed", progress=100)
        
        logger.info(
            f"Deployment completed",
            extra={
                "job_id": job_id,
                "workspace_id": workspace_id,
                "project_name": project_name,
                "env": env,
                "result": result,
            }
        )
        
        return {
            "status": "success",
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "result": result,
        }
        
    except Exception as e:
        logger.error(
            f"Deployment failed",
            extra={
                "job_id": job_id,
                "workspace_id": workspace_id,
                "project_name": project_name,
                "env": env,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )
        raise


async def run_rollback(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a rollback using the infra RollbackManager.
    
    Args:
        job_id: Job ID
        payload: Rollback parameters
    
    Returns:
        Rollback result
    """
    workspace_id = payload["workspace_id"]
    project_name = payload["project_name"]
    env = payload["env"]
    triggered_by = payload.get("triggered_by", "system")
    
    logger.info(
        f"Starting rollback",
        extra={
            "job_id": job_id,
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "triggered_by": triggered_by,
        }
    )
    
    try:
        job_client = get_job_client()
        await job_client.update_progress(job_id, step="initializing", progress=10)
        
        # Get credentials
        from .deps import get_credentials_store_sync, get_db_sync
        db = get_db_sync()
        creds_store = get_credentials_store_sync(db)
        credentials = await creds_store.get(workspace_id, project_name, env)
        
        if not credentials:
            raise ValueError(f"No credentials found for {workspace_id}/{project_name}/{env}")
        
        await job_client.update_progress(job_id, step="rolling_back", progress=30)
        
        # Import and run rollback
        from backend.infra.rollback_manager import RollbackManager
        
        rollback_mgr = RollbackManager(
            user=workspace_id,
            project_name=project_name,
        )
        
        result = rollback_mgr.rollback(
            env=env,
            credentials=credentials,
        )
        
        await job_client.update_progress(job_id, step="completed", progress=100)
        
        logger.info(
            f"Rollback completed",
            extra={
                "job_id": job_id,
                "workspace_id": workspace_id,
                "project_name": project_name,
                "env": env,
            }
        )
        
        return {
            "status": "success",
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "result": result,
        }
        
    except Exception as e:
        logger.error(
            f"Rollback failed",
            extra={
                "job_id": job_id,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )
        raise


# Task registry for app_kernel jobs
TASKS = {
    "deploy": run_deployment,
    "rollback": run_rollback,
}
