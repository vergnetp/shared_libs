# Infrastructure Module

A comprehensive Personal Cloud Orchestration System for managing multi-project infrastructure with automatic scaling, deployment, health monitoring, and recovery capabilities.

## Overview

The Infrastructure Module provides a complete solution for managing cloud infrastructure across multiple projects and environments. It handles everything from initial server provisioning to deployment automation, health monitoring, and disaster recovery.

### Key Features

- **JSON-Driven Infrastructure**: Define your infrastructure requirements directly in JSON with full worker support
- **Multi-Project Support**: Manage multiple projects with different environments (prod, uat, test)
- **Worker Support**: Deploy and manage background workers alongside web services
- **Automatic Deployment**: Git-based deployment with version tagging and rollback capabilities
- **Distributed Health Monitoring**: Peer-to-peer health checks with consensus-based failure detection
- **Auto-Recovery**: Automatic snapshot-based recovery when services fail
- **Load Balancing**: Dynamic nginx configuration with upstream management
- **Secret Management**: Secure handling of environment variables and secrets
- **Platform Agnostic**: Support for Docker, Kubernetes, and Podman deployments

## 🎯 How It Works: JSON Specification → Infrastructure

```
┌─────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
│ infrastructure.json │    │  --orchestrate      │    │   Live Droplets     │
│ (Infrastructure     │───▶│  (Creates Servers)   │───▶│   (Runtime State)   │
│  Specification)     │    │                      │    │                     │
│                     │    │ • Creates droplets   │    │ • Real IP addresses │
│ "hostomatic": {     │    │ • Assigns services   │    │ • Calculated ports  │
│   "web_droplets": 2 │    │ • Configures network │    │ • Service mapping   │
│   "environments":   │    │ • Deploys workers    │    │ • Worker processes  │
│   ["prod", "uat"]   │    │                      │    │                     │
│ }                   │    │                      │    │                     │
└─────────────────────┘    └──────────────────────┘    └─────────────────────┘
```

### Data Flow Example

**Input (infrastructure.json):**
```json
{
  "infrastructure_spec": {
    "projects": {
      "hostomatic": {
        "environments": ["prod", "uat"],
        "web_droplets": 2,
        "web_droplet_spec": "s-2vcpu-4gb"
      }
    }
  }
}
```

**Processing:** `python orchestrator.py --orchestrate`
- Creates droplets: master, hostomatic-web1, hostomatic-web2
- Gets real IP addresses from DigitalOcean
- Calculates deterministic ports using hashing
- Assigns services including workers to appropriate servers

**Output (runtime state in same file):**
```json
{
  "droplets": {
    "master": {"ip": "192.168.1.10", "role": "master"},
    "hostomatic-web1": {"ip": "192.168.1.11", "role": "web", "project": "hostomatic"},
    "hostomatic-web2": {"ip": "192.168.1.12", "role": "web", "project": "hostomatic"}
  },
  "projects": {
    "hostomatic-prod": {
      "backend": {"port": 8001, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
      "frontend": {"port": 9001, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
      "worker_email": {"type": "worker", "assigned_droplets": ["hostomatic-web1"]},
      "scheduler": {"type": "worker", "assigned_droplets": ["hostomatic-web1"]}
    }
  }
}
```

## 🚀 Quick Start Guide

### Step 1: Install Dependencies

```bash
pip install digitalocean paramiko aiohttp jinja2 psutil pyyaml python-dotenv
```

### Step 2: Initialize Configuration Files

```bash
python infra/setup/setup.py
```

This creates:
- `config/infrastructure.json` - Define your projects and server requirements
- `config/deployment_config.json` - Git repositories and service definitions (including workers)
- `config/email_config.json` - Email notifications setup
- `config/sms_config.json` - SMS alerts configuration
- `templates/` - Deployment templates for Docker/Kubernetes with worker support

### Step 3: Set Required Environment Variables

```bash
# Required: DigitalOcean API Token
export DO_TOKEN="dop_v1_your_actual_token_here"

# Required: Your public IP for SSH access
export ADMIN_IP="203.0.113.100/32"

# Project secrets (including worker secrets)
export HOSTOMATIC_PROD_DB_PASSWORD="secure_database_password"
export HOSTOMATIC_PROD_SENDGRID_API_KEY="SG.your_sendgrid_key_for_workers"
```

### Step 4: Configure Your Infrastructure

