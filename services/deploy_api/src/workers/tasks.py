"""
Deployment worker tasks - runs actual deployments via infra code.

This is the bridge between deploy_api and your existing infra library.

Note: Job status (running/completed/failed) is automatically tracked in Redis
by the kernel worker. Tasks can optionally call update_progress() for step updates.
"""

import json
import traceback
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from backend.app_kernel import get_logger
    from backend.app_kernel.jobs import get_job_client
    from backend.app_kernel.db import get_db_connection
except ImportError:
    import logging
    def get_logger():
        return logging.getLogger(__name__)
    def get_job_client():
        raise RuntimeError("Job client not available")
    def get_db_connection():
        raise RuntimeError("DB connection not available")


def _get_logger():
    """Lazy logger getter to avoid import-time issues."""
    return get_logger()


async def _update_progress(job_id: str, step: str, progress: int):
    """Update job progress in Redis via kernel."""
    try:
        job_client = get_job_client()
        await job_client.update_progress(job_id, step=step, progress=progress)
    except Exception:
        pass  # Progress updates are optional


async def _sync_run_to_db(
    job_id: str, 
    status: str, 
    error: Optional[str] = None,
    result: Optional[Dict] = None,
):
    """
    Sync job status to deployment_runs table for historical records.
    
    The primary status is in Redis (managed by kernel). This syncs to DB
    for persistence beyond Redis TTL and for domain-specific queries.
    """
    try:
        async with get_db_connection() as conn:
            runs = await conn.find_entities(
                "deployment_runs",
                where_clause="job_id = ?",
                params=(job_id,),
                limit=1,
            )
            
            if not runs:
                return
            
            run = runs[0]
            run["status"] = status
            
            if status == "running" and not run.get("started_at"):
                run["started_at"] = datetime.utcnow().isoformat()
            
            if status in ("completed", "failed"):
                run["completed_at"] = datetime.utcnow().isoformat()
                
            if error:
                run["error"] = error
            if result:
                run["result_json"] = json.dumps(result)
            
            await conn.save_entity("deployment_runs", run)
            
    except Exception as e:
        _get_logger().warning(f"Failed to sync run to DB: {e}")


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
    logger = _get_logger()
    
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
    
    # Sync running status to DB
    await _sync_run_to_db(job_id, "running")
    
    try:
        # Update job progress
        await _update_progress(job_id, step="initializing", progress=5)
        
        # Try to import infra modules - fall back to mock if not available
        try:
            from infra.deployer import Deployer
            use_mock = False
        except ImportError:
            logger.warning("infra module not available, using mock deployment")
            use_mock = True
        
        if use_mock:
            # Mock deployment for testing
            import asyncio
            await _update_progress(job_id, step="mock_building", progress=30)
            await asyncio.sleep(2)  # Simulate work
            await _update_progress(job_id, step="mock_deploying", progress=60)
            await asyncio.sleep(2)
            await _update_progress(job_id, step="mock_verifying", progress=90)
            await asyncio.sleep(1)
            
            result = {
                "mock": True,
                "services_deployed": services or ["all"],
                "env": env,
                "message": "Mock deployment completed successfully"
            }
        else:
            # Real deployment
            from backend.app_kernel.db import get_db_connection
            from ..stores import CredentialsStore
            
            async with get_db_connection() as conn:
                creds_store = CredentialsStore(conn)
                credentials = await creds_store.get(workspace_id, project_name, env)
            
            if not credentials:
                raise ValueError(f"No credentials found for {workspace_id}/{project_name}/{env}")
            
            await _update_progress(job_id, step="loading_config", progress=10)
            
            deployer = Deployer(user=workspace_id, project_name=project_name)
            
            await _update_progress(job_id, step="building_images", progress=20)
            
            if services:
                result = deployer.deploy_services(
                    env=env,
                    services=services,
                    force_rebuild=force,
                    credentials=credentials,
                )
            else:
                result = deployer.deploy(
                    env=env,
                    force_rebuild=force,
                    credentials=credentials,
                )
        
        await _update_progress(job_id, step="completed", progress=100)
        
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
        
        # Mark as completed
        await _sync_run_to_db(job_id, "completed", result=result)
        
        return {
            "status": "success",
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "result": result,
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Deployment failed",
            extra={
                "job_id": job_id,
                "workspace_id": workspace_id,
                "project_name": project_name,
                "env": env,
                "error": error_msg,
                "traceback": traceback.format_exc(),
            }
        )
        
        # Mark as failed with error
        await _sync_run_to_db(job_id, "failed", error=error_msg)
        
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
    logger = _get_logger()
    
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
    
    # Mark as running
    await _sync_run_to_db(job_id, "running")
    
    try:
        await _update_progress(job_id, step="initializing", progress=10)
        
        # Try to import infra modules - fall back to mock if not available
        try:
            from infra.rollback_manager import RollbackManager
            use_mock = False
        except ImportError:
            logger.warning("infra module not available, using mock rollback")
            use_mock = True
        
        if use_mock:
            # Mock rollback for testing
            import asyncio
            await _update_progress(job_id, step="mock_rolling_back", progress=40)
            await asyncio.sleep(2)
            await _update_progress(job_id, step="mock_verifying", progress=80)
            await asyncio.sleep(1)
            
            result = {
                "mock": True,
                "env": env,
                "message": "Mock rollback completed successfully"
            }
        else:
            # Real rollback
            from backend.app_kernel.db import get_db_connection
            from ..stores import CredentialsStore
            
            async with get_db_connection() as conn:
                creds_store = CredentialsStore(conn)
                credentials = await creds_store.get(workspace_id, project_name, env)
            
            if not credentials:
                raise ValueError(f"No credentials found for {workspace_id}/{project_name}/{env}")
            
            await _update_progress(job_id, step="rolling_back", progress=30)
            
            rollback_mgr = RollbackManager(
                user=workspace_id,
                project_name=project_name,
            )
            
            result = rollback_mgr.rollback(
                env=env,
                credentials=credentials,
            )
        
        await _update_progress(job_id, step="completed", progress=100)
        
        logger.info(
            f"Rollback completed",
            extra={
                "job_id": job_id,
                "workspace_id": workspace_id,
                "project_name": project_name,
                "env": env,
            }
        )
        
        # Mark as completed
        await _sync_run_to_db(job_id, "completed", result=result)
        
        return {
            "status": "success",
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "result": result,
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Rollback failed",
            extra={
                "job_id": job_id,
                "error": error_msg,
                "traceback": traceback.format_exc(),
            }
        )
        
        # Mark as failed with error
        await _sync_run_to_db(job_id, "failed", error=error_msg)
        
        raise


# Task registry for app_kernel jobs
TASKS = {
    "deploy": run_deployment,
    "rollback": run_rollback,
}
