#!/usr/bin/env python3
"""
Clean deployment status check - Uses ServerInventory as source of truth
"""

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory


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

def check_deployment_status(project="new_project", env="uat", zone=None):
    """
    Check deployment status using ServerInventory as source of truth.
    
    Args:
        project: Project name
        env: Environment name
        zone: Optional zone filter (e.g., 'lon1')
    """
    print("=" * 60)
    print(f"DEPLOYMENT STATUS: {project}/{env}")
    print("=" * 60)
    
    # Get servers from ServerInventory (source of truth)
    servers = ServerInventory.get_servers(
        deployment_status=ServerInventory.STATUS_ACTIVE,
        zone=zone
    )
    
    if not servers:
        print(f"\n⚠ No green servers found in zone {zone or 'any'}")
        print("\nTrying all statuses...")
        servers = ServerInventory.list_all_servers()
        if zone:
            servers = [s for s in servers if s['zone'] == zone]
    
    if not servers:
        print("✗ No servers found!")
        return
    
    # Group servers by zone and status
    by_zone = {}
    for server in servers:
        z = server['zone']
        if z not in by_zone:
            by_zone[z] = []
        by_zone[z].append(server)
    
    print(f"\nFound {len(servers)} servers:")
    for z, zone_servers in sorted(by_zone.items()):
        print(f"  {z}: {len(zone_servers)} servers")
        for s in zone_servers:
            print(f"    • {s['ip']:<16} ({s['deployment_status']}) {s['cpu']}CPU/{s['memory']}MB")
    
    # Get just IPs for commands
    all_server_ips = [s['ip'] for s in servers]
    
    # 1. Container Status
    print("\n" + "=" * 60)
    print("CONTAINERS")
    print("-" * 60)
    
    for server_ip in sorted(all_server_ips):
        containers = run_remote_cmd(
            f"docker ps --format '{{{{.Names}}}}' | grep '{project}_{env}' | sort",
            server_ip
        )
        print(f"\n{server_ip}:")
        if not containers or not containers.strip():
            print(f"  ⚠ No {project}_{env} containers running")
            continue
            
        for container in containers.split('\n'):
            if container.strip():
                # Get container status
                status = run_remote_cmd(
                    f"docker inspect {container} --format '{{{{.State.Status}}}}'",
                    server_ip, quiet=True
                )
                print(f"  • {container:<40} {status}")
    
    # 2. Service Health
    print("\n" + "=" * 60)
    print("SERVICE HEALTH")
    print("-" * 60)
    
    # Find which server has each service by checking running containers
    service_locations = {}
    for server_ip in all_server_ips:
        containers = run_remote_cmd(
            f"docker ps --format '{{{{.Names}}}}' | grep '{project}_{env}'",
            server_ip, quiet=True
        )
        for container in containers.split('\n'):
            if not container.strip():
                continue
            # Extract service name from container name
            # Format: {project}_{env}_{service} or {project}_{env}_{service}_secondary
            parts = container.strip().split('_')
            if len(parts) >= 3:
                service = parts[2]  # Third part is the service name
                if service not in service_locations:
                    service_locations[service] = []
                service_locations[service].append(server_ip)
    
    # Check Redis
    if 'redis' in service_locations:
        redis_server = service_locations['redis'][0]
        result = run_remote_cmd(
            f"docker exec {project}_{env}_redis redis-cli ping", 
            redis_server, quiet=True
        )
        status = "✓ Healthy" if 'PONG' in result else "✗ Not responding"
        print(f"\nRedis ({redis_server}): {status}")
    else:
        print(f"\nRedis: ⚠ Container not found")
    
    # Check Postgres
    if 'postgres' in service_locations:
        postgres_server = service_locations['postgres'][0]
        result = run_remote_cmd(
            f"docker exec {project}_{env}_postgres pg_isready -U {project}_user",
            postgres_server, quiet=True
        )
        if 'accepting connections' in result:
            status = "✓ Healthy"
        elif 'restarting' in result.lower():
            status = "✗ Restarting (check logs)"
        else:
            status = f"✗ Not ready: {result}"
        print(f"Postgres ({postgres_server}): {status}")
    else:
        print(f"Postgres: ⚠ Container not found")
    
    # Check Job Status
    if 'job' in service_locations:
        print(f"\nJob Status:")
        for server in service_locations['job']:
            result = run_remote_cmd(
                f"docker ps -a --filter 'name={project}_{env}_job' --format '{{{{.Status}}}}' | head -1",
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
    
    for server_ip in sorted(all_server_ips):
        print(f"\n{server_ip}:")
        
        # Count cron jobs
        health_count = run_remote_cmd(
            "crontab -l 2>/dev/null | grep -c 'health_monitor' || echo 0",
            server_ip, quiet=True
        )
        worker_count = run_remote_cmd(
            f"crontab -l 2>/dev/null | grep -c '{project}_{env}_worker' || echo 0",
            server_ip, quiet=True
        )
        
        print(f"  • Health Monitor: {'✓ Installed' if health_count != '0' else '✗ Not found'}")
        print(f"  • Worker: {'✓ Installed' if worker_count != '0' else '✗ Not found'}")
        
        # Check if worker has run
        if worker_count != '0':
            log_check = run_remote_cmd(
                f"ls -la /var/log/cron_{project}_{env}_worker.log 2>&1",
                server_ip, quiet=True
            )
            if 'No such file' not in log_check and log_check:
                print(f"    Worker log: exists")
    
    # 4. Issues to check
    print("\n" + "=" * 60)
    print("POTENTIAL ISSUES")
    print("-" * 60)
    
    issues_found = False
    
    # Check Postgres logs if it's restarting
    if 'postgres' in service_locations:
        postgres_server = service_locations['postgres'][0]
        pg_status = run_remote_cmd(
            f"docker ps --filter 'name={project}_{env}_postgres' --format '{{{{.Status}}}}'",
            postgres_server, quiet=True
        )
        if 'Restarting' in pg_status:
            issues_found = True
            print(f"\n⚠ Postgres is restarting. Check logs:")
            print(f"  ssh root@{postgres_server} 'docker logs {project}_{env}_postgres --tail 20'")
    
    # Check for containers in unhealthy state
    for server_ip in all_server_ips:
        unhealthy = run_remote_cmd(
            f"docker ps --filter 'health=unhealthy' --filter 'name={project}_{env}' --format '{{{{.Names}}}}'",
            server_ip, quiet=True
        )
        if unhealthy and unhealthy.strip():
            issues_found = True
            print(f"\n⚠ Unhealthy containers on {server_ip}:")
            for container in unhealthy.split('\n'):
                if container.strip():
                    print(f"  • {container}")
    
    if not issues_found:
        print("\n✓ No issues detected")
    
    print("\n" + "=" * 60)
    print("\nQuick Commands:")
    print(f"  View inventory:  python -c 'from server_inventory import ServerInventory; s=ServerInventory.get_inventory_summary(); print(s)'")
    if 'postgres' in service_locations:
        print(f"  Postgres logs:   ssh root@{service_locations['postgres'][0]} 'docker logs {project}_{env}_postgres --tail 50'")
    if 'redis' in service_locations:
        print(f"  Redis logs:      ssh root@{service_locations['redis'][0]} 'docker logs {project}_{env}_redis --tail 50'")
    print("=" * 60)

if __name__ == "__main__":
    import sys
    
    # Parse arguments
    project = "new_project"
    env = "uat"
    zone = None
    
    if len(sys.argv) > 1:
        project = sys.argv[1]
    if len(sys.argv) > 2:
        env = sys.argv[2]
    if len(sys.argv) > 3:
        zone = sys.argv[3]
    
    check_deployment_status(project, env, zone)