Edit `config/infrastructure.json`:
```json
{
  "infrastructure_spec": {
    "droplets": {
      "master": {
        "size": "s-2vcpu-4gb",
        "region": "lon1",
        "role": "master"
      }
    },
    "projects": {
      "hostomatic": {
        "environments": ["prod", "uat"],
        "web_droplets": 2,
        "web_droplet_spec": "s-2vcpu-4gb"
      },
      "digitalpixo": {
        "environments": ["prod", "uat"],
        "web_droplets": 1,
        "web_droplet_spec": "s-1vcpu-1gb"
      }
    }
  }
}
```

### Step 5: Configure Services Including Workers

Edit `config/deployment_config.json`:
```json
{
  "projects": {
    "hostomatic": {
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "secrets": ["db_password", "stripe_key"]
        },
        "frontend": {
          "containerfile_path": "frontend/Dockerfile",
          "secrets": ["stripe_publishable_key"]
        },
        "worker_email": {
          "type": "worker",
          "containerfile_path": "workers/Dockerfile",
          "command": "python email_processor.py",
          "secrets": ["db_password", "sendgrid_api_key"]
        },
        "scheduler": {
          "type": "worker",
          "containerfile_path": "scheduler/Dockerfile", 
          "command": "python cron_scheduler.py",
          "secrets": ["db_password", "redis_password"]
        }
      }
    }
  }
}
```

### Step 6: Initialize and Create Infrastructure

```bash
# Initialize the system
python orchestrator.py --init

# Create infrastructure from JSON spec
python orchestrator.py --orchestrate
```

### Step 7: Deploy Your Project (Including Workers)

```bash
# Deploy to UAT (deploys web services AND workers)
python orchestrator.py --deploy-uat hostomatic

# Deploy to production (promotes UAT images including workers)
python orchestrator.py --deploy-prod hostomatic
```

## 📋 Complete Command Reference

### Infrastructure Management
```bash
# Initialize system
python orchestrator.py --init

# Create/update infrastructure from JSON
python orchestrator.py --orchestrate

# Force recreate all resources
python orchestrator.py --orchestrate --force

# Get infrastructure status
python orchestrator.py --status
```

### Project Management
```bash
# Add a new project
python orchestrator.py --add-project myapp "prod,uat" 2 s-2vcpu-4gb

# Scale existing project
python orchestrator.py --scale hostomatic 3

# Remove project
python orchestrator.py --remove-project oldproject
```

### Deployment (Web Services + Workers)
```bash
# Deploy to UAT from Git (includes all workers)
python orchestrator.py --deploy-uat hostomatic

# Deploy to UAT from local code (for development)
python orchestrator.py --deploy-uat hostomatic --local --project-path ../hostomatic

# Deploy to production (uses latest UAT tag, includes workers)
python orchestrator.py --deploy-prod hostomatic
```

### Health Monitoring
```bash
# Start monitoring on master
python orchestrator.py --monitor master &

# Start monitoring on web servers  
python orchestrator.py --monitor hostomatic-web1 &
python orchestrator.py --monitor hostomatic-web2 &
```

## 🔧 Configuration Files Reference

### infrastructure.json
Single source of truth for your infrastructure:
```json
{
  "droplets": {
    "master": {
      "ip": "192.168.1.10",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "master"
    }
  },
  "projects": {
    "hostomatic-prod": {
      "backend": {
        "type": "web",
        "port": 8001,
        "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]
      },
      "worker_email": {
        "type": "worker",
        "assigned_droplets": ["hostomatic-web1"]
      }
    }
  },
  "infrastructure_spec": {
    "droplets": {
      "master": {
        "size": "s-2vcpu-4gb",
        "region": "lon1",
        "role": "master"
      }
    },
    "projects": {
      "hostomatic": {
        "environments": ["prod", "uat"],
        "web_droplets": 2,
        "web_droplet_spec": "s-2vcpu-4gb"
      }
    }
  }
}
```

### deployment_config.json (with Worker Support)
```json
{
  "deployment_platform": "docker",
  "projects": {
    "hostomatic": {
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "secrets": ["db_password", "stripe_key"]
        },
        "worker_email": {
          "type": "worker",
          "containerfile_path": "workers/Dockerfile",
          "command": "python email_processor.py",
          "secrets": ["db_password", "sendgrid_api_key"]
        },
        "scheduler": {
          "type": "worker",
          "containerfile_path": "scheduler/Dockerfile",
          "command": "python cron_scheduler.py",
          "secrets": ["db_password", "redis_password"]
        }
      }
    }
  }
}
```

