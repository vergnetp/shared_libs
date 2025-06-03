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

## 🎯 How It Works: CSV → Infrastructure → JSON State

```
┌─────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
│   projects.csv      │    │  --orchestrate      │    │ infrastructure.json │
│   (Your Spec)       │───▶│  (Creates Servers)   │───▶│ (Runtime State)     │
│                     │    │                      │    │                     │
│ hostomatic,3,s-2gb  │    │ • Creates droplets   │    │ • Real IP addresses │
│ digitalpixo,1,s-1gb │    │ • Assigns services   │    │ • Calculated ports  │
│                     │    │ • Configures network │    │ • Service mapping   │
└─────────────────────┘    └──────────────────────┘    └─────────────────────┘
        ▲                                                         │
        │                                                         │
        │ ◄─────────── WINNER: CSV always wins ──────────────────┘
        │              (JSON is regenerated from CSV)
        │
   ┌────▼─────┐
   │ --scale  │  Updates CSV and re-orchestrates
   └──────────┘
```

### Data Flow Example

**Input (projects.csv):**
```csv
Project,Servers,MasterSpec,WebSpec
hostomatic,3,s-2vcpu-4gb,s-2vcpu-4gb
```

**Processing:** `python orchestrator.py --orchestrate`
- Creates 3 DigitalOcean droplets (master, web1, web2)
- Gets real IP addresses from DigitalOcean
- Calculates deterministic ports using hashing
- Assigns services to appropriate servers

**Output (infrastructure.json):**
```json
{
  "droplets": {
    "master": {"ip": "192.168.1.10", "role": "master"},
    "web1": {"ip": "192.168.1.11", "role": "web"},
    "web2": {"ip": "192.168.1.12", "role": "web"}
  },
  "projects": {
    "hostomatic-prod": {
      "backend": {"port": 8001, "assigned_droplets": ["web1", "web2"]},
      "frontend": {"port": 9001, "assigned_droplets": ["web1", "web2"]}
    }
  }
}
```

### 🏆 Source of Truth: CSV Wins

**Important:** The `projects.csv` file is the **source of truth**. The `infrastructure.json` is generated from it.

- **If you edit `projects.csv`**: Run `python orchestrator.py --orchestrate` to update servers
- **If you edit `infrastructure.json`**: Your changes will be **overwritten** when you run `--orchestrate`
- **To make permanent changes**: Always edit the CSV file, not the JSON

### 🔄 Update Commands

| What Changed | Command to Run | What Happens |
|--------------|----------------|--------------|
| Edit `projects.csv` | `python orchestrator.py --orchestrate` | Creates/destroys servers to match CSV |
| Scale a project | `python orchestrator.py --scale hostomatic 5` | Updates CSV and re-orchestrates |
| Manual JSON edit | `python orchestrator.py --orchestrate` | **Overwrites your JSON changes** |
| Add new project | Edit CSV, then `--orchestrate` | Creates new infrastructure |

## 🚀 Quick Start Guide

Follow these steps in order to get your infrastructure running:

### Step 1: Install Dependencies

```bash
pip install digitalocean paramiko aiohttp jinja2 psutil pyyaml python-dotenv
```

### Step 2: Initialize Configuration Files

```bash
python infra/setup/setup.py
```

This creates:
- `config/projects.csv` - Define your projects and server requirements
- `config/deployment_config.json` - Git repositories and service definitions
- `config/email_config.json` - Email notifications setup
- `config/sms_config.json` - SMS alerts configuration
- `templates/` - Deployment templates for Docker/Kubernetes
- `.env.example` - Example environment variables

### Step 3: Set Required Environment Variables

**Option A: Create `.env` file (recommended)**
```bash
# Copy the example and fill in your values
cp .env.example .env
```

**Option B: Export environment variables**
```bash
# Required: DigitalOcean API Token
export DO_TOKEN="dop_v1_your_actual_token_here"

# Required: Your public IP for SSH access (will auto-detect if not provided)
export ADMIN_IP="203.0.113.100/32"

# Optional: Additional authorized IPs
export ADDITIONAL_IPS="203.0.113.200/32,203.0.113.201/32"
```

**Getting your DigitalOcean API Token:**
1. Go to https://cloud.digitalocean.com/account/api/tokens
2. Click "Generate New Token"
3. Give it a name like "Infrastructure Orchestrator"
4. Select "Read" and "Write" scopes
5. Copy the token and use it as `DO_TOKEN`

### Step 4: Configure Your Projects

Edit `config/projects.csv`:
```csv
Project,Servers,MasterSpec,WebSpec
hostomatic,3,s-2vcpu-4gb,s-2vcpu-4gb
digitalpixo,1,s-1vcpu-1gb,s-1vcpu-1gb
mynewproject,2,s-1vcpu-2gb,s-1vcpu-2gb
```

**Column meanings:**
- `Project`: Your project name (must match Git repository name)
- `Servers`: Total number of servers (1 = master only, 2+ = master + web servers)
- `MasterSpec`: DigitalOcean droplet size for master server
- `WebSpec`: DigitalOcean droplet size for web servers

