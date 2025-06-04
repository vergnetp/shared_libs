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
- **Email Notifications**: Comprehensive email system with SMTP support and automatic compression

## 🎯 How It Works: JSON Specification → Infrastructure

```
┌─────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
│ infrastructure.json │    │  --orchestrate      │    │   Live Droplets     │
│ (Infrastructure     │───▶│  (Creates Servers)   │───▶│   (Runtime State)   │
│  Specification)     │    │                      │    │                     │
│                     │    │ • Creates droplets   │    │ • Real IP addresses │
│ "hostomatic": {     │    │ • Assigns services   │    │ • Calculated ports  │
│   "environments":   │    │ • Configures network │    │ • Service mapping   │
│   ["prod", "uat"]   │    │ • Deploys workers    │    │ • Worker processes  │
│ }                   │    │                      │    │                     │
└─────────────────────┘    └──────────────────────┘    └─────────────────────┘
```

### Data Flow Example

**Input (infrastructure.json with infrastructure_spec):**
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
    "master": {"ip": "192.168.1.10", "role": "master", "size": "s-2vcpu-4gb", "region": "lon1"},
    "hostomatic-web1": {"ip": "192.168.1.11", "role": "web", "size": "s-2vcpu-4gb", "region": "lon1", "project": "hostomatic"},
    "hostomatic-web2": {"ip": "192.168.1.12", "role": "web", "size": "s-2vcpu-4gb", "region": "lon1", "project": "hostomatic"}
  },
  "projects": {
    "hostomatic": {
      "prod": {
        "backend": {"type": "web", "port": 8001, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
        "frontend": {"type": "web", "port": 9001, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
        "worker_cleaner": {"type": "worker", "assigned_droplets": ["hostomatic-web1"]}
      },
      "uat": {
        "backend": {"type": "web", "port": 8002, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
        "frontend": {"type": "web", "port": 9002, "assigned_droplets": ["hostomatic-web1", "hostomatic-web2"]},
        "worker_cleaner": {"type": "worker", "assigned_droplets": ["hostomatic-web1"]}
      }
    }
  },
  "health_monitoring": {
    "heartbeat_config": {
      "interval_minutes": 15
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

# Email configuration
export GMAIL_APP_PASSWORD="your_gmail_app_password"
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
  "deployment_platform": "docker",
  "auto_commit_before_deploy": true,
  "git_config": {
    "base_url": "https://github.com/yourorg",
    "url_pattern": "{base_url}/{project}.git"
  },
  "projects": {
    "hostomatic": {
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "build_context": "backend/",
          "secrets": ["db_password", "stripe_key"]
        },
        "frontend": {
          "containerfile_path": "frontend/Dockerfile",
          "build_context": "frontend/",
          "secrets": ["stripe_publishable_key"]
        },
        "worker_email": {
          "type": "worker",
          "containerfile_path": "workers/Dockerfile",
          "build_context": "workers/",
          "command": "python email_processor.py",
          "secrets": ["db_password", "sendgrid_api_key"]
        },
        "scheduler": {
          "type": "worker",
          "containerfile_path": "scheduler/Dockerfile",
          "build_context": "scheduler/",
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
python orchestrator.py --orchestrate --force --yes

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

# Deploy with image rebuilding instead of promotion
python orchestrator.py --deploy-prod hostomatic --rebuild-images
```

### Health Monitoring
```bash
# Start monitoring on master
python orchestrator.py --monitor master &

# Start monitoring on web servers  
python orchestrator.py --monitor hostomatic-web1 &
python orchestrator.py --monitor hostomatic-web2 &
```

### Recovery and Maintenance
```bash
# Emergency recovery
python orchestrator.py --recover failed-droplet-name

# Cleanup old resources
python orchestrator.py --cleanup --dry-run
python orchestrator.py --cleanup

# Update administrator IP
python orchestrator.py --update-ip 203.0.113.100/32

# Reproduce deployment from tag
python orchestrator.py --reproduce v1.0.0-uat-20250603-1200 --reproduce-dir ./reproduced
```

## 🔧 Configuration Files Reference

### infrastructure.json
Single source of truth for your infrastructure with transactional orchestration:
```json
{
  "droplets": {
    "master": {
      "ip": "192.168.1.10",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "master"
    },
    "hostomatic-web1": {
      "ip": "192.168.1.11",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "web",
      "project": "hostomatic"
    }
  },
  "projects": {
    "hostomatic": {
      "prod": {
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
    }
  },
  "health_monitoring": {
    "heartbeat_config": {
      "interval_minutes": 15
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
  "auto_commit_before_deploy": true,
  "git_config": {
    "base_url": "https://github.com/yourorg",
    "url_pattern": "{base_url}/{project}.git",
    "default_branch": "main"
  },
  "projects": {
    "hostomatic": {
      "versioning": {
        "auto_tag_uat": true,
        "tag_format": "v{version}-uat-{timestamp}",
        "prod_uses_uat_tags": true
      },
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "build_context": "backend/",
          "secrets": ["db_password", "stripe_key"]
        },
        "worker_email": {
          "type": "worker",
          "containerfile_path": "workers/Dockerfile",
          "build_context": "workers/",
          "command": "python email_processor.py",
          "secrets": ["db_password", "sendgrid_api_key"]
        }
      }
    }
  }
}
```

### email_config.json
```json
{
  "provider": "smtp",
  "from_address": "alerts@yourdomain.com",
  "reply_to": "admin@yourdomain.com",
  "default_subject_prefix": "[Infrastructure] ",
  "max_file_size_mb": 25,
  "default_recipients": ["admin@yourdomain.com"],
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465,
  "smtp_user": "alerts@yourdomain.com",
  "smtp_password": "GMAIL_APP_PASSWORD",
  "use_ssl": true
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

## 📧 Email System

The system includes a comprehensive email notification system with the following features:

### Email Features
- **Multi-Provider Support**: SMTP (including Gmail) with extensible adapter pattern
- **Rich Content**: Both HTML and plain text email support
- **Attachments**: File attachment support with automatic compression
- **Template System**: Email templates for heartbeat, recovery notifications
- **Security**: Safe attachment handling with size limits and compression

### Email Usage
```python
from backend.emailing import Emailer, EmailConfig

# Configure email
config = EmailConfig(
    provider="smtp",
    from_address="alerts@yourdomain.com",
    smtp_host="smtp.gmail.com",
    smtp_port=465,
    smtp_user="alerts@yourdomain.com",
    smtp_password="your_app_password",
    use_ssl=True,
    default_recipients=["admin@yourdomain.com"]
)

# Send notification
emailer = Emailer(config)
emailer.send_email(
    subject="Infrastructure Alert",
    recipients=["admin@yourdomain.com"],
    html="<h1>Alert</h1><p>System status update</p>",
    attached_file="report.pdf",
    compress=True
)
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

### Platform Support

The system supports multiple container platforms:

- **Docker**: Default platform using Docker Compose
- **Kubernetes**: YAML manifests with namespace isolation
- **Podman**: Alternative container runtime

Switch platforms in `deployment_config.json`:
```json
{
  "deployment_platform": "kubernetes"
}
```

### Custom Worker Types

Add custom workers to deployment config:
```json
{
  "worker_custom": {
    "type": "worker",
    "containerfile_path": "custom_workers/Dockerfile",
    "build_context": "custom_workers/",
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

### Deployment Versioning

The system supports sophisticated deployment versioning:

1. **Unified Tagging**: Single tag covers both project and shared-libs
2. **UAT → Production**: Production deployments use tested UAT images
3. **Reproduction**: Exact deployment reproduction from any tag
4. **Auto-commit**: Automatic commit and tagging before deployment

Example deployment with versioning:
```bash
# Deploy to UAT (creates tag: deploy-uat-20250603-120000)
python orchestrator.py --deploy-uat hostomatic --local

# Deploy to production (promotes UAT images)
python orchestrator.py --deploy-prod hostomatic

# Reproduce exact deployment later
python orchestrator.py --reproduce deploy-uat-20250603-120000
```

### Health Monitoring Features

- **Distributed Consensus**: Multiple monitors must agree on failure
- **Deterministic Leader Election**: Lowest IP address coordinates recovery
- **Automatic Recovery**: Snapshot-based recovery with service migration
- **Email Heartbeats**: Regular "all OK" emails with infrastructure status
- **Backup Notification**: Backup servers send alerts when master is down

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `InfrastructureOrchestrator`

Main orchestrator that coordinates all infrastructure operations including worker management.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config_dir: str = "config"` | | Initialization | Initialize the orchestrator with configuration directory. |
| | `initialize_system` | | `Dict[str, Any]` | Setup | Initialize the entire orchestration system including SSH keys and configurations. |
| | `orchestrate_infrastructure` | `force_recreate: bool = False`, `skip_confirmation: bool = False` | `Dict[str, Any]` | Infrastructure | Main orchestration function - create infrastructure from JSON specification with transactional approach. |
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
| | `_analyze_infrastructure_state` | | `Dict[str, Any]` | Planning | Analyze current vs desired state and determine what changes are needed including worker services. |
| | `_validate_infrastructure_changes` | `analysis: Dict[str, Any]` | `Dict[str, Any]` | Validation | Validate proposed infrastructure changes and get user confirmation with cost estimates. |
| | `_plan_transactional_execution` | `analysis: Dict[str, Any]`, `force_recreate: bool` | `Dict[str, Any]` | Planning | Plan the transactional execution with service migration including worker migrations. |
| | `_execute_transactional_plan` | `plan: Dict[str, Any]` | `Dict[str, Any]` | Execution | Execute the planned infrastructure changes including worker deployments with rollback capability. |
| | `_create_droplet` | `name: str`, `config: Dict[str, Any]` | `Dict[str, Any]` | Infrastructure | Create a new droplet with specified configuration for hosting workers. |
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
| | `get_desired_droplets` | | `Dict[str, Dict[str, Any]]` | Transactional Operations | Get droplets as defined in JSON (desired state). |
| | `get_actual_droplets_from_do` | `do_manager` | `Dict[str, Dict[str, Any]]` | Transactional Operations | Get actual droplets from DigitalOcean. |
| | `get_droplets_to_create` | `do_manager` | `List[Dict[str, Any]]` | Transactional Operations | Get droplets that need to be created. |
| | `get_droplets_to_modify` | `do_manager` | `List[Dict[str, Any]]` | Transactional Operations | Get droplets that need to be modified (recreated with different specs). |
| | `get_droplets_to_delete` | `do_manager` | `List[Dict[str, Any]]` | Transactional Operations | Get droplets that exist in DO but not in desired state. |
| | `get_ip_corrections_needed` | `do_manager` | `List[Dict[str, Any]]` | Transactional Operations | Get droplets where JSON IP doesn't match DO IP (need correction). |
| | `update_droplet_ip` | `name: str`, `new_ip: str` | | Droplet Management | Update droplet IP address in JSON. |
| | `get_services_on_droplet` | `droplet_name: str` | `List[Dict[str, Any]]` | Service Management | Get all services running on a specific droplet with full context including workers. |
| | `get_candidate_droplets_for_service_migration` | `service_info: Dict[str, Any]`, `exclude_droplets: List[str] = None` | `List[str]` | Service Management | Get candidate droplets where a service can be migrated. |
| | `plan_service_migration` | `droplet_to_remove: str` | `Dict[str, Any]` | Service Management | Plan how to migrate services away from a droplet that will be removed. |
| | `execute_service_migration` | `migration_plan: Dict[str, Any]` | | Service Management | Execute the planned service migrations. |
| | `add_project_service` | `project: str`, `service_type: str`, `environment: str = None`, `port: int = None`, `assigned_droplets: List[str] = None`, `service_config: Dict[str, Any] = None` | | Project Management | Add a service (web or worker) to a project. |
| | `remove_project_service` | `project: str`, `service_type: str`, `environment: str = None` | | Project Management | Remove a service (web or worker) from a project. |
| | `get_project_services` | `project_key: str` | `Dict[str, Dict[str, Any]]` | Project Management | Get all services for a project-environment key. |
| | `get_all_projects` | | `Dict[str, Dict[str, Any]]` | Project Management | Get all projects with flat keys for backward compatibility. |
| | `add_droplet` | `name: str`, `ip: str`, `size: str`, `region: str`, `role: str`, `monitors: List[str] = None`, `project: str = None` | | Droplet Management | Add a new droplet to the state. |
| | `remove_droplet` | `name: str` | | Droplet Management | Remove droplet from state. |
| | `get_droplet` | `name: str` | `Optional[Dict[str, Any]]` | Droplet Management | Get droplet configuration. |
| | `get_all_droplets` | | `Dict[str, Dict[str, Any]]` | Droplet Management | Get all droplets. |
| | `get_droplets_by_role` | `role: str` | `Dict[str, Dict[str, Any]]` | Droplet Management | Get droplets filtered by role. |
| | `get_droplets_by_project` | `project: str` | `Dict[str, Dict[str, Any]]` | Droplet Management | Get droplets filtered by project. |
| | `get_required_droplets` | | `Dict[str, Dict[str, Any]]` | Legacy Methods | Get required droplets (same as desired droplets). |
| | `get_required_services` | | `Dict[str, Dict[str, Any]]` | Legacy Methods | Get required services (same as current services with flat keys). |
| | `add_project_spec` | `project: str`, `environments: List[str]`, `web_droplets: int`, `web_droplet_spec: str` | | Legacy Methods | Add project by creating actual droplets and project structure. |
| | `remove_project_spec` | `project: str` | | Legacy Methods | Remove project and its associated droplets. |
| | `get_service_name` | `project: str`, `service_type: str` | `str` | Computed Relationships | Generate service name from project and service type. |
| | `get_load_balancer_targets` | `project: str`, `service_type: str` | `List[str]` | Computed Relationships | Get load balancer targets for a service (web services only). |
| | `get_monitored_by` | `droplet_name: str` | `List[str]` | Computed Relationships | Get list of droplets that monitor the given droplet. |
| | `generate_resource_hash` | `project: str`, `environment: str` | `str` | Computed Relationships | Generate deterministic hash for resource naming. |
| | `get_hash_based_port` | `project: str`, `environment: str`, `base_port: int`, `port_range: int = 1000` | `int` | Computed Relationships | Generate hash-based port allocation. |
| | `update_heartbeat_config` | `interval_minutes: int = None` | | Health Monitoring | Update heartbeat monitoring configuration. |
| | `get_heartbeat_config` | | `Dict[str, Any]` | Health Monitoring | Get heartbeat monitoring configuration. |
| | `get_master_droplet` | | `Optional[Dict[str, Any]]` | Utility Methods | Get the master droplet. |
| | `get_web_droplets` | | `Dict[str, Dict[str, Any]]` | Utility Methods | Get all web droplets. |
| | `validate_state` | | `List[str]` | Utility Methods | Validate the current state and return any issues. |
| | `get_summary` | | `Dict[str, Any]` | Utility Methods | Get infrastructure summary. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_load_state` | | `Dict[str, Any]` | Initialization | Load state from JSON file or create empty state. |
| | `_create_empty_state` | | `Dict[str, Any]` | Initialization | Create empty state structure. |
| | `_get_flat_project_key` | `project: str`, `environment: str` | `str` | Project Management | Generate flat project key from project and environment. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `Emailer`

Main class for sending emails with provider abstraction and attachment support.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config: EmailConfig` | | Initialization | Initialize the emailer with configuration. |
| `@try_catch` | `compress_file` | `data: Union[str, bytes]` | `bytes` | Utility | Compresses a file or bytes into a ZIP archive. |
| `@try_catch` | `send_email` | `subject: str`, `recipients: List[str]`, `text: Optional[str] = None`, `html: Optional[str] = None`, `attached_file: Optional[Union[str, bytes]] = None`, `compress: Optional[bool] = False`, `attached_file_name: Optional[str] = None`, `from_address: Optional[str] = None`, `reply_to: Optional[str] = None`, `cc: Optional[List[str]] = None`, `bcc: Optional[List[str]] = None`, `headers: Optional[Dict[str, str]] = None` | `Dict[str, Any]` | Email | Send an email with optional text, HTML content, and attachments. |
| | `close` | | `None` | Lifecycle | Close adapter connections and perform cleanup. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `EmailConfig`

Configuration for email operations.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `provider: str = "smtp"`, `from_address: Optional[str] = None`, `reply_to: Optional[str] = None`, `default_subject_prefix: str = ""`, `max_file_size_mb: int = 25`, `default_recipients: Optional[List[str]] = None`, `**provider_settings` | | Initialization | Initialize email configuration with connection parameters. |
| | `with_overrides` | `**overrides` | `EmailConfig` | Configuration | Create a new configuration with specific overrides. |
| | `get_provider_setting` | `key: str`, `default: Any = None` | `Any` | Configuration | Get a provider-specific setting. |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert configuration to dictionary. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_validate_config` | | | Validation | Validate configuration values and adjust if necessary. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `EmailAdapter`

Base interface for all email provider adapters.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@abstractmethod` | `send_email` | `subject: str`, `recipients: List[str]`, `text: Optional[str] = None`, `html: Optional[str] = None`, `from_address: Optional[str] = None`, `reply_to: Optional[str] = None`, `cc: Optional[List[str]] = None`, `bcc: Optional[List[str]] = None`, `attachments: Optional[List[Dict[str, Any]]] = None`, `headers: Optional[Dict[str, str]] = None` | `Dict[str, Any]` | Email | Send an email. |
| `@abstractmethod` | `close` | | `None` | Lifecycle | Close connections and perform cleanup. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `SMTPAdapter`

SMTP email provider adapter.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config: EmailConfig` | | Initialization | Initialize SMTP adapter with configuration. |
| `@try_catch` | `send_email` | `subject: str`, `recipients: List[str]`, `text: Optional[str] = None`, `html: Optional[str] = None`, `from_address: Optional[str] = None`, `reply_to: Optional[str] = None`, `cc: Optional[List[str]] = None`, `bcc: Optional[List[str]] = None`, `attachments: Optional[List[Dict[str, Any]]] = None`, `headers: Optional[Dict[str, str]] = None` | `Dict[str, Any]` | Email | Send an email via SMTP. |
| | `close` | | `None` | Lifecycle | Close SMTP connection if open. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_connect` | | | Connection | Connect to the SMTP server. |
| | `_add_attachment` | `msg: MIMEMultipart`, `attachment: Dict[str, Any]` | `None` | Email | Add an attachment to the email. |

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
| | `_process_health_result` | `result: HealthCheckResult` | | Monitoring | Process a health check result and trigger recovery if needed. |
| | `_am_i_recovery_leader_for` | `failed_target: str` | `bool` | Recovery | Deterministically determine if this server should lead recovery (lowest IP wins). |
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
| | `_create_email_templates` | | `Dict[str, Any]` | Templates | Create email notification templates including worker status. |
| | `_create_env_templates` | | `Dict[str, Any]` | Templates | Create environment file templates including worker environment template. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `PlatformManager`

Manages platform-specific operations across different container platforms.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `platform: str = 'docker'`, `secret_manager=None` | | Initialization | Initialize platform manager with specified platform and secret manager. |
| `@classmethod` | `get_available_platforms` | | `List[str]` | Platform Information | Get list of available platforms (docker, kubernetes). |
| | `get_platform_name` | | `str` | Platform Information | Get current platform name. |
| | `get_platform_capabilities` | | `Dict[str, bool]` | Platform Information | Get platform capabilities including secrets, networking, auto-scaling support. |
| | `build_image` | `image_name: str`, `containerfile_path: str`, `build_context: str`, `build_args: Dict[str, str] = None` | `bool` | Image Management | Build container image using platform-specific method. |
| | `deploy_service` | `context: Dict[str, Any]`, `config_file_path: str` | `bool` | Service Deployment | Deploy service using platform-specific configuration. |
| | `generate_deployment_config` | `context: Dict[str, Any]` | `str` | Service Deployment | Generate platform-specific deployment configuration. |
| | `get_config_file_name` | `service_name: str` | `str` | Service Deployment | Get platform-specific configuration file name. |
| | `get_deploy_command` | `config_file: str` | `str` | Service Deployment | Get platform-specific deployment command. |
| | `check_service_status` | `service_name: str` | `str` | Service Management | Check service status. |
| | `get_service_logs` | `service_name: str`, `lines: int = 100` | `str` | Service Management | Get service logs. |
| | `stop_service` | `service_name: str` | `bool` | Service Management | Stop service. |
| | `remove_service` | `service_name: str` | `bool` | Service Management | Remove service. |
| | `restart_service` | `service_name: str` | `bool` | Service Management | Restart service. |
| | `create_secrets` | `project: str`, `environment: str`, `secrets: Dict[str, str]` | `List[str]` | Secret Management | Create secrets using platform-specific method. |
| | `remove_secret` | `secret_name: str`, `**kwargs` | `bool` | Secret Management | Remove a secret. |
| | `list_secrets` | `**kwargs` | `List[str]` | Secret Management | List all secrets. |
| | `cleanup_project_secrets` | `project: str`, `environment: str` | `int` | Secret Management | Remove all secrets for a project/environment. |
| | `get_project_secrets` | `project: str`, `environment: str` | `List[str]` | Secret Management | Get all secrets for a project/environment. |
| | `validate_secret_availability` | `secret_name: str`, `**kwargs` | `bool` | Secret Management | Validate that a secret exists and is accessible. |
| | `get_health_check_url` | `service_name: str`, `host: str`, `port: int` | `str` | Health Checks | Generate health check URL. |
| | `get_platform_info` | | `Dict[str, Any]` | Platform Information | Get comprehensive platform information. |
| | `validate_service_configuration` | `context: Dict[str, Any]` | `List[str]` | Validation | Validate service configuration for the platform. |
| | `switch_platform` | `new_platform: str` | `bool` | Utility | Switch to a different platform. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `_get_runtime` | `platform: str` | `ContainerRuntime` | Initialization | Get platform-specific runtime. |
| | `_get_template_engine` | `platform: str` | `TemplateEngine` | Initialization | Get platform-specific template engine. |
| | `_get_secret_handler` | `platform: str`, `secret_manager` | `SecretHandler` | Initialization | Get platform-specific secret handler. |
| | `_enhance_context_with_secrets` | `context: Dict[str, Any]` | `Dict[str, Any]` | Secret Management | Enhance context with platform-specific secrets configuration. |

</details>

<br>

</div>