## 🏗️ Architecture with Workers

### Infrastructure Layout

```
┌─────────────────────────────────────────────────────────────┐
│                     Load Balancer                          │
│                    (nginx on master)                       │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Master  │   │  Web1   │   │  Web2   │
   │         │   │         │   │         │
   │ • Nginx │   │ • App   │   │ • App   │
   │ • DB    │   │ • Cache │   │ • Cache │
   │ • Redis │   │ • Workers│  │ • Workers│
   │ • Vault │   │         │   │         │
   └─────────┘   └─────────┘   └─────────┘
```

### Service Distribution Including Workers

- **Master**: Infrastructure services (DB, Redis, Vault, OpenSearch) + Load Balancer
- **Web Servers**: Application services (Backend, Frontend) + Background Workers
- **Workers**: Email processing, scheduled tasks, image processing, etc.

### Worker Types Supported

1. **Email Workers**: Process email queues
   ```bash
   command: "python email_processor.py"
   ```

2. **Scheduled Tasks**: Cron-like background jobs
   ```bash
   command: "python cron_scheduler.py"
   ```

3. **Image Processing**: Handle file uploads/processing
   ```bash
   command: "python image_processor.py"
   ```

4. **Data Processing**: ETL, analytics, reports
   ```bash
   command: "python data_processor.py"
   ```

## 🔄 Worker Deployment Flow

```
1. Git Repository (includes worker code)
   ↓
2. Clone & Build (builds worker images)
   ↓
3. Create Secrets (including worker secrets)
   ↓
4. Deploy Web Services to Droplets
   ↓
5. Deploy Workers to Same Droplets
   ↓
6. Update Load Balancer (web services only)
   ↓
7. Create Snapshots (includes worker state)
   ↓
8. Health Monitoring (includes worker processes)
```

## 🔍 Monitoring Workers

### Worker Health Checks

Workers are monitored differently from web services:

- **Process Health**: Check if worker process is running
- **Queue Health**: Monitor job queue depths
- **Resource Usage**: CPU/memory monitoring
- **Error Rates**: Track worker failures

### Worker Recovery

When a worker fails:
1. **Process Restart**: Automatic restart of worker containers
2. **Queue Reprocessing**: Re-queue failed jobs
3. **Droplet Recovery**: Full droplet recovery includes workers
4. **Load Balancing**: Workers don't affect load balancer (no external traffic)

## 🚨 Troubleshooting Workers

### Common Worker Issues

**1. Worker Not Starting**
```bash
# Check worker logs
ssh -i ~/.ssh/infrastructure_key root@droplet-ip
docker logs hostomatic-prod-worker_email
```

**2. Worker Process Died**
```bash
# Check worker health
docker ps --filter name=worker
docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

**3. Job Queue Backed Up**
```bash
# Check Redis queue length
redis-cli LLEN email_queue
redis-cli LLEN scheduler_queue
```

### Worker Commands

```bash
# Deploy only workers (after code changes)
python orchestrator.py --deploy-uat hostomatic

# Scale workers independently (modify infrastructure.json worker assignments)
python orchestrator.py --orchestrate