### Step 5: Configure Git Repositories

Edit `config/deployment_config.json` to match your Git setup:
```json
{
  "git_config": {
    "base_url": "https://github.com/yourusername",
    "url_pattern": "{base_url}/{project}.git"
  },
  "projects": {
    "hostomatic": {
      "services": {
        "backend": {
          "containerfile_path": "backend/Dockerfile",
          "secrets": ["db_password", "stripe_key", "openai_api_key"]
        },
        "frontend": {
          "containerfile_path": "frontend/Dockerfile",
          "secrets": ["stripe_publishable_key"]
        }
      }
    }
  }
}
```

### Step 6: Set Project Secrets

For each project and environment, set the required secrets:

```bash
# Example for hostomatic production
export HOSTOMATIC_PROD_DB_PASSWORD="secure_database_password"
export HOSTOMATIC_PROD_STRIPE_KEY="sk_live_your_stripe_secret_key"
export HOSTOMATIC_PROD_OPENAI_API_KEY="sk-your_openai_api_key"

# Example for hostomatic UAT
export HOSTOMATIC_UAT_DB_PASSWORD="uat_database_password"
export HOSTOMATIC_UAT_STRIPE_KEY="sk_test_your_test_stripe_key"

# Global fallbacks (used if project-specific not found)
export DB_PASSWORD="default_db_password"
export STRIPE_PUBLISHABLE_KEY="pk_live_your_publishable_key"
```

### Step 7: Initialize the System

```bash
python orchestrator.py --init
```

This will:
- ✅ Generate SSH keys for server access
- ✅ Upload SSH keys to DigitalOcean
- ✅ Validate configuration files
- ✅ Test DigitalOcean API connection

### Step 8: Create Infrastructure

```bash
python orchestrator.py --orchestrate
```

This will:
- 🏗️ Create droplets based on your CSV configuration
- 🔧 Configure firewalls and networking
- 🚀 Deploy core infrastructure services
- 📋 Set up load balancer configuration
- 💾 Initialize infrastructure state

### Step 9: Check Status

```bash
python orchestrator.py --status
```

Verify that:
- All droplets are running
- Services are configured
- No validation issues exist

### Step 10: Deploy Your First Project

```bash
# Deploy to UAT first (for testing)
python orchestrator.py --deploy-uat hostomatic

# If UAT works well, deploy to production
python orchestrator.py --deploy-prod hostomatic
```

## 📋 Complete Command Reference

### Infrastructure Management
```bash
# Initialize system
python orchestrator.py --init

# Create/update infrastructure from CSV
python orchestrator.py --orchestrate

# Force recreate all resources
python orchestrator.py --orchestrate --force

# Get infrastructure status
python orchestrator.py --status

# Update your IP address
python orchestrator.py --update-ip 203.0.113.200
```

### Project Deployment
```bash
# Deploy to UAT from Git
python orchestrator.py --deploy-uat hostomatic

# Deploy to UAT from local code (for development)
python orchestrator.py --deploy-uat hostomatic --local --project-path ../hostomatic

# Deploy to production (uses latest UAT tag)
python orchestrator.py --deploy-prod hostomatic

# Reproduce exact deployment from tag
python orchestrator.py --reproduce v1.2.3-uat-20241215-1430 --reproduce-dir ./reproduced
```

### Scaling Operations
```bash
# Scale hostomatic to 5 servers
python orchestrator.py --scale hostomatic 5
```

### Health Monitoring
```bash
# Start monitoring on master (run in background)
python orchestrator.py --monitor master &

# Start monitoring on web servers
python orchestrator.py --monitor web1 &
python orchestrator.py --monitor web2 &
```

### Recovery Operations
```bash
# Emergency recovery of failed server
python orchestrator.py --recover web1

# Clean up old snapshots and resources
python orchestrator.py --cleanup --dry-run  # Preview what will be cleaned
python orchestrator.py --cleanup             # Actually clean up
```

## 🔧 Configuration Files Reference

### projects.csv
Defines your infrastructure requirements:
```csv
Project,Servers,MasterSpec,WebSpec
hostomatic,3,s-2vcpu-4gb,s-2vcpu-4gb
digitalpixo,1,s-1vcpu-1gb,s-1vcpu-1gb
```

