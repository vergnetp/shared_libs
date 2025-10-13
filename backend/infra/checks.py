#!/usr/bin/env python3
"""
Clean deployment status check
"""

from execute_cmd import CommandExecuter
from deployment_state_manager import DeploymentStateManager

def run_remote_cmd(cmd, server_ip, quiet=False):
    """Run command and return clean output"""
    try:
        result = CommandExecuter.run_cmd(cmd, server_ip, 'root')
        output = str(result).strip()
        # Filter out Alpine package installation noise
        lines = []
        for line in output.split('\n'):
            if not any(x in line for x in ['fetch https://dl-cdn', 'Installing', 'Executing', 'OK: ', '/alpine/']):
                lines.append(line)
        return '\n'.join(lines).strip()
    except Exception as e:
        if not quiet:
            return f"Error: {e}"
        return ""

def check_deployment_status():
    print("=" * 60)
    print("DEPLOYMENT STATUS")
    print("=" * 60)
    
    # Get servers from deployment state
    deployments = DeploymentStateManager.get_all_services("new_project", "uat")
    all_servers = set()
    for service_info in deployments.values():
        if service_info and service_info.get('servers'):
            all_servers.update(service_info['servers'])
    
    print(f"\nServers: {list(all_servers)}")
    
    # 1. Container Status
    print("\n" + "=" * 60)
    print("CONTAINERS")
    print("-" * 60)
    
    for server_ip in sorted(all_servers):
        containers = run_remote_cmd(
            "docker ps --format '{{.Names}}' | grep -v '^$' | sort",
            server_ip
        )
        print(f"\n{server_ip}:")
        for container in containers.split('\n'):
            if container.strip():
                # Get container status
                status = run_remote_cmd(
                    f"docker inspect {container} --format '{{{{.State.Status}}}}'",
                    server_ip, quiet=True
                )
                print(f"  • {container:<30} {status}")
    
    # 2. Service Health
    print("\n" + "=" * 60)
    print("SERVICE HEALTH")
    print("-" * 60)
    
    # Check Redis
    redis_server = deployments.get('redis', {}).get('servers', [None])[0]
    if redis_server:
        result = run_remote_cmd("docker exec new_project_uat_redis redis-cli ping", redis_server, quiet=True)
        status = "✓ Healthy" if 'PONG' in result else "✗ Not responding"
        print(f"\nRedis ({redis_server}): {status}")
    
    # Check Postgres
    postgres_server = deployments.get('postgres', {}).get('servers', [None])[0]
    if postgres_server:
        result = run_remote_cmd(
            "docker exec new_project_uat_postgres pg_isready -U new_project_user",
            postgres_server, quiet=True
        )
        if 'accepting connections' in result:
            status = "✓ Healthy"
        elif 'restarting' in result.lower():
            status = "✗ Restarting (check logs)"
        else:
            status = "✗ Not ready"
        print(f"Postgres ({postgres_server}): {status}")
    
    # Check Job Status
    print(f"\nJob Status:")
    job_servers = deployments.get('job', {}).get('servers', [])
    for server in job_servers:
        result = run_remote_cmd(
            "docker ps -a --filter 'name=new_project_uat_job' --format '{{.Status}}' | head -1",
            server, quiet=True
        )
        if 'Exited (0)' in result:
            print(f"  {server}: ✓ Completed successfully")
        else:
            print(f"  {server}: {result or 'Not found'}")
    
    # 3. Scheduled Tasks
    print("\n" + "=" * 60)
    print("SCHEDULED TASKS (CRON)")
    print("-" * 60)
    
    for server_ip in sorted(all_servers):
        print(f"\n{server_ip}:")
        
        # Count cron jobs
        health_count = run_remote_cmd(
            "crontab -l 2>/dev/null | grep -c 'health_monitor' || echo 0",
            server_ip, quiet=True
        )
        worker_count = run_remote_cmd(
            "crontab -l 2>/dev/null | grep -c 'new_project_uat_worker' || echo 0",
            server_ip, quiet=True
        )
        
        print(f"  • Health Monitor: {'✓ Installed' if health_count != '0' else '✗ Not found'}")
        print(f"  • Worker: {'✓ Installed' if worker_count != '0' else '✗ Not found'}")
        
        # Check if worker has run
        if worker_count != '0':
            log_check = run_remote_cmd(
                "ls -la /var/log/cron_new_project_uat_worker.log 2>&1",
                server_ip, quiet=True
            )
            if 'No such file' not in log_check and log_check:
                print(f"    Worker log: exists")
    
    # 4. Issues to check
    print("\n" + "=" * 60)
    print("POTENTIAL ISSUES")
    print("-" * 60)
    
    # Check Postgres logs if it's restarting
    if postgres_server:
        pg_status = run_remote_cmd(
            "docker ps --filter 'name=new_project_uat_postgres' --format '{{.Status}}'",
            postgres_server, quiet=True
        )
        if 'Restarting' in pg_status:
            print(f"\n⚠ Postgres is restarting. Check logs:")
            print(f"  ssh root@{postgres_server} 'docker logs new_project_uat_postgres --tail 20'")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    check_deployment_status()