# Monitor worker health
python orchestrator.py --status | grep worker
```

## 📚 Advanced Usage

### Custom Worker Types

Add custom workers to deployment config:
```json
{
  "worker_custom": {
    "type": "worker",
    "containerfile_path": "custom_workers/Dockerfile",
    "command": "python my_custom_worker.py",
    "secrets": ["custom_api_key", "db_password"],
    "environment": {
      "WORKER_CONCURRENCY": "4",
      "CUSTOM_CONFIG": "value"
    }
  }
}
```

### Worker Scaling

Workers scale with web droplets automatically:
```bash
# Scale project (includes workers)
python orchestrator.py --scale hostomatic 4
```

### Multi-Environment Workers

Workers are deployed to each environment:
- `hostomatic-prod-worker_email` (production email worker)
- `hostomatic-uat-worker_email` (UAT email worker)

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `InfrastructureOrchestrator`

Main orchestrator that coordinates all infrastructure operations including worker management.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config_dir: str = "config"` | | Initialization | Initialize the orchestrator with configuration directory. |
| | `initialize_system` | | `Dict[str, Any]` | Setup | Initialize the entire orchestration system including SSH keys and configurations. |
| | `orchestrate_infrastructure` | `force_recreate: bool = False` | `Dict[str, Any]` | Infrastructure | Main orchestration function - create infrastructure from JSON specification. |
| | `add_project` | `project: str`, `environments: List[str]`, `web_droplets: int`, `web_droplet_spec: str` | `Dict[str, Any]` | Project Management | Add a new project to infrastructure with worker support. |
| | `scale_project` | `project: str`, `target_web_droplets: int` | `Dict[str, Any]` | Scaling | Scale a project to target number of web droplets (workers scale automatically). |
| | `remove_project` | `project: str` | `Dict[str, Any]` | Project Management | Remove a project from infrastructure including all workers. |
| | `deploy_to_uat` | `project: str`, `branch: str = "main"`, `use_local: bool = False`, `local_project_path: str = None` | `Dict[str, Any]` | Deployment | Deploy project to UAT environment including all workers from specified Git branch. |
| | `deploy_to_prod` | `project: str`, `use_uat_tag: bool = True`, `promote_images: bool = True` | `Dict[str, Any]` | Deployment | Deploy project to production environment including workers using UAT tags. |
| | `start_health_monitoring` | `droplet_name: str` | `Dict[str, Any]` | Monitoring | Start health monitoring daemon on a specific droplet (includes worker monitoring). |
| | `get_infrastructure_status` | | `Dict[str, Any]` | Status | Get comprehensive infrastructure status including all components and workers. |
| | `emergency_recovery` | `failed_droplet: str` | `Dict[str, Any]` | Recovery | Perform emergency recovery of a failed droplet including workers using snapshots. |
| | `cleanup_infrastructure` | `dry_run: bool = True` | `Dict[str, Any]` | Maintenance | Clean up old snapshots and unused resources. |
| | `update_administrator_ip` | `new_ip: str` | `Dict[str, Any]` | Security | Update administrator IP across all infrastructure and firewall rules. |
| | `get_service_discovery_info` | `project: str`, `environment: str` | `Dict[str, Any]` | Discovery | Get service discovery information including workers for debugging and connectivity testing. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_setup_ssh_keys` | | `Dict[str, Any]` | Setup | Setup SSH keys for infrastructure access and upload to DigitalOcean. |
| | `_load_deployment_config` | | `Dict[str, Any]` | Setup | Load deployment configuration including worker definitions and initialize deployment manager. |
| | `_validate_infrastructure_spec` | | `Dict[str, Any]` | Validation | Validate infrastructure specification structure and content including worker assignments. |
| | `_plan_infrastructure_changes_from_spec` | `force_recreate: bool = False` | `Dict[str, Any]` | Planning | Plan what infrastructure changes are needed based on JSON specification including worker services. |
| | `_execute_infrastructure_plan` | `plan: Dict[str, Any]` | `Dict[str, Any]` | Execution | Execute the planned infrastructure changes including worker deployments. |
| | `_create_droplet` | `name: str`, `config: Dict[str, Any]` | `Dict[str, Any]` | Infrastructure | Create a new droplet with specified configuration for hosting workers. |
| | `_resize_droplet` | `name: str`, `new_size: str` | `Dict[str, Any]` | Infrastructure | Resize an existing droplet (updates state, actual resize requires manual intervention). |
| | `_configure_service` | `project: str`, `service_type: str`, `config: Dict[str, Any]` | `Dict[str, Any]` | Infrastructure | Configure a service (web or worker) in the infrastructure state. |
| | `_remove_service` | `project: str`, `service_type: str` | `Dict[str, Any]` | Infrastructure | Remove a service (web or worker) from the infrastructure state. |
| | `_destroy_droplet` | `name: str` | `Dict[str, Any]` | Infrastructure | Destroy a droplet and remove from state including all hosted workers. |
| | `_setup_monitoring_relationships` | `droplet_name: str`, `role: str` | | Monitoring | Setup peer monitoring relationships for new droplet based on role including worker monitoring. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `InfrastructureState`

Manages the normalized infrastructure state with computed relationships including worker services.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `state_file: str = "config/infrastructure.json"` | | Initialization | Initialize infrastructure state with JSON file path. |
| | `save_state` | | | Persistence | Save current state to JSON file. |
| | `get_infrastructure_spec` | | `Dict[str, Any]` | Infrastructure Specification | Get infrastructure specification from JSON. |
| | `update_infrastructure_spec` | `spec: Dict[str, Any]` | | Infrastructure Specification | Update infrastructure specification. |
| | `add_project_spec` | `project: str`, `environments: List[str]`, `web_droplets: int`, `web_droplet_spec: str` | | Infrastructure Specification | Add project specification for worker and web service deployment. |
| | `remove_project_spec` | `project: str` | | Infrastructure Specification | Remove project specification including all workers. |
| | `get_required_droplets` | | `Dict[str, Dict[str, Any]]` | Computed Requirements | Calculate required droplets from spec for hosting workers and web services. |
| | `get_required_services` | | `Dict[str, Dict[str, Any]]` | Computed Requirements | Calculate required services from spec including workers and infrastructure services. |
| | `add_droplet` | `name: str`, `ip: str`, `size: str`, `region: str`, `role: str`, `monitors: List[str] = None`, `project: str = None` | | Droplet Management | Add a new droplet to the state that can host workers. |
| | `update_droplet_ip` | `name: str`, `new_ip: str` | | Droplet Management | Update droplet IP address. |
| | `remove_droplet` | `name: str` | | Droplet Management | Remove droplet from state including all hosted workers. |
| | `get_droplet` | `name: str` | `Optional[Dict[str, Any]]` | Droplet Management | Get droplet configuration by name. |
| | `get_all_droplets` | | `Dict[str, Dict[str, Any]]` | Droplet Management | Get all droplets. |
| | `get_droplets_by_role` | `role: str` | `Dict[str, Dict[str, Any]]` | Droplet Management | Get droplets filtered by role. |
| | `get_droplets_by_project` | `project: str` | `Dict[str, Dict[str, Any]]` | Droplet Management | Get droplets filtered by project including worker hosts. |
| | `add_project_service` | `project: str`, `service_type: str`, `port: int = None`, `assigned_droplets: List[str] = None`, `service_config: Dict[str, Any] = None` | | Project Management | Add a service (web or worker) to a project. |
| | `remove_project_service` | `project: str`, `service_type: str` | | Project Management | Remove a service (web or worker) from a project. |
| | `get_project_services` | `project: str` | `Dict[str, Dict[str, Any]]` | Project Management | Get all services for a project including workers. |
| | `get_all_projects` | | `Dict[str, Dict[str, Any]]` | Project Management | Get all projects including worker services. |
| | `get_service_name` | `project: str`, `service_type: str` | `str` | Computed Relationships | Generate service name from project and service type (web or worker). |
| | `get_services_on_droplet` | `droplet_name: str` | `List[str]` | Computed Relationships | Get all services running on a specific droplet including workers. |
| | `get_load_balancer_targets` | `project: str`, `service_type: str` | `List[str]` | Computed Relationships | Get load balancer targets for a service (web services only, excludes workers). |
| | `get_monitored_by` | `droplet_name: str` | `List[str]` | Computed Relationships | Get list of droplets that monitor the given droplet. |
| | `generate_resource_hash` | `project: str`, `environment: str` | `str` | Utility | Generate deterministic hash for resource naming. |
| | `get_hash_based_port` | `project: str`, `environment: str`, `base_port: int`, `port_range: int = 1000` | `int` | Utility | Generate hash-based port allocation for web services. |
| | `update_heartbeat_config` | `primary_sender: str = None`, `backup_senders: List[str] = None`, `interval_minutes: int = None` | | Health Monitoring | Update heartbeat monitoring configuration. |
| | `get_heartbeat_config` | | `Dict[str, Any]` | Health Monitoring | Get heartbeat monitoring configuration. |
| | `get_master_droplet` | | `Optional[Dict[str, Any]]` | Utility | Get the master droplet. |
| | `get_web_droplets` | | `Dict[str, Dict[str, Any]]` | Utility | Get all web droplets that can host workers. |
| | `validate_state` | | `List[str]` | Validation | Validate the current state and return any issues including worker assignments. |
| | `get_summary` | | `Dict[str, Any]` | Status | Get infrastructure summary with counts including worker services. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_load_state` | | `Dict[str, Any]` | Persistence | Load state from JSON file or create empty state. |
| | `_create_empty_state` | | `Dict[str, Any]` | Persistence | Create empty state structure with default worker support. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DistributedHealthMonitor`

Distributed health monitoring daemon that runs on each droplet including worker monitoring.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `droplet_name: str`, `infrastructure_state: InfrastructureState`, `snapshot_manager: SnapshotManager`, `load_balancer_manager: LoadBalancerManager`, `emailer=None` | | Initialization | Initialize distributed health monitor for a specific droplet including worker monitoring. |
| | `start_monitoring` | | | Monitoring | Start the distributed health monitoring daemon with all monitoring tasks including worker health checks. |
| | `get_monitoring_status` | | `Dict[str, Any]` | Status | Get current monitoring status including health results and active operations for workers. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_health_check_loop` | | | Monitoring | Main health checking loop that runs continuously including worker process checks. |
| | `_perform_health_checks` | | | Monitoring | Perform health checks on all assigned targets including worker status. |
| | `_check_target_health` | `target_droplet: str` | `HealthCheckResult` | Monitoring | Check health of a specific target droplet including worker processes. |
| | `_process_health_result` | `result: HealthCheckResult` | | Monitoring | Process a health check result and trigger consensus if needed. |
| | `_report_failure_to_peers` | `failed_target: str`, `error: str` | | Consensus | Report failure to peer droplets for consensus building. |
| | `_consensus_check_loop` | | | Consensus | Check for failure consensus and trigger recovery actions including worker recovery. |
| | `_check_failure_consensus` | | | Consensus | Check if consensus has been reached for any failures. |
| | `_handle_consensus_failure` | `failed_target: str`, `consensus: FailureConsensus` | | Recovery | Handle a target that has reached failure consensus including worker recovery. |
| | `_coordinate_recovery` | `failed_target: str` | | Recovery | Coordinate recovery of a failed target including workers as the elected leader. |
| | `_heartbeat_loop` | | | Notifications | Send heartbeat emails at regular intervals including worker status. |
| | `_send_heartbeat_if_due` | | | Notifications | Send heartbeat email if interval has passed. |
| | `_send_heartbeat_email` | `email_type: str` | | Notifications | Send heartbeat email notification with infrastructure status including workers. |
| | `_cleanup_loop` | | | Maintenance | Periodic cleanup of old monitoring data. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `ConfigManager`

