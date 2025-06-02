## Infrastructure Module Analysis

The module is well-structured with clear separation of concerns:

1. **Core Components**: `InfrastructureState`, `InfrastructureOrchestrator`
2. **Managers**: Various specialized managers for different aspects
3. **Distributed Health**: Advanced health monitoring with peer consensus
4. **Environment Generation**: Dynamic configuration management
5. **Setup System**: Automated configuration initialization

Here's the README for the infrastructure module:

```markdown
# Infrastructure Module

A comprehensive Personal Cloud Orchestration System for managing multi-project infrastructure with automatic scaling, deployment, health monitoring, and recovery capabilities.

## Overview

The Infrastructure Module provides a complete solution for managing cloud infrastructure across multiple projects and environments. It handles everything from initial server provisioning to deployment automation, health monitoring, and disaster recovery.

### Key Features

- **CSV-Driven Infrastructure**: Define your infrastructure requirements in simple CSV files
- **Multi-Project Support**: Manage multiple projects with different environments (prod, uat, test)
- **Automatic Deployment**: Git-based deployment with version tagging and rollback capabilities
- **Distributed Health Monitoring**: Peer-to-peer health checks with consensus-based failure detection
- **Auto-Recovery**: Automatic snapshot-based recovery when services fail
- **Load Balancing**: Dynamic nginx configuration with upstream management
- **Secret Management**: Secure handling of environment variables and secrets
- **Platform Agnostic**: Support for Docker, Kubernetes, and Podman deployments

## Quick Start

### 1. Initial Setup

```bash
# Run the setup script to initialize all configurations
python infra/setup/setup.py

# This creates:
# - config/projects.csv
# - config/deployment_config.json
# - config/email_config.json
# - templates/ directory with deployment templates
```

### 2. Configure Your Projects

Edit `config/projects.csv`:
```csv
Project,Servers,MasterSpec,WebSpec
hostomatic,3,s-2vcpu-4gb,s-2vcpu-4gb
digitalpixo,1,s-1vcpu-1gb,s-1vcpu-1gb
```

### 3. Set Environment Variables

```bash
# Required
export DO_TOKEN="your_digitalocean_api_token"

# Optional - will auto-detect if not provided
# export ADMIN_IP="203.0.113.100/32"

# Additional authorized IPs (optional)
# export ADDITIONAL_IPS="203.0.113.200/32,203.0.113.201/32"

# Project secrets (example for hostomatic)
export HOSTOMATIC_PROD_DB_PASSWORD="secure_password"
export HOSTOMATIC_PROD_STRIPE_KEY="sk_live_..."

### 4. Initialize and Orchestrate Infrastructure

```bash
# Initialize the system
python orchestrator.py --init

# Create infrastructure from CSV
python orchestrator.py --orchestrate

# Check status
python orchestrator.py --status
```

## Core Components

### InfrastructureOrchestrator

The main coordinator that ties together all system components.

```python
from infra.orchestrator import InfrastructureOrchestrator

orchestrator = InfrastructureOrchestrator()

# Initialize system
result = orchestrator.initialize_system()

# Create infrastructure from CSV
result = orchestrator.orchestrate_infrastructure()

# Deploy to UAT
result = orchestrator.deploy_to_uat("hostomatic")

# Deploy to production
result = orchestrator.deploy_to_prod("hostomatic")
```

### InfrastructureState

Centralized state management for all infrastructure components.

```python
from infra.infrastructure_state import InfrastructureState

state = InfrastructureState()

# Add droplets
state.add_droplet("master", "192.168.1.10", "s-2vcpu-4gb", "lon1", "master")

# Add services
state.add_project_service("hostomatic-prod", "backend", 8001, ["web1", "web2"])

# Get load balancer targets
targets = state.get_load_balancer_targets("hostomatic-prod", "backend")
```

## Deployment Workflows

### UAT Deployment

```bash
# Deploy from Git (recommended for UAT)
python orchestrator.py --deploy-uat hostomatic

# Deploy from local codebase (for development)
python orchestrator.py --deploy-uat hostomatic --local --project-path ../hostomatic
```

### Production Deployment

```bash
# Deploy using latest UAT tag (recommended)
python orchestrator.py --deploy-prod hostomatic

# Deploy specific tag
python orchestrator.py --deploy-prod hostomatic --tag v1.2.3-uat-20241215-1430
```

### Version Management

The system automatically creates deployment tags:

