"""
Health check job that uses your existing ServiceLocator
Usage: python health_check_job.py <project> <env> <service1> [service2] ...
"""
import sys
import os
from pathlib import Path
import socket
from datetime import datetime

sys.path.insert(0, '/app')

from service_locator import ServiceLocator
from enums import Envs, ServiceTypes


def check_service_health(project_name: str, env: Envs, service_type: ServiceTypes, service_name: str):
    """Check if a service is healthy"""
    try:
        endpoint = ServiceLocator.get_endpoint(project_name, env, service_type, service_name, timeout=10)
        host, port = endpoint.split(':')
        port = int(port)
        
        # Test connection
        with socket.create_connection((host, port), timeout=5):
            return True, f"✅ {service_type.value}/{service_name}: {endpoint}"
            
    except Exception as e:
        return False, f"❌ {service_type.value}/{service_name}: {str(e)}"


def main():
    if len(sys.argv) < 4:
        print("Usage: python health_check_job.py <project> <env> <service1> [service2] ...")
        print("Services: maindb, cache, search, api, worker")
        sys.exit(1)
    
    project_name = sys.argv[1]
    env = Envs(sys.argv[2])
    service_names = sys.argv[3:]
    
    print(f"🏥 Starting health check for {project_name}/{env.value}")
    print(f"📋 Checking services: {', '.join(service_names)}")
    
    # Map service names to types
    service_map = {
        'maindb': ServiceTypes.POSTGRES,
        'cache': ServiceTypes.REDIS,
        'search': ServiceTypes.OPENSEARCH,
        'api': ServiceTypes.WEB,
        'worker': ServiceTypes.WORKER,
    }
    
    results = []
    healthy_count = 0
    
    try:
        for service_name in service_names:
            if service_name not in service_map:
                result = f"⚠️ Unknown service: {service_name}"
                results.append((False, result))
                continue
            
            service_type = service_map[service_name]
            
            # Skip worker services (no exposed ports)
            if service_type == ServiceTypes.WORKER:
                result = f"⏸️ WORKER/{service_name}: Skipped (no exposed port)"
                results.append((True, result))
                healthy_count += 1
                continue
            
            is_healthy, result = check_service_health(project_name, env, service_type, service_name)
            results.append((is_healthy, result))
            
            if is_healthy:
                healthy_count += 1
        
        # Print results
        print(f"\n📊 Health Check Results ({datetime.now()}):")
        print("-" * 60)
        
        for is_healthy, result in results:
            print(f"   {result}")
        
        print("-" * 60)
        print(f"🎯 Summary: {healthy_count}/{len(service_names)} services healthy")
        
        # Exit with error if any service is unhealthy
        if healthy_count < len(service_names):
            print("⚠️ Some services are unhealthy")
            sys.exit(1)
        else:
            print("✅ All services are healthy")
        
    except Exception as e:
        print(f"❌ Health check job error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()