Manages all configuration files and templates including worker configurations.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config_dir: str = "config"`, `templates_dir: str = "templates"` | | Initialization | Initialize configuration manager with directory paths. |
| | `initialize_all_configs` | | `Dict[str, Any]` | Setup | Initialize all configuration files with defaults including worker templates. |
| | `validate_all_configs` | | `Dict[str, Any]` | Validation | Validate all configuration files including worker configurations. |
| | `get_config_summary` | | `Dict[str, Any]` | Status | Get summary of all configuration files including worker templates. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_create_infrastructure_json` | | `Dict[str, Any]` | Configuration | Create example infrastructure.json with worker support. |
| | `_create_deployment_config` | | `Dict[str, Any]` | Configuration | Create deployment_config.json with worker service definitions. |
| | `_create_email_config` | | `Dict[str, Any]` | Configuration | Create email_config.json for notifications. |
| | `_create_sms_config` | | `Dict[str, Any]` | Configuration | Create sms_config.json for SMS alerts. |
| | `_create_all_templates` | | `Dict[str, Any]` | Templates | Create all deployment templates including worker templates. |
| | `_create_docker_compose_template` | | `Dict[str, Any]` | Templates | Create Docker Compose template with worker support. |
| | `_create_k8s_deployment_template` | | `Dict[str, Any]` | Templates | Create Kubernetes deployment template with worker support. |
| | `_create_k8s_service_template` | | `Dict[str, Any]` | Templates | Create Kubernetes service template (excludes workers). |
| | `_create_k8s_namespace_template` | | `Dict[str, Any]` | Templates | Create Kubernetes namespace template. |
| | `_create_nginx_template` | | `Dict[str, Any]` | Templates | Create nginx configuration template. |
| | `_create_vault_template` | | `Dict[str, Any]` | Templates | Create Vault configuration template. |
| | `_create_email_templates` | | `Dict[str, Any]` | Templates | Create email notification templates including worker status. |
| | `_create_env_templates` | | `Dict[str, Any]` | Templates | Create environment file templates including worker environment template. |

</details>

<br>

</div>