- **UAT**: `v1.2.3-uat-20241215-1430`
- **Production**: Uses proven UAT tags

```bash
# Reproduce exact deployment state
python orchestrator.py --reproduce v1.2.3-uat-20241215-1430 --reproduce-dir ./reproduced-deployment
```

## Health Monitoring

### Distributed Monitoring

Each droplet monitors assigned peers with consensus-based failure detection:

```bash
# Start monitoring on master
python orchestrator.py --monitor master &

# Start monitoring on web droplets
python orchestrator.py --monitor web1 &
python orchestrator.py --monitor web2 &
```

### Automatic Recovery

When consensus is reached on a failure:

1. **Immediate**: Remove from load balancer
2. **Recovery**: Create new droplet from latest snapshot
3. **Restoration**: Add back to load balancer
4. **Notification**: Email alerts sent automatically

### Manual Recovery

```bash
# Emergency recovery of failed droplet
python orchestrator.py --recover web1
```

## Scaling Operations

### Horizontal Scaling

```bash
# Scale hostomatic to 5 servers
python orchestrator.py --scale hostomatic 5

# This updates the CSV and re-orchestrates infrastructure
```

### Load Balancer Management

The system automatically:
- Generates nginx upstream configurations
- Updates load balancer when services change
- Provides health check endpoints

## Secret Management

### Environment Variable Patterns

The system supports multiple naming patterns:

```bash
# Project-specific (highest priority)
HOSTOMATIC_PROD_DB_PASSWORD="..."

# Environment-specific
PROD_DB_PASSWORD="..."

# Global fallback
DB_PASSWORD="..."
```

### Docker Secrets Integration

Secrets are automatically converted to Docker secrets for secure runtime access.

## Configuration Files

### projects.csv
Defines infrastructure requirements:
```csv
Project,Servers,MasterSpec,WebSpec
hostomatic,3,s-2vcpu-4gb,s-2vcpu-4gb
digitalpixo,1,s-1vcpu-1gb,s-1vcpu-1gb
```

### deployment_config.json
Defines deployment configuration:
```json
{
  "deployment_platform": "docker",
  "git_config": {
    "base_url": "https://github.com/yourorg",
    "url_pattern": "{base_url}/{project}.git"
  },
  "projects": {
    "hostomatic": {
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "secrets": ["db_password", "stripe_key"]
        }
      }
    }
  }
}
```

## Advanced Features

### Hash-Based Resource Allocation

Services get deterministic ports and resource names:

```python
# Generate consistent port for hostomatic-prod-backend
port = state.get_hash_based_port("hostomatic", "prod", 8000, 1000)
# Always returns the same port for the same project/environment
```

### Infrastructure Validation

```bash
# Validate current state
python orchestrator.py --status

# The system checks for:
# - Missing droplets
# - Port conflicts
# - Service assignment issues
```

### Cleanup Operations

```bash
# Clean up old snapshots and resources (dry run)
python orchestrator.py --cleanup --dry-run

# Actually perform cleanup
python orchestrator.py --cleanup
```

## Troubleshooting

### Common Commands

```bash
# Get comprehensive status
python orchestrator.py --status

# Update administrator IP
python orchestrator.py --update-ip 203.0.113.200

# Test service connectivity
curl http://master-ip/hostomatic/prod/backend/health
```

### Log Locations

- **System logs**: Check orchestrator output
- **Nginx logs**: `/var/log/nginx/` on master droplet
- **Service logs**: `docker logs <service-name>` on assigned droplets

## Integration Points

### Email Notifications

Configure in `config/email_config.json`:
- Heartbeat emails (every 15 minutes)
- Recovery notifications
- Deployment confirmations

### External Services

- **DigitalOcean**: Droplet and snapshot management
- **Git repositories**: Source code deployment
- **Docker Registry**: Container image storage

## Security

### Access Control

- SSH key-based authentication
- IP-based firewall rules
- Secret isolation via Docker secrets

### Network Security

- Private networks between droplets
- Public access only through load balancer
- Rate limiting and DDoS protection

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `InfrastructureOrchestrator`

Main orchestrator that coordinates all infrastructure operations.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config_dir: str = "config"` | | Initialization | Initialize the orchestrator with configuration directory. |
| | `initialize_system` | | `Dict[str, Any]` | Setup | Initialize the entire orchestration system including SSH keys and configurations. |
| | `orchestrate_infrastructure` | `force_recreate: bool = False` | `Dict[str, Any]` | Infrastructure | Main orchestration function - create infrastructure from CSV configuration. |
| | `deploy_to_uat` | `project: str`, `branch: str = "main"` | `Dict[str, Any]` | Deployment | Deploy project to UAT environment from specified Git branch. |
| | `deploy_to_prod` | `project: str`, `use_uat_tag: bool = True` | `Dict[str, Any]` | Deployment | Deploy project to production environment using UAT tags. |
| | `scale_project` | `project: str`, `target_servers: int` | `Dict[str, Any]` | Scaling | Scale a project to target number of servers by updating CSV and re-orchestrating. |
| | `start_health_monitoring` | `droplet_name: str` | `Dict[str, Any]` | Monitoring | Start health monitoring daemon on a specific droplet. |
| | `get_infrastructure_status` | | `Dict[str, Any]` | Status | Get comprehensive infrastructure status including all components. |
| | `emergency_recovery` | `failed_droplet: str` | `Dict[str, Any]` | Recovery | Perform emergency recovery of a failed droplet using snapshots. |
| | `cleanup_infrastructure` | `dry_run: bool = True` | `Dict[str, Any]` | Maintenance | Clean up old snapshots and unused resources. |
| | `update_administrator_ip` | `new_ip: str` | `Dict[str, Any]` | Security | Update administrator IP across all infrastructure and firewall rules. |
| | `get_service_discovery_info` | `project: str`, `environment: str` | `Dict[str, Any]` | Discovery | Get service discovery information for debugging and connectivity testing. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_setup_ssh_keys` | | `Dict[str, Any]` | Setup | Setup SSH keys for infrastructure access and upload to DigitalOcean. |
| | `_load_deployment_config` | | `Dict[str, Any]` | Setup | Load deployment configuration and initialize deployment manager. |
| | `_validate_projects_csv` | | `Dict[str, Any]` | Validation | Validate projects CSV file structure and content. |
| | `_load_projects_csv` | | `List[Dict[str, Any]]` | Configuration | Load and parse projects CSV file. |
| | `_plan_infrastructure_changes` | `projects: List[Dict[str, Any]]`, `force_recreate: bool = False` | `Dict[str, Any]` | Planning | Plan what infrastructure changes are needed based on CSV requirements. |
| | `_execute_infrastructure_plan` | `plan: Dict[str, Any]` | `Dict[str, Any]` | Execution | Execute the planned infrastructure changes. |
| | `_create_droplet` | `name: str`, `config: Dict[str, Any]` | `Dict[str, Any]` | Infrastructure | Create a new droplet with specified configuration. |
| | `_resize_droplet` | `name: str`, `new_size: str` | `Dict[str, Any]` | Infrastructure | Resize an existing droplet (updates state, actual resize requires manual intervention). |
| | `_configure_service` | `project: str`, `service_type: str`, `config: Dict[str, Any]` | `Dict[str, Any]` | Infrastructure | Configure a service in the infrastructure state. |
| | `_destroy_droplet` | `name: str` | `Dict[str, Any]` | Infrastructure | Destroy a droplet and remove from state. |
| | `_setup_monitoring_relationships` | `droplet_name: str`, `role: str` | | Monitoring | Setup peer monitoring relationships for new droplet based on role. |
| | `_update_project_csv` | `project: str`, `new_server_count: int` | | Configuration | Update project server count in CSV file. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `InfrastructureState`