### deployment_config.json
Defines Git repositories and service configuration:
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
          "command": "python email_processor.py",
          "secrets": ["db_password", "sendgrid_api_key"]
        }
      }
    }
  }
}
```

### email_config.json
Configure email notifications:
```json
{
  "provider": "smtp",
  "from_address": "alerts@yourdomain.com",
  "smtp_settings": {
    "host": "smtp.gmail.com",
    "port": 587,
    "use_tls": true,
    "username": "alerts@yourdomain.com",
    "password": "GMAIL_APP_PASSWORD"
  },
  "recipients": {
    "admin": "admin@yourdomain.com"
  }
}
```

## 🔐 Secret Management

### Environment Variable Naming Patterns

The system supports multiple naming patterns with this priority:

1. **Project + Environment specific** (highest priority):
   ```bash
   HOSTOMATIC_PROD_DB_PASSWORD="..."
   DIGITALPIXO_UAT_STRIPE_KEY="..."
   ```

2. **Project specific**:
   ```bash
   HOSTOMATIC_DB_PASSWORD="..."
   ```

3. **Environment specific**:
   ```bash
   PROD_DB_PASSWORD="..."
   UAT_STRIPE_KEY="..."
   ```

4. **Global fallback** (lowest priority):
   ```bash
   DB_PASSWORD="..."
   STRIPE_KEY="..."
   ```

### Common Secret Names

**Database secrets:**
- `DB_PASSWORD` - Database password
- `REDIS_PASSWORD` - Redis password

**API keys:**
- `STRIPE_KEY` - Stripe secret key
- `STRIPE_PUBLISHABLE_KEY` - Stripe publishable key
- `OPENAI_API_KEY` - OpenAI API key
- `SENDGRID_API_KEY` - SendGrid API key

**Authentication:**
- `JWT_SECRET` - JWT signing secret
- `GOOGLE_OAUTH_CLIENT_ID` - Google OAuth client ID

**Infrastructure:**
- `OPENSEARCH_ADMIN_PASSWORD` - OpenSearch admin password
- `VAULT_ROOT_TOKEN` - Vault root token

## 🏗️ Architecture Overview

### Infrastructure Layout

For a project with 3 servers:

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
   │ • Redis │   │         │   │         │
   │ • Vault │   │         │   │         │
   └─────────┘   └─────────┘   └─────────┘
```

### Service Distribution

- **Master**: Infrastructure services (DB, Redis, Vault, OpenSearch) + Load Balancer
- **Web Servers**: Application services (Backend, Frontend, Workers)
- **All Servers**: Health monitoring and peer communication

### Deployment Flow

```
1. Git Repository
   ↓
2. Clone & Build
   ↓
3. Create Secrets
   ↓
4. Deploy to Servers
   ↓
5. Update Load Balancer
   ↓
6. Create Snapshots
   ↓
7. Health Monitoring
```

## 🔍 Monitoring & Health Checks

### Distributed Health Monitoring

Each server monitors assigned peers:

- **Master** monitors all web servers
- **Web servers** monitor master and one other web server (ring topology)
- **Consensus required** for failure detection (prevents false positives)
- **Automatic recovery** when consensus is reached

### Email Notifications

- **Heartbeat emails** every 15 minutes (all systems OK)
- **Recovery notifications** when servers are automatically restored
- **Failure alerts** when recovery fails

### Health Check Endpoints

- `http://your-master-ip/health` - Load balancer health
- `http://your-master-ip/lb-status` - Load balancer status (JSON)
- `http://your-master-ip/project/environment/service/health` - Service health

## 🆘 Troubleshooting

### Common Issues

**1. SSH Key Problems**
```bash
# Check if SSH keys exist
ls -la ~/.ssh/infrastructure_key*

# Test SSH connection
ssh -i ~/.ssh/infrastructure_key root@your-droplet-ip
```

**2. DigitalOcean API Issues**
```bash
# Test API token
curl -X GET "https://api.digitalocean.com/v2/account" \
  -H "Authorization: Bearer $DO_TOKEN"
```

**3. Missing Dependencies**
```bash
# Install all required packages
pip install digitalocean paramiko aiohttp jinja2 psutil pyyaml python-dotenv
```

**4. Configuration Validation**
```bash
# Check configuration files
python orchestrator.py --status

# Look for validation issues in the output
```

**5. Service Not Starting**
```bash
# Check service logs on specific droplet
ssh -i ~/.ssh/infrastructure_key root@droplet-ip
docker logs service-name
```

### Getting Help

1. **Check Status**: `python orchestrator.py --status`
2. **Review Logs**: Look at orchestrator output for errors
3. **Validate Config**: Ensure all required files exist and are properly formatted
4. **Test Connectivity**: Verify DigitalOcean API access and SSH connectivity

## 📚 Advanced Usage

### Local Development Deployment

```bash
# Deploy from local codebase (faster iteration)
python orchestrator.py --deploy-uat hostomatic --local --project-path ../hostomatic
```

### Version Management

```bash
# List available tags
git tag -l "v*-uat-*"

# Deploy specific version to production
python orchestrator.py --deploy-prod hostomatic --tag v1.2.3-uat-20241215-1430

# Reproduce exact deployment state
python orchestrator.py --reproduce v1.2.3-uat-20241215-1430
```

### Multi-Platform Support

```bash
# Switch to Kubernetes (edit deployment_config.json)
{
  "deployment_platform": "kubernetes"
}

# Switch to Podman
{
  "deployment_platform": "podman"
}
```

### Service Discovery

```bash
# Get service endpoints for debugging
python orchestrator.py --status

# Check specific project services
curl http://master-ip/hostomatic/prod/backend/health
curl http://master-ip/hostomatic/uat/frontend/health
```

---

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