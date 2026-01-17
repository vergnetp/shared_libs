"""
Rollback utilities - Build deployment configs for rollback operations.

This module provides helpers to construct the right MultiDeployConfig
for rollback scenarios. The actual deploy is handled by DeploymentService.

Usage:
    from infra.deploy.rollback import RollbackHelper
    
    # Build config from deployment metadata
    config = RollbackHelper.build_config(
        service_name="api",
        project="myproject",
        environment="prod",
        workspace_id="user123",
        image_name="myproject_api:deploy_abc123",
        server_ips=["1.2.3.4"],
        port=8000,
        env_vars={"FOO": "bar"},
    )
    
    # Deploy using the service
    service = DeploymentService(do_token, agent_key)
    result = await service.deploy(config)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .service import MultiDeployConfig, DeploySource


@dataclass
class RollbackMetadata:
    """
    Metadata from a previous deployment needed for rollback.
    
    This is typically fetched from your deployment history database.
    """
    # Required
    service_name: str
    project: str
    environment: str
    workspace_id: str
    image_name: str  # Tagged image (e.g., "myapp:deploy_abc123" or "myapp:v5")
    server_ips: List[str]
    
    # Optional
    port: int = 8000
    env_vars: Dict[str, str] = field(default_factory=dict)
    deployment_id: Optional[str] = None  # For tagging the rollback deployment
    
    # Config snapshot (optional - for restoring exact settings)
    config_snapshot: Optional[Dict[str, Any]] = None


class RollbackHelper:
    """
    Helper class for building rollback deployment configs.
    
    The actual deployment is performed by DeploymentService.
    This class just helps construct the right configuration.
    """
    
    @staticmethod
    def build_config(
        service_name: str,
        project: str,
        environment: str,
        workspace_id: str,
        image_name: str,
        server_ips: List[str],
        port: int = 8000,
        env_vars: Optional[Dict[str, str]] = None,
        deployment_id: Optional[str] = None,
        container_port: Optional[int] = None,
        host_port: Optional[int] = None,
    ) -> MultiDeployConfig:
        """
        Build a MultiDeployConfig for rollback.
        
        Args:
            service_name: Name of the service
            project: Project name
            environment: Environment (prod, staging, etc.)
            workspace_id: User/workspace ID
            image_name: The tagged image to rollback to
            server_ips: Servers to rollback on
            port: Service port (default 8000)
            env_vars: Environment variables
            deployment_id: Optional ID for this rollback deployment
            container_port: Override container port
            host_port: Override host port
            
        Returns:
            MultiDeployConfig configured for rollback
        """
        return MultiDeployConfig(
            name=service_name,
            port=port,
            container_port=container_port,
            host_port=host_port,
            env_vars=env_vars or {},
            environment=environment,
            project=project,
            workspace_id=workspace_id,
            deployment_id=deployment_id,
            
            # Rollback uses IMAGE source with local image
            source_type=DeploySource.IMAGE,
            image=image_name,
            skip_pull=True,  # Image is local (tagged during original deployment)
            
            # Target the specified servers
            server_ips=server_ips,
            
            # Service mesh settings
            setup_sidecar=True,
            
            # Don't re-provision domain (already set up)
            setup_domain=False,
        )
    
    @staticmethod
    def build_config_from_metadata(
        metadata: RollbackMetadata,
        deployment_id: Optional[str] = None,
    ) -> MultiDeployConfig:
        """
        Build a MultiDeployConfig from RollbackMetadata.
        
        Args:
            metadata: Deployment metadata from history
            deployment_id: Optional ID for this rollback deployment
            
        Returns:
            MultiDeployConfig configured for rollback
        """
        # Extract ports from config_snapshot if available
        container_port = None
        host_port = None
        if metadata.config_snapshot:
            container_port = metadata.config_snapshot.get("container_port")
            host_port = metadata.config_snapshot.get("host_port")
        
        return RollbackHelper.build_config(
            service_name=metadata.service_name,
            project=metadata.project,
            environment=metadata.environment,
            workspace_id=metadata.workspace_id,
            image_name=metadata.image_name,
            server_ips=metadata.server_ips,
            port=metadata.port,
            env_vars=metadata.env_vars,
            deployment_id=deployment_id or metadata.deployment_id,
            container_port=container_port,
            host_port=host_port,
        )
    
    @staticmethod
    def get_tagged_image_name(
        image_name: str,
        deployment_id: str,
    ) -> str:
        """
        Get the tagged image name for a deployment.
        
        Images are tagged as: {base}:deploy_{deployment_id}
        
        Args:
            image_name: Original image name (may have existing tag)
            deployment_id: Deployment ID
            
        Returns:
            Tagged image name for rollback
        """
        # Strip existing tag if present
        base = image_name.split(":")[0] if image_name else ""
        return f"{base}:deploy_{deployment_id}"
