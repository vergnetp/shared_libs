import sys, os
import asyncio
from typing import Dict

from .config import DeploymentConfig
from .deploy import deploy
from .environments import dev_config, uat_config, prod_config


async def main(configs: Dict[str, DeploymentConfig]={'dev': dev_config, 'uat': uat_config, 'prod': prod_config}):
 
    env = input(f"Which environment do you want to deploy? [dev/uat/prod]: ").strip().lower()
    config = configs.get(env, None)

    if not config:
        print(f"No config available for {env}, aborting")
        return 0

    print(f"Current Working Directory: {os.getcwd()}")
    print(f"Build Context: {config.build_context}")
    print(f"Container Files: {config.container_files}")
    print(f"The file directory: {os.path.abspath(os.path.dirname(__file__))}")

    # Get version from command line or prompt
    if len(sys.argv) > 1:
        version = sys.argv[1]
    else:
        version = input("Enter version to deploy (e.g., v1.2.3): ").strip()
        if not version:
            print("Version is required")
            return 1
    
    # Check for dry run 
    dry_run = True
    confirm = input(f"Do you really want to deploy (if not we just show you the commands as a dry run)? [y/N]: ").strip().lower()
    if confirm  in ['y', 'yes']:
        dry_run = False    
    
    if dry_run:
        print("   (DRY RUN - no changes will be made)")
    else:
        print(f"🚀 Deploying {version}...")    
    
    # Show what will be deployed
    print(f"   API servers: {len(config.api_servers)}")
    print(f"   Worker servers: {len(config.worker_servers)}")
    print(f"   Registry: {config.registry_url}")
    print(f"   Runtime: {config.container_runtime.value}")
    print(f"   SSL: {'Enabled' if config.ssl_enabled else 'Disabled'}")    
   
    # Execute deployment
    print("\n" + "="*50)
    result = await deploy(
        config=config,
        version=version,
        dry_run=dry_run
    )
    
    # Report results
    print("="*50)
    if result["success"]:
        print(f"✅ Production deployment successful!")
        print(f"   Services deployed: {len(result['deployed_services'])}")
        for service_name, service_info in result["deployed_services"].items():
            print(f"   - {service_name}: {service_info.get('image', 'N/A')}")
    else:
        print(f"❌ Production deployment failed!")
        if result["failed_services"]:
            print(f"   Failed services: {', '.join(result['failed_services'])}")
        if "error" in result:
            print(f"   Error: {result['error']}")
        return 1
    
    return 0

if __name__ == "__main__":
    
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n❌ Deployment cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)