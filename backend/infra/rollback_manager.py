from typing import Optional, List

try:
    from .deployment_state_manager import DeploymentStateManager
except ImportError:
    from deployment_state_manager import DeploymentStateManager
try:
    from .deployment_naming import DeploymentNaming
except ImportError:
    from deployment_naming import DeploymentNaming
try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .deployer import Deployer
except ImportError:
    from deployer import Deployer
try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer


def log(msg):
    Logger.log(msg)


class RollbackManager:
    """
    Minimal rollback manager - reuses deploy() infrastructure.
    
    Strategy: Rollback = deploy with older version tag
    """
    
    @staticmethod
    def check_image_exists_in_registry(
        docker_hub_user: str,
        repository: str,
        tag: str
    ) -> bool:
        """
        Check if image tag exists in Docker Hub registry.
        
        Args:
            docker_hub_user: Docker Hub username
            repository: Repository name (e.g., "myproject-prod-api")
            tag: Version tag to check
            
        Returns:
            True if image exists in registry
        """
        try:
            url = f"https://hub.docker.com/v2/repositories/{docker_hub_user}/{repository}/tags/{tag}/"
            cmd = f'curl -sS -f -o /dev/null -w "%{{http_code}}" "{url}"'
            
            result = CommandExecuter.run_cmd(cmd, 'localhost')
            
            # Extract HTTP status code
            if hasattr(result, 'stdout'):
                status_code = result.stdout.strip()
            else:
                status_code = str(result).strip()
            
            # 200 = exists, 404 = not found
            return status_code == "200"
            
        except Exception as e:
            log(f"Warning: Could not verify image in registry: {e}")
            return False  # Assume doesn't exist if can't verify
    
    @staticmethod
    def list_available_versions(
        project: str,
        env: str,
        service: str
    ) -> List[str]:
        """
        List available versions from deployment history.
        
        Returns:
            List of version strings (newest first)
        """
        return DeploymentStateManager.get_version_history(project, env, service)
    
    @staticmethod
    def get_previous_version(
        project: str,
        env: str,
        service: str
    ) -> Optional[str]:
        """
        Get previous deployment version.
        
        Returns:
            Previous version string or None
        """
        return DeploymentStateManager.get_previous_version(project, env, service)
    
    @staticmethod
    def rollback(
        project: str,
        env: str,
        service: str,
        target_version: Optional[str] = None,
        validate_registry: bool = True
    ) -> bool:
        """
        Rollback service to previous version by redeploying with older image.
        
        Process:
        1. Determine target version (previous if not specified)
        2. Validate image exists in registry (optional)
        3. Call deploy() with target version
        4. Existing immutable infrastructure handles the rest
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            target_version: Version to rollback to (None = previous)
            validate_registry: Check if image exists before attempting
            
        Returns:
            True if rollback successful
        """
        log(f"Starting rollback for {project}/{env}/{service}")
        Logger.start()
        
        try:
            # Get current version
            current = DeploymentStateManager.get_current_deployment(project, env, service)
            
            if not current:
                log(f"No current deployment found for {service}")
                Logger.end()
                return False
            
            current_version = current.get("version", "unknown")
            log(f"Current version: {current_version}")
            
            # Determine target version
            if not target_version:
                target_version = RollbackManager.get_previous_version(project, env, service)
                
                if not target_version:
                    log("No previous version available for rollback")
                    log("Deployment history:")
                    history = DeploymentStateManager.get_deployment_history(project, env, service)
                    for i, record in enumerate(history[:5]):
                        log(f"  {i+1}. v{record.get('version')} - {record.get('deployed_at')}")
                    Logger.end()
                    return False
            
            log(f"Target version: {target_version}")
            
            # Check if already on target version
            if current_version == target_version:
                log(f"Already running version {target_version}")
                Logger.end()
                return True
            
            # Optional: Validate image exists in registry
            if validate_registry:
                log("Validating image exists in registry...")
                
                configurer = DeploymentConfigurer(project)
                docker_hub_user = configurer.get_docker_hub_user()
                service_config = configurer.get_services(env).get(service)
                
                if not service_config:
                    log(f"Service {service} not found in config")
                    Logger.end()
                    return False
                
                # Build repository name
                repository = DeploymentNaming.get_image_name(
                    docker_hub_user,
                    project,
                    env,
                    service,
                    target_version
                ).split(':')[0].split('/')[-1]  # Extract repo name
                
                if not RollbackManager.check_image_exists_in_registry(
                    docker_hub_user, repository, target_version
                ):
                    log(f"Image not found in registry: {docker_hub_user}/{repository}:{target_version}")
                    log("Cannot rollback to non-existent image")
                    Logger.end()
                    return False
                
                log("Image found in registry")
            
            # Rollback = redeploy with target version
            log(f"Deploying version {target_version}...")
            
            deployer = Deployer(project, auto_sync=False)
            success = deployer.deploy(
                env=env,
                service_name=service,
                build=False,  # Don't build, use existing registry image
                target_version=target_version
            )
            
            if success:
                log(f"Rollback successful: {current_version} → {target_version}")
            else:
                log(f"Rollback failed")
            
            Logger.end()
            return success
            
        except Exception as e:
            log(f"Rollback failed with exception: {e}")
            Logger.end()
            return False


def main():
    """CLI for rollback operations"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Service Rollback Manager')
    parser.add_argument('--project', required=True, help='Project name')
    parser.add_argument('--env', required=True, help='Environment')
    parser.add_argument('--service', required=True, help='Service name')
    
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Rollback command
    rollback_cmd = subparsers.add_parser('rollback', help='Rollback to previous version')
    rollback_cmd.add_argument('--version', help='Target version (default: previous)')
    rollback_cmd.add_argument('--no-validate', action='store_true', 
                             help='Skip registry validation')
    
    # List versions
    list_cmd = subparsers.add_parser('list', help='List available versions from history')
    
    # History
    history_cmd = subparsers.add_parser('history', help='Show deployment history')
    
    args = parser.parse_args()
    
    if args.command == 'rollback':
        success = RollbackManager.rollback(
            args.project,
            args.env,
            args.service,
            args.version,
            validate_registry=not args.no_validate
        )
        exit(0 if success else 1)
    
    elif args.command == 'list':
        versions = RollbackManager.list_available_versions(
            args.project,
            args.env,
            args.service
        )
        
        if not versions:
            print(f"No deployment history found for {args.service}")
            exit(1)
        
        print(f"\nAvailable versions for {args.service}:")
        for i, version in enumerate(versions, 1):
            marker = "(current)" if i == 1 else "(previous)" if i == 2 else ""
            print(f"  {i}. {version} {marker}")
    
    elif args.command == 'history':
        history = DeploymentStateManager.get_deployment_history(
            args.project,
            args.env,
            args.service
        )
        
        if not history:
            print(f"No deployment history found for {args.service}")
            exit(1)
        
        print(f"\nDeployment history for {args.service}:")
        for i, deployment in enumerate(history, 1):
            print(f"\n{i}. Version: {deployment.get('version')}")
            print(f"   Deployed: {deployment.get('deployed_at')}")
            print(f"   Servers: {', '.join(deployment.get('servers', []))}")


if __name__ == "__main__":
    main()