Manages the normalized infrastructure state with computed relationships.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `state_file: str = "config/infrastructure.json"` | | Initialization | Initialize infrastructure state with JSON file path. |
| | `save_state` | | | Persistence | Save current state to JSON file. |
| | `add_droplet` | `name: str`, `ip: str`, `size: str`, `region: str`, `role: str`, `monitors: List[str] = None` | | Droplet Management | Add a new droplet to the state. |
| | `update_droplet_ip` | `name: str`, `new_ip: str` | | Droplet Management | Update droplet IP address. |
| | `remove_droplet` | `name: str` | | Droplet Management | Remove droplet from state. |
| | `get_droplet` | `name: str` | `Optional[Dict[str, Any]]` | Droplet Management | Get droplet configuration by name. |
| | `get_all_droplets` | | `Dict[str, Dict[str, Any]]` | Droplet Management | Get all droplets. |
| | `get_droplets_by_role` | `role: str` | `Dict[str, Dict[str, Any]]` | Droplet Management | Get droplets filtered by role. |
| | `add_project_service` | `project: str`, `service_type: str`, `port: int = None`, `assigned_droplets: List[str] = None`, `service_config: Dict[str, Any] = None` | | Project Management | Add a service to a project. |
| | `remove_project_service` | `project: str`, `service_type: str` | | Project Management | Remove a service from a project. |
| | `get_project_services` | `project: str` | `Dict[str, Dict[str, Any]]` | Project Management | Get all services for a project. |
| | `get_all_projects` | | `Dict[str, Dict[str, Any]]` | Project Management | Get all projects. |
| | `get_service_name` | `project: str`, `service_type: str` | `str` | Computed Relationships | Generate service name from project and service type. |
| | `get_services_on_droplet` | `droplet_name: str` | `List[str]` | Computed Relationships | Get all services running on a specific droplet. |
| | `get_load_balancer_targets` | `project: str`, `service_type: str` | `List[str]` | Computed Relationships | Get load balancer targets for a service (web services only). |
| | `get_monitored_by` | `droplet_name: str` | `List[str]` | Computed Relationships | Get list of droplets that monitor the given droplet. |
| | `generate_resource_hash` | `project: str`, `environment: str` | `str` | Utility | Generate deterministic hash for resource naming. |
| | `get_hash_based_port` | `project: str`, `environment: str`, `base_port: int`, `port_range: int = 1000` | `int` | Utility | Generate hash-based port allocation. |
| | `update_heartbeat_config` | `primary_sender: str = None`, `backup_senders: List[str] = None`, `interval_minutes: int = None` | | Health Monitoring | Update heartbeat monitoring configuration. |
| | `get_heartbeat_config` | | `Dict[str, Any]` | Health Monitoring | Get heartbeat monitoring configuration. |
| | `get_master_droplet` | | `Optional[Dict[str, Any]]` | Utility | Get the master droplet. |
| | `get_web_droplets` | | `Dict[str, Dict[str, Any]]` | Utility | Get all web droplets. |
| | `validate_state` | | `List[str]` | Validation | Validate the current state and return any issues. |
| | `get_summary` | | `Dict[str, Any]` | Status | Get infrastructure summary with counts and basic info. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_load_state` | | `Dict[str, Any]` | Persistence | Load state from JSON file or create empty state. |
| | `_create_empty_state` | | `Dict[str, Any]` | Persistence | Create empty state structure. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DistributedHealthMonitor`

Distributed health monitoring daemon that runs on each droplet.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `droplet_name: str`, `infrastructure_state: InfrastructureState`, `snapshot_manager: SnapshotManager`, `load_balancer_manager: LoadBalancerManager`, `emailer=None` | | Initialization | Initialize distributed health monitor for a specific droplet. |
| | `start_monitoring` | | | Monitoring | Start the distributed health monitoring daemon with all monitoring tasks. |
| | `get_monitoring_status` | | `Dict[str, Any]` | Status | Get current monitoring status including health results and active operations. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_health_check_loop` | | | Monitoring | Main health checking loop that runs continuously. |
| | `_perform_health_checks` | | | Monitoring | Perform health checks on all assigned targets. |
| | `_check_target_health` | `target_droplet: str` | `HealthCheckResult` | Monitoring | Check health of a specific target droplet. |
| | `_process_health_result` | `result: HealthCheckResult` | | Monitoring | Process a health check result and trigger consensus if needed. |
| | `_report_failure_to_peers` | `failed_target: str`, `error: str` | | Consensus | Report failure to peer droplets for consensus building. |
| | `_consensus_check_loop` | | | Consensus | Check for failure consensus and trigger recovery actions. |
| | `_check_failure_consensus` | | | Consensus | Check if consensus has been reached for any failures. |
| | `_handle_consensus_failure` | `failed_target: str`, `consensus: FailureConsensus` | | Recovery | Handle a target that has reached failure consensus. |
| | `_coordinate_recovery` | `failed_target: str` | | Recovery | Coordinate recovery of a failed target as the elected leader. |
| | `_heartbeat_loop` | | | Notifications | Send heartbeat emails at regular intervals. |
| | `_send_heartbeat_if_due` | | | Notifications | Send heartbeat email if interval has passed. |
| | `_send_heartbeat_email` | `email_type: str` | | Notifications | Send heartbeat email notification with infrastructure status. |
| | `_cleanup_loop` | | | Maintenance | Periodic cleanup of old monitoring data. |

</details>

<br>

</div>
```

