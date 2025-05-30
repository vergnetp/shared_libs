# Personal Cloud Orchestration System

A comprehensive infrastructure management system for hosting multiple small projects with automatic scaling capabilities, designed for cost-effective POC hosting with horizontal scaling potential.

## Overview

This system manages multiple projects across different environments (production, UAT, test) using a simple CSV configuration file. It automatically provisions DigitalOcean droplets, handles service deployment, manages load balancing, and provides seamless scaling capabilities.

### Key Features

- **CSV-driven configuration**: Simple project and server specification
- **Automatic resource provisioning**: Droplet creation, SSH key management, Docker installation
- **Hash-based resource allocation**: Deterministic port/credential assignment
- **Intelligent load balancing**: Master droplet acts as entry point and load balancer
- **Horizontal scaling**: Easy addition/removal of servers per project
- **Spec upgrades**: Migrate to larger droplets without downtime
- **State management**: JSON tracking of all infrastructure components
- **Git-based deployment**: Automated deployment from Git repositories to services
- **Post-deployment snapshots**: Automatic snapshots after successful deployments for fast recovery
- **Distributed health monitoring**: Peer-to-peer health checks with automatic recovery
- **Heartbeat email system**: Passive monitoring with "all OK" notifications
- **Failure recovery**: Automated backup and recovery procedures using latest-code snapshots

## Architecture

### Core Components

1. **Master Droplet**: Central hub containing Vault, load balancer, and small project services
2. **Project-Specific Droplets**: Dedicated servers for projects requiring scale
3. **Shared Services**: Vault for secrets, OpenSearch for centralized logging, monitoring
4. **Load Balancing**: Nginx-based traffic distribution across available servers
5. **Git-Based Deployment**: Automated service deployment from Git repositories
6. **Post-Deployment Snapshots**: Automatic snapshots containing latest deployed code
7. **Distributed Health Monitoring**: Each droplet monitors peers and takes recovery actions
8. **Heartbeat Notification**: Regular "all OK" emails with backup senders

### Service Distribution

Each project gets 3 environments (prod/uat/test) with:
- Backend service (API/server)
- Frontend service (website/UI)
- Database (PostgreSQL)
- Queue system (Redis)
- Centralized logging (OpenSearch)
- All secrets stored in centralized Vault or Docker secrets

## Configuration

### CSV Format
```csv
Project,Servers,MasterSpec,WebSpec
digitalpixo,1,s-2vcpu-4gb,s-1vcpu-1gb
hostomatic,4,s-8vcpu-16gb,s-2vcpu-4gb
newstartup,1,s-1vcpu-1gb,s-1vcpu-1gb
```

### Resource Allocation Strategy

Resources are deterministically assigned using project+environment hashing:

```python
# Example for hostomatic-prod (hash: a1b2c3)
Backend service port: 8000 + (hash % 1000) = 8123
Frontend service port: 9000 + (hash % 1000) = 9123
Redis port: 6000 + (hash % 1000) = 6123
Database port: 5000 + (hash % 1000) = 5123
Database name: hostomatic_prod_a1b2c3
Database user: user_a1b2c3
```

## State Management

### JSON State Structure (Normalized)
```json
{
  "droplets": {
    "master": {
      "ip": "1.2.3.4",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "master",
      "monitors": ["web1", "web2", "web3"],
      "services": ["vault", "opensearch", "load_balancer"]
    },
    "web1": {
      "ip": "1.2.3.5",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "web",
      "monitors": ["master", "web2"]
    },
    "web2": {
      "ip": "1.2.3.6",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "web",
      "monitors": ["master", "web3"]
    },
    "web3": {
      "ip": "1.2.3.7",
      "size": "s-2vcpu-4gb",
      "region": "lon1",
      "role": "web",
      "monitors": ["master", "web1"]
    }
  },
  "projects": {
    "hostomatic-prod": {
      "backend": {"port": 8123, "assigned_droplets": ["web1", "web2"]},
      "frontend": {"port": 9123, "assigned_droplets": ["web1", "web2"]},
      "database": {"port": 5123, "assigned_droplets": ["web1"]},
      "redis": {"port": 6123, "assigned_droplets": ["web1"]},
      "opensearch": {"port": 9456, "assigned_droplets": ["master"]},
      "vault": {"port": 8456, "assigned_droplets": ["master"]},
      "worker_email": {"type": "worker", "assigned_droplets": ["web1"]},
      "worker_reports": {"type": "worker", "assigned_droplets": ["web2"]},
      "scheduler": {"type": "worker", "assigned_droplets": ["web1"]}
    },
    "digitalpixo-uat": {
      "backend": {"port": 8789, "assigned_droplets": ["web1"]},
      "frontend": {"port": 9789, "assigned_droplets": ["web1"]},
      "database": {"port": 5789, "assigned_droplets": ["web1"]},
      "redis": {"port": 6789, "assigned_droplets": ["web1"]},
      "opensearch": {"port": 9234, "assigned_droplets": ["master"]},
      "vault": {"port": 8234, "assigned_droplets": ["master"]}
    },
    "infrastructure": {
      "nginx": {"type": "infrastructure", "port": 80, "assigned_droplets": ["master"]},
      "nginx_ssl": {"type": "infrastructure", "port": 443, "assigned_droplets": ["master"]}
    }
    "digitalpixo-prod": {
      "backend": {"port": 8456, "assigned_droplets": ["master"]},
      "frontend": {"port": 9456, "assigned_droplets": ["master"]},
      "database": {"port": 5456, "assigned_droplets": ["master"]},
      "redis": {"port": 6456, "assigned_droplets": ["master"]}
    },
    "digitalpixo-uat": {
      "backend": {"port": 8789, "assigned_droplets": ["web1"]},
      "frontend": {"port": 9789, "assigned_droplets": ["web1"]},
      "database": {"port": 5789, "assigned_droplets": ["web1"]},
      "redis": {"port": 6789, "assigned_droplets": ["web1"]}
    },
    "newstartup-prod": {
      "backend": {"port": 8234, "assigned_droplets": ["master"]},
      "frontend": {"port": 9234, "assigned_droplets": ["master"]},
      "database": {"port": 5234, "assigned_droplets": ["master"]},
      "redis": {"port": 6234, "assigned_droplets": ["master"]}
    }
  },
  "health_monitoring": {
    "heartbeat_config": {
      "primary_sender": "master",
      "backup_senders": ["web1", "web2"],
      "interval_minutes": 15
    }
  }
}
```

### Computed Values (Generated Dynamically)
```python
# Get all services running on a specific droplet (including workers)
def get_services_on_droplet(droplet_name: str) -> List[str]:
    services = []
    for project, config in state['projects'].items():
        for service_type, service_config in config.items():
            if droplet_name in service_config['assigned_droplets']:
                services.append(f"{project}-{service_type}")
    return services

# Get load balancer targets for web services only (workers don't need load balancing)
def get_load_balancer_targets(project: str, service_type: str) -> List[str]:
    service_config = state['projects'][project][service_type]
    
    # Skip workers - they don't need load balancing
    if service_config.get('type') == 'worker':
        return []
        
    targets = []
    for droplet_name in service_config['assigned_droplets']:
        droplet_ip = state['droplets'][droplet_name]['ip']
        port = service_config['port']
        targets.append(f"{droplet_ip}:{port}")
    return targets

# Examples:
# get_services_on_droplet("web1") → ["hostomatic-prod-backend", "hostomatic-prod-frontend", "hostomatic-prod-worker_email", "hostomatic-prod-scheduler"]
# get_load_balancer_targets("hostomatic-prod", "backend") → ["1.2.3.5:8123", "1.2.3.6:8123"]
# get_load_balancer_targets("hostomatic-prod", "worker_email") → [] (workers don't get load balanced)
```

## Distributed Health Monitoring

### Peer Monitoring Architecture

Each droplet monitors specific peers to ensure redundancy without duplication:

```python
# Example 4-droplet monitoring topology
{
  "master": {
    "monitors": ["web1", "web2", "web3"],
    "monitored_by": ["web1", "web2", "web3"]
  },
  "web1": {
    "monitors": ["master", "web2"], 
    "monitored_by": ["master", "web3"]
  },
  "web2": {
    "monitors": ["master", "web3"],
    "monitored_by": ["master", "web1"] 
  },
  "web3": {
    "monitors": ["master", "web1"],
    "monitored_by": ["master", "web2"]
  }
}
```

### Health Check Components (Per Droplet)

```
/opt/health/
├── monitor_peers.py         # Monitor other droplets
├── self_monitor.py          # Monitor local services  
├── recovery_actions.py      # Take corrective actions
├── heartbeat_notifier.py    # Send "all OK" emails using your Emailer
├── text_notifier.py         # Send SMS alerts for critical failures  
├── health_daemon.py         # Main health monitoring daemon
├── config/
│   ├── peer_list.json      # Other droplets to monitor
│   ├── email_config.json   # Your EmailConfig settings
│   └── sms_config.json     # SMS provider settings
└── modules/
    ├── emailer.py          # Your existing email module
    ├── email_config.py     # Your existing config module
    └── adapters/           # Your SMTP/provider adapters
```

### Computed Service Information
```python
# Service names follow pattern: {project}-{service_type}
def get_service_name(project: str, service_type: str) -> str:
    return f"{project}-{service_type}"

# Get all services running on a specific droplet
def get_services_on_droplet(droplet_name: str) -> List[str]:
    services = []
    for project, config in state['projects'].items():
        for service_type, service_config in config.items():
            if droplet_name in service_config['assigned_droplets']:
                services.append(f"{project}-{service_type}")
    return services

# Get load balancer targets for a service
def get_load_balancer_targets(project: str, service_type: str) -> List[str]:
    service_config = state['projects'][project][service_type]
    targets = []
    for droplet_name in service_config['assigned_droplets']:
        droplet_ip = state['droplets'][droplet_name]['ip']
        port = service_config['port']
        targets.append(f"{droplet_ip}:{port}")
    return targets

# Examples:
# get_services_on_droplet("web1") → ["hostomatic-prod-backend", "hostomatic-prod-frontend", "digitalpixo-uat-backend"]
# get_load_balancer_targets("hostomatic-prod", "backend") → ["1.2.3.5:8123", "1.2.3.6:8123"]
```

### Recovery Scenarios

#### Master Droplet Failure
1. **Detection**: Multiple web droplets detect master unreachable
2. **Consensus**: Droplets confirm failure with each other
3. **Leadership**: Droplet with lowest IP becomes recovery leader
4. **Actions**:
   - Promote self to temporary master (load balancer, basic services)
   - Create new master droplet from latest snapshot
   - Restore Vault from backup
   - Migrate temporary services to new master
   - Update DNS and load balancer configs

#### Web Droplet Failure
1. **Detection**: Master or peer droplets detect service unreachable
2. **Immediate**: Remove failed droplet from nginx upstream pools
3. **Recovery**: Create replacement droplet with same configuration
4. **Restoration**: Deploy services and add back to load balancer

#### Consensus Protocol
```python
def confirm_failure_with_peers(self, failed_droplet):
    """Prevent false positives - require majority agreement"""
    confirmations = 0
    for peer in self.peers:
        if peer.also_sees_failure(failed_droplet):
            confirmations += 1
    
    # Need majority agreement before taking action
    return confirmations >= (len(self.peers) // 2)
```

## Heartbeat Email System

### Primary Heartbeat (Master Droplet)
- **Frequency**: Every 15 minutes
- **Content**: Brief infrastructure status summary
- **Subject**: `✅ Infrastructure OK - 14:30`

```
All systems operational:
• Master: healthy (vault, nginx, 3 services)
• Web droplets: 3 healthy  
• Services: 12 running
• Last check: 2025-05-29 14:30:15

No action needed.
```

### Backup Heartbeat (Web Droplets)
- **Trigger**: When master fails to send heartbeat for 20+ minutes
- **Frequency**: Every 10 minutes (more urgent)
- **Subject**: `⚠️ Backup Heartbeat - Master may be down - 14:35`

```
Backup heartbeat from web1:
• Master status: unreachable
• This droplet: Healthy
• Other peers: web2 healthy, web3 healthy

Master droplet may need attention.
```

### Email Monitoring Rules
- `✅ Infrastructure OK` → Auto-archive (passive confirmation)
- `⚠️ Backup Heartbeat` → Inbox (needs attention)
- `🚨 Emergency Alert` → High priority (critical failure)
- **No emails for 30+ minutes** → Manual intervention required

## Python-Based Health Monitoring

### Main Health Daemon
```python
class HealthDaemon:
    def __init__(self, droplet_config, email_config, sms_config=None):
        self.role = droplet_config['role']
        self.peers = droplet_config['peers']
        self.services = droplet_config['services']
        
        # Initialize your existing emailer
        self.emailer = Emailer(EmailConfig(**email_config))
        self.text_notifier = TextNotifier(sms_config) if sms_config else None
        
        self.last_heartbeat = datetime.now()
        self.health_status = {}
        
    async def run_monitoring_loop(self):
        """Main monitoring loop - runs continuously"""
        while True:
            await self.check_self_health()
            await self.check_peer_health()
            await self.send_heartbeat_if_due()
            await self.take_recovery_actions_if_needed()
            await asyncio.sleep(30)  # Check every 30 seconds
```

### Heartbeat Email Integration
```python
class HeartbeatNotifier:
    def __init__(self, emailer: Emailer, recipient: str):
        self.emailer = emailer
        self.recipient = recipient
        self.last_heartbeat = {}
        
    def send_infrastructure_ok_email(self, status_summary: dict):
        """Send regular 'all OK' heartbeat using your Emailer"""
        subject = f"✅ Infrastructure OK - {datetime.now().strftime('%H:%M')}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #28a745;">🟢 All Systems Operational</h2>
            <table style="border-collapse: collapse; width: 100%;">
                <tr><td><strong>Master:</strong></td><td>{status_summary['master']['status']}</td></tr>
                <tr><td><strong>Web Droplets:</strong></td><td>{status_summary['web_count']} healthy</td></tr>
                <tr><td><strong>Total Services:</strong></td><td>{status_summary['total_services']} running</td></tr>
                <tr><td><strong>Backend Services:</strong></td><td>{status_summary['backend_services']} running</td></tr>
                <tr><td><strong>Frontend Services:</strong></td><td>{status_summary['frontend_services']} running</td></tr>
                <tr><td><strong>Last Check:</strong></td><td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
            </table>
            <p style="color: #6c757d; font-size: 14px;">No action needed.</p>
        </div>
        """
        
        text_content = f"""
        All systems operational:
        • Master: {status_summary['master']['status']} 
        • Web droplets: {status_summary['web_count']} healthy
        • Backend services: {status_summary['backend_services']} running
        • Frontend services: {status_summary['frontend_services']} running
        • Last check: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        
        No action needed.
        """
        
        self.emailer.send_email(
            subject=subject,
            recipients=[self.recipient],
            html=html_content,
            text=text_content
        )
        
    def send_backup_heartbeat(self, droplet_name: str, status_summary: dict):
        """Send backup heartbeat when master is down"""
        subject = f"⚠️ Backup Heartbeat - Master may be down - {datetime.now().strftime('%H:%M')}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #ffc107;">⚠️ Backup Heartbeat from {droplet_name}</h2>
            <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px;">
                <p><strong>Master status:</strong> {status_summary['master']['status']}</p>
                <p><strong>This droplet:</strong> Healthy</p>
                <p><strong>Other peers:</strong> {status_summary['peer_status']}</p>
            </div>
            <p style="color: #856404; margin-top: 15px;">Master droplet may need attention.</p>
        </div>
        """
        
        self.emailer.send_email(
            subject=subject,
            recipients=[self.recipient],
            html=html_content
        )
```

### Critical Failure Alerts
```python
class CriticalAlertNotifier:
    def __init__(self, emailer: Emailer, text_notifier, recipient: str, sms_number: str = None):
        self.emailer = emailer
        self.text_notifier = text_notifier
        self.recipient = recipient
        self.sms_number = sms_number
        
    def send_critical_alert(self, failure_type: str, details: dict):
        """Send both email and SMS for critical failures"""
        subject = f"🚨 CRITICAL: {failure_type} - {datetime.now().strftime('%H:%M')}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #dc3545;">🚨 CRITICAL INFRASTRUCTURE FAILURE</h2>
            <div style="background-color: #f8d7da; border: 1px solid #f5c6cb; padding: 15px; border-radius: 5px;">
                <p><strong>Failure Type:</strong> {failure_type}</p>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Details:</strong></p>
                <ul>
        """
        
        for key, value in details.items():
            html_content += f"<li><strong>{key}:</strong> {value}</li>"
        
        html_content += """
                </ul>
            </div>
            <p style="color: #721c24; margin-top: 15px;"><strong>IMMEDIATE ACTION REQUIRED</strong></p>
        </div>
        """
        
        # Send email
        self.emailer.send_email(
            subject=subject,
            recipients=[self.recipient],
            html=html_content
        )
        
        # Send SMS if configured
        if self.text_notifier and self.sms_number:
            sms_message = f"CRITICAL: {failure_type} at {datetime.now().strftime('%H:%M')}. Check email for details."
            self.text_notifier.send_sms(self.sms_number, sms_message)
```

## Implementation Components

### 1. DigitalOcean Management (`DigitalOceanManager`)
- Droplet creation and destruction
- Firewall rule management with administrator IP and droplet IPs
- SSH key upload and management
- Snapshot creation for backups
- Dynamic IP management for administrative access

### 2. SSH Key Management (`SSHKeyManager`)
- Automatic SSH key generation if not exists
- Cross-platform key path resolution
- Key upload to DigitalOcean account
- Public key distribution to servers

### 3. Server Setup (`ServerSetup`)
- Remote server configuration via SSH
- Docker installation and setup
- Additional SSH key deployment
- Service configuration and deployment
- Health monitoring agent installation

### 4. Service Orchestration
- Docker Compose generation per droplet
- Service deployment and management from Git repositories
- Health check implementation
- Rolling updates and migrations
- Post-deployment snapshot creation

### 5. Deployment Manager (`DeploymentManager`) 🆕
- Git repository integration and code pulling
- Service building and containerization
- Automated deployment to target droplets
- Post-deployment verification and testing
- Rollback capabilities for failed deployments
- Integration with snapshot creation for recovery

### 6. Snapshot Manager (`SnapshotManager`) 🆕  
- Post-deployment snapshot creation with latest code
- Snapshot lifecycle management and cleanup
- Fast recovery droplet creation from snapshots
- Snapshot validation and integrity checks
- Storage optimization and retention policies

### 5. Load Balancer Configuration (`LoadBalancerManager`)
- Dynamic nginx configuration generation based on deployed services
- Automatic upstream pool management for web services
- SSL certificate management and renewal
- Health check integration and automatic upstream removal
- Service discovery integration with infrastructure state

### 6. Infrastructure Service Manager (`InfrastructureManager`) 🆕
- Deployment and management of core infrastructure services
- Nginx load balancer with dynamic upstream generation
- OpenSearch centralized logging service deployment
- Vault secret management service deployment
- Infrastructure service health monitoring and recovery

### 5. Load Balancer Configuration
- Nginx upstream pool management
- Dynamic configuration generation
- SSL certificate management
- Health check integration
- Automatic upstream removal on failure

### 6. Distributed Health Monitor (`DistributedHealthMonitor`)
- Python-based peer-to-peer health checking using asyncio
- Integration with your existing Emailer class for notifications
- SMS alerts for critical failures via text module
- Consensus-based failure detection
- Automated recovery procedures using post-deployment snapshots
- Load balancer integration
- Rich HTML email notifications with status tables

### 7. Recovery Agent (`RecoveryAgent`)
- Python-based recovery automation using latest snapshots
- Master droplet rebuilding with progress notifications
- Web droplet replacement from post-deployment snapshots (3-5 min recovery)
- Service migration procedures
- Emergency escalation via email + SMS

## Scaling Operations

### Scale Out (Add Servers)
```csv
# Before
hostomatic,1

# After  
hostomatic,4
```

**Actions performed:**
1. Create 3 new droplets (web1, web2, web3)
2. Deploy hostomatic services across all droplets
3. Update nginx upstream pools on master
4. Configure peer monitoring relationships
5. Update JSON state with new droplet information

### Scale Up (Better Specs)
```csv
# Before
hostomatic,4,s-4vcpu-8gb,s-2vcpu-4gb

# After
hostomatic,4,s-8vcpu-16gb,s-2vcpu-4gb
```

**Actions performed:**
1. Create new droplet with upgraded specs
2. Migrate database/stateful services with zero downtime
3. Update load balancer configuration
4. Update peer monitoring relationships
5. Destroy old droplet after verification

### Force Migration
Set `droplet.ip = null` in JSON to force recreation of any droplet.

## Security Features

### Firewall Configuration
- SSH access (port 22) - administrator IP and inter-droplet communication
- Web traffic (ports 80, 443, 8000-9999) - nginx proxy access
- Internal services (5432, 6379, 8200) - restricted to known droplet IPs
- Health check ports (8080) - peer droplet access only
- Automatic firewall rule application during droplet creation
- Dynamic IP updates for administrator access

### SSH Key Management
- Single SSH key pair for all infrastructure (`~/.ssh/infrastructure_key`)
- Automatic key generation if not present
- Cross-platform compatibility (Linux, macOS, Windows 10+)
- Proper file permissions automatically set

### Secret Management

- Centralized Vault on master droplet (deployed but optional)
- Docker secrets for secure secret injection at runtime
- Database passwords and API keys stored as Docker secrets
- OS environment variables as fallback/migration path
- Automatic secret injection during deployment

## Failure Handling Strategy

### Critical Failure Scenarios

1. **Master Droplet Failure** (High Impact)
   - Detection: Web droplets detect within 60 seconds
   - Impact: Vault, load balancer, shared services down
   - Recovery: Automated promotion + rebuild from snapshot
   - RTO: 10 minutes (temporary), 30 minutes (full recovery)
   - Notification: Backup heartbeat emails immediately

2. **Database Server Failure** (High Impact)
   - Detection: Service health checks fail within 30 seconds
   - Impact: Data loss risk, service unavailability
   - Recovery: Restore from backup to new droplet
   - RTO: 15 minutes
   - Notification: Emergency alert email

#### Web Droplet Failure
1. **Detection**: Peer monitoring detects within 30 seconds
2. **Immediate**: Remove failed droplet from nginx upstream pools
3. **Recovery**: Create replacement droplet from latest post-deployment snapshot
4. **Restoration**: Update configuration and add back to load balancer
5. **RTO**: 3-5 minutes (snapshot contains latest deployed code)
6. **Notification**: Mentioned in next heartbeat email

4. **Multiple Droplet Failure** (Catastrophic)
   - Detection: No heartbeat emails for 30+ minutes
   - Impact: Service unavailability, potential data loss
   - Recovery: Manual intervention required
   - RTO: 60+ minutes
   - Notification: Absence of heartbeat emails

### Backup Strategy
```python
BACKUP_SCHEDULE = {
    "vault": {"frequency": "hourly", "retention": "30 days"},
    "databases": {"frequency": "every_6_hours", "retention": "14 days"},
    "post_deployment_snapshots": {
        "frequency": "after_each_successful_deployment", 
        "retention": "keep_last_3_per_droplet",
        "purpose": "fast_recovery_with_latest_code"
    },
    "master_snapshots": {"frequency": "daily", "retention": "7 days"},
    "configuration_state": {"frequency": "hourly", "retention": "7 days"}
}
```

### Health Monitoring Schedule
```python
MONITORING_SCHEDULE = {
    "service_health_checks": "every_30_seconds",
    "peer_droplet_checks": "every_2_minutes", 
    "database_connectivity": "every_minute",
    "vault_accessibility": "every_2_minutes",
    "heartbeat_emails": "every_15_minutes"
}
```

## Usage Workflow

### Initial Setup
1. Create CSV with project specifications
2. Set DigitalOcean API token in environment
3. Configure SMTP settings for heartbeat emails
4. Run orchestration script
5. System creates all required infrastructure
6. Verify heartbeat emails are being received

### Adding New Project
1. Add line to CSV: `newproject,1,s-1vcpu-1gb,s-1vcpu-1gb`
2. Run orchestration script
3. New services deployed to master droplet
4. Load balancer automatically configured
5. Health monitoring includes new services

### Scaling Existing Project
1. Update CSV: Change server count or specs
2. Run orchestration script
3. System detects changes and performs migration
4. Peer monitoring relationships updated
5. Zero-downtime scaling completed

### Monitoring and Maintenance
1. Daily automated backups
2. Continuous peer health monitoring
3. Regular heartbeat email confirmations
4. Automatic recovery for common failures
5. Manual intervention tools for complex issues

## File Structure

```
infrastructure/
├── orchestrator.py          # Main orchestration logic
├── droplet_manager.py       # DigitalOcean API integration
├── server_setup.py          # Remote server configuration
├── load_balancer.py         # Nginx configuration management
├── vault_manager.py         # Secret management
├── deployment_manager.py    # 🆕 Git-based service deployment
├── snapshot_manager.py      # 🆕 Post-deployment snapshot management
├── distributed_health.py    # Main health monitoring daemon
├── recovery_agent.py        # Automated recovery procedures
├── notification/            # Notification modules
│   ├── emailer.py          # Your existing email module
│   ├── email_config.py     # Your existing config module
│   ├── text_notifier.py    # SMS/text notification module  
│   └── adapters/           # Email provider adapters
├── backup_manager.py        # Automated backup system
├── config/
│   ├── projects.csv         # Project configuration
│   ├── infrastructure.json  # Current state tracking
│   ├── email_config.json    # EmailConfig settings
│   ├── sms_config.json      # SMS provider settings
│   └── deployment_config.json # 🆕 Git repos, platforms, and deployment settings
├── templates/
│   ├── docker-compose.yml   # Docker deployment template
│   ├── k8s-deployment.yml   # 🆕 Kubernetes deployment template
│   ├── k8s-service.yml      # 🆕 Kubernetes service template
│   ├── nginx.conf           # Load balancer configuration
│   ├── vault-config.hcl     # Vault configuration
│   ├── health-monitor.yml   # Health monitoring configuration
│   └── email-templates/     # HTML email templates
│       ├── heartbeat.html   # Regular heartbeat template
│       ├── backup-heartbeat.html # Backup heartbeat template
│       └── critical-alert.html  # Critical failure template
└── scripts/
    ├── deploy-health-daemon.py  # Deploy monitoring to droplets
    ├── deploy-services.py   # 🆕 Manual service deployment script
    ├── update-admin-ip.py   # 🆕 Update administrator IP in firewall rules
    ├── backup.py            # Backup automation
    ├── recovery.py          # Disaster recovery
    └── test-notifications.py   # Test email/SMS systems
```

## API Functions

### Core Operations
```python
# Create and setup new droplet with auto-generated SSH keys
server_ip = create_and_setup_droplet(
    do_token="your-token",
    droplet_name="web-server-1", 
    service_type="all-in-one",
    allowed_nginx_ips=["192.168.1.100"],
    ssh_key_path="~/.ssh/infrastructure_key"  # Auto-created if missing
)

# Setup existing server with health monitoring
success = setup_existing_server(
    server_ip="192.168.1.100",
    ssh_key_path="~/.ssh/infrastructure_key",
    additional_ssh_keys={"deploy": "ssh-rsa AAAAB3..."},
    enable_health_monitoring=True,
    peer_droplets=["1.2.3.4", "1.2.3.5"]
)

# Orchestrate entire infrastructure from CSV
orchestrate_infrastructure(
    csv_file="config/projects.csv",
    state_file="config/infrastructure.json",
    email_config="config/email_config.json",
    deployment_config="config/deployment_config.json"
)
```

### Deployment Operations 🆕
```python
# Deploy to UAT (creates version tag)
uat_result = deploy_to_uat(
    project="hostomatic",
    branch="main"  # Creates tag like "v1.2.3-uat-20250529-1430"
)

# Deploy to prod using UAT-tested version
prod_result = deploy_to_prod(
    project="hostomatic", 
    use_uat_tag=True,  # Uses the UAT tag that was tested
    reuse_uat_image=True  # Speed up by reusing UAT-built container image
)

# Deploy service with platform choice
deploy_service(
    project="hostomatic",
    environment="prod", 
    service_type="backend",
    target_droplets=["web1", "web2"],
    platform="docker",  # or "kubernetes", "podman"
    git_ref="v1.2.3-uat-20250529-1430"
)

# Switch deployment platform for project
switch_deployment_platform(
    project="hostomatic",
    from_platform="docker",
    to_platform="kubernetes",
    migrate_existing_services=True
)

# Update administrator IP in firewall rules
update_administrator_ip(
    old_ip="203.0.113.10",
    new_ip="203.0.113.20",
    update_all_droplets=True
)

# Deploy with custom environment variables
deploy_with_custom_env(
    project="hostomatic",
    environment="prod",
    custom_vars={
        "FEATURE_FLAG_X": "enabled",
        "API_RATE_LIMIT": "1000"
    }
)
```

### Management Operations
```python
# Force recreate droplet
force_recreate_droplet("web1")

# Migrate to better specs
migrate_droplet_specs("master", "s-8vcpu-16gb")

# Scale project
scale_project("hostomatic", target_servers=6)

# Emergency recovery
emergency_restore_master(snapshot_id="12345")

# Update administrator firewall access
update_admin_firewall_access(new_ip="203.0.113.20")

# Test heartbeat system
test_heartbeat_notification()

# Manual health check
check_infrastructure_health()
```

### Health Monitoring Operations
```python
# Setup distributed monitoring with your existing modules
setup_health_monitoring(
    droplet_list=["master", "web1", "web2", "web3"],
    email_config=EmailConfig(
        provider="smtp",
        from_address="alerts@yourdomain.com",
        **smtp_settings
    ),
    sms_config={
        "provider": "twilio",  # or your preferred SMS provider
        "account_sid": "...",
        "auth_token": "...",
        "from_number": "+1234567890"
    },
    recipient_email="you@yourdomain.com",
    recipient_sms="+1987654321"
)

# Test notification systems
test_heartbeat_email()
test_critical_alert_email()
test_sms_notification("Test message")

# Manual health operations
health_status = check_infrastructure_health()
send_manual_heartbeat(health_status)
trigger_recovery_procedure("master_failure")
```

## Cost Optimization

### Resource Sharing Strategy
- Multiple small projects share master droplet resources
- Only successful projects get dedicated servers
- Automatic rightsizing based on usage patterns
- Efficient resource utilization across all projects
- Health monitoring prevents resource waste from failed services

### Scaling Economics
- Start with $20/month master droplet for multiple projects
- Scale individual projects as needed ($10-20/month per additional server)
- Predictable costs based on CSV configuration
- No over-provisioning of unused resources
- Heartbeat emails prevent unnecessary manual monitoring time

## Deployment and Recovery Strategy 🆕

### Git-Based Deployment Architecture

The system uses a Git-centric approach with version tagging and flexible deployment targets:

```json
// config/deployment_config.json
{
  "deployment_platform": "docker", // "docker", "kubernetes", "podman"
  "projects": {
    "hostomatic": {
      "git_repo": "https://github.com/yourorg/hostomatic.git",
      "versioning": {
        "auto_tag_uat": true,
        "tag_format": "v{version}-uat-{timestamp}",
        "prod_uses_uat_tags": true
      },
      "services": {
        "backend": {
          "dockerfile_path": "backend/Dockerfile",
          "build_context": "backend/",
          "env_template": "templates/backend.env",
          "secrets": ["db_password", "redis_password", "stripe_key", "openai_api_key", "jwt_secret"]
        },
        "frontend": {
          "dockerfile_path": "frontend/Dockerfile", 
          "build_context": "frontend/",
          "env_template": "templates/frontend.env",
          "secrets": ["stripe_publishable_key", "google_oauth_client_id"]
        },
        "worker_email": {
          "type": "worker",
          "dockerfile_path": "workers/Dockerfile",
          "build_context": "workers/",
          "command": "python email_processor.py",
          "secrets": ["db_password", "redis_password", "sendgrid_api_key"]
        },
        "worker_reports": {
          "type": "worker", 
          "dockerfile_path": "workers/Dockerfile",
          "build_context": "workers/",
          "command": "python report_generator.py",
          "secrets": ["db_password", "redis_password", "aws_access_key", "aws_secret_key"]
        },
        "scheduler": {
          "type": "worker",
          "dockerfile_path": "scheduler/Dockerfile", 
          "build_context": "scheduler/",
          "command": "python cron_scheduler.py",
          "secrets": ["db_password", "redis_password"]
        }
      }
    },
    "digitalpixo": {
      "git_repo": "https://github.com/yourorg/digitalpixo.git",
      "services": {
        "backend": {
          "dockerfile_path": "Dockerfile",
          "build_context": "./",
          "secrets": ["db_password", "openai_api_key", "sendgrid_api_key"]
        }
      }
    }
  }
}
```

### Version Tagging and Promotion Strategy

```python
class VersionManager:
    def __init__(self, git_manager):
        self.git_manager = git_manager
        
    def deploy_to_uat(self, project: str, branch: str = "main"):
        """Deploy to UAT and create version tag"""
        
        # 1. Deploy to UAT environment
        deployment_result = self.deployment_manager.deploy_environment(
            project=project,
            environment="uat", 
            git_ref=branch
        )
        
        if deployment_result.success:
            # 2. Create version tag after successful UAT deployment
            version_tag = self.create_uat_version_tag(project)
            
            # 3. Store tag for future prod deployment
            self.store_uat_tag_for_prod(project, version_tag)
            
            return {
                "status": "success",
                "uat_deployed": True,
                "version_tag": version_tag,
                "ready_for_prod": True
            }
    
    def create_uat_version_tag(self, project: str) -> str:
        """Create Git tag after successful UAT deployment"""
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        version = self.get_next_version(project)
        tag_name = f"v{version}-uat-{timestamp}"
        
        # Create and push tag
        self.git_manager.create_tag(project, tag_name)
        self.git_manager.push_tag(project, tag_name)
        
        return tag_name
    
    def deploy_to_prod(self, project: str, use_uat_tag: bool = True):
        """Deploy to prod using UAT-tested version tag"""
        
        if use_uat_tag:
            # Use the version tag that passed UAT testing
            latest_uat_tag = self.get_latest_uat_tag(project)
            git_ref = latest_uat_tag
        else:
            # Manual override - use specific branch/commit
            git_ref = "main"  # or specified ref
            
        return self.deployment_manager.deploy_environment(
            project=project,
            environment="prod",
            git_ref=git_ref,
            reuse_uat_image=use_uat_tag  # Speed up by reusing UAT-built image
        )
```

### Platform-Agnostic Deployment

```python
class DeploymentManager:
    def __init__(self, platform: str = "docker"):
        self.platform = platform
        self.deployer = self._get_platform_deployer(platform)
        
    def _get_platform_deployer(self, platform: str):
        """Factory pattern for different deployment platforms"""
        deployers = {
            "docker": DockerDeployer(),
            "kubernetes": KubernetesDeployer(), 
            "podman": PodmanDeployer()
        }
        
        if platform not in deployers:
            raise ValueError(f"Unsupported platform: {platform}")
            
        return deployers[platform]
    
    def deploy_service(self, project, environment, service_type, droplets, git_ref):
        """Platform-agnostic service deployment"""
        
        # 1. Generate dynamic environment variables
        dynamic_env = self.generate_dynamic_environment(project, environment, service_type)
        
        # 2. Build or pull container image
        image_name = self.build_or_reuse_image(project, service_type, git_ref)
        
        # 3. Deploy using platform-specific deployer
        return self.deployer.deploy(
            image=image_name,
            environment_vars=dynamic_env,
            target_droplets=droplets,
            service_config=self.get_service_config(project, service_type)
        )

class InfrastructureManager:
    def deploy_infrastructure_services(self, droplet_name: str):
        """Deploy core infrastructure services to master droplet"""
        
        infrastructure_services = self.config.get('infrastructure_services', {})
        
        for service_name, service_config in infrastructure_services.items():
            if service_name == 'nginx':
                self.deploy_nginx_load_balancer(droplet_name, service_config)
            elif service_name == 'opensearch':
                self.deploy_opensearch(droplet_name, service_config)
            elif service_name == 'vault':
                self.deploy_vault(droplet_name, service_config)
    
    def deploy_nginx_load_balancer(self, droplet_name: str, config: dict):
        """Deploy nginx with dynamic upstream configuration"""
        
        # Generate nginx.conf with current project upstreams
        nginx_config = self.generate_nginx_config()
        
        # Deploy nginx container
        compose_config = {
            'version': '3.8',
            'services': {
                'nginx': {
                    'image': 'nginx:alpine',
                    'ports': ['80:80', '443:443'],
                    'volumes': [
                        './nginx.conf:/etc/nginx/nginx.conf:ro',
                        './ssl:/etc/nginx/ssl:ro'
                    ],
                    'networks': ['infrastructure'],
                    'restart': 'unless-stopped'
                }
            },
            'networks': {
                'infrastructure': {'external': True}
            }
        }
        
        self.deploy_service_to_droplet(droplet_name, 'nginx', compose_config)
    
    def deploy_opensearch(self, droplet_name: str, config: dict):
        """Deploy OpenSearch for centralized logging"""
        
        # Create OpenSearch admin password secret
        admin_password = self.secret_manager.get_secret('OPENSEARCH_ADMIN_PASSWORD')
        self.docker_client.create_secret('opensearch_admin_password', admin_password)
        
        compose_config = {
            'version': '3.8',
            'services': {
                'opensearch': {
                    'image': config['image'],
                    'environment': config['environment'],
                    'secrets': ['opensearch_admin_password'],
                    'ports': ['9200:9200', '9600:9600'],
                    'volumes': ['opensearch_data:/usr/share/opensearch/data'],
                    'networks': ['infrastructure'],
                    'restart': 'unless-stopped'
                }
            },
            'volumes': {
                'opensearch_data': {}
            },
            'secrets': {
                'opensearch_admin_password': {'external': True}
            },
            'networks': {
                'infrastructure': {'external': True}
            }
        }
        
        self.deploy_service_to_droplet(droplet_name, 'opensearch', compose_config)
    
    def generate_nginx_config(self) -> str:
        """Generate nginx.conf with dynamic upstreams for all projects"""
        
        upstreams = []
        locations = []
        
        # Generate upstreams for each project service
        for project, services in self.state['projects'].items():
            if project == 'infrastructure':
                continue  # Skip infrastructure services
                
            for service_type, service_config in services.items():
                if service_config.get('type') == 'worker':
                    continue  # Workers don't need load balancing
                    
                if 'port' in service_config:
                    upstream_name = f"{project}_{service_type}"
                    upstream_servers = []
                    
                    for droplet_name in service_config['assigned_droplets']:
                        droplet_ip = self.state['droplets'][droplet_name]['ip']
                        port = service_config['port']
                        upstream_servers.append(f"server {droplet_ip}:{port};")
                    
                    upstreams.append(f"""
upstream {upstream_name} {{
    {chr(10).join(upstream_servers)}
}}""")
                    
                    # Generate location block
                    locations.append(f"""
location /{project}/{service_type}/ {{
    proxy_pass http://{upstream_name}/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}}""")
        
        nginx_config = f"""
events {{
    worker_connections 1024;
}}

http {{
    {chr(10).join(upstreams)}
    
    server {{
        listen 80;
        server_name _;
        
        {chr(10).join(locations)}
        
        # Health check endpoint
        location /health {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}
    }}
}}"""
        
        return nginx_config

class KubernetesDeployer:
    def deploy(self, image, environment_vars, target_droplets, service_config):
        """Deploy using Kubernetes manifests"""
        
        # Generate K8s deployment, service, configmap
        k8s_manifests = self.generate_k8s_manifests(
            image=image,
            environment=environment_vars,
            service_config=service_config
        )
        
        # Apply to each droplet's K8s cluster
        for droplet in target_droplets:
            self.apply_manifests(droplet, k8s_manifests)
```

### Dynamic Environment Variable Generation

```python
class EnvironmentGenerator:
    def __init__(self, secret_manager):
        self.secret_manager = secret_manager
        
    def generate_dynamic_environment(self, project: str, environment: str, service_type: str, service_config: dict) -> Dict[str, str]:
        """Generate dynamic environment variables using hashes and Docker secrets"""
        
        # Generate hash for deterministic resource naming
        resource_hash = self.generate_resource_hash(project, environment)
        
        # Create Docker secrets for sensitive data
        self.create_docker_secrets(project, environment, service_config)
        
        # Get droplet/infrastructure info
        assigned_droplets = self.get_assigned_droplets(project, environment, service_type)
        db_host = self.get_database_host(project, environment)
        
        dynamic_vars = {
            # Database configuration using hashes
            "DB_USER": f"user_{resource_hash[:8]}",
            "DB_NAME": f"{project}_{environment}_{resource_hash[:8]}",
            "DB_HOST": db_host,
            "DB_PORT": str(5000 + (int(resource_hash, 16) % 1000)),
            
            # Redis configuration
            "REDIS_HOST": self.get_redis_host(project, environment),
            "REDIS_PORT": str(6000 + (int(resource_hash, 16) % 1000)),
            
            # Vault configuration (project-specific)
            "VAULT_HOST": self.get_vault_host(project, environment),
            "VAULT_PORT": str(8000 + (int(resource_hash, 16) % 1000)),  # Hash-based port
            
            # OpenSearch configuration (project-specific)
            "OPENSEARCH_HOST": self.get_opensearch_host(project, environment),
            "OPENSEARCH_PORT": str(9000 + (int(resource_hash, 16) % 1000)),  # Hash-based port
            "OPENSEARCH_INDEX": f"{project}-{environment}-logs-{resource_hash[:6]}",
            
            # Service-specific configuration (only for web services)
            "SERVICE_NAME": f"{project}-{environment}-{service_type}",
            "ENVIRONMENT": environment,
            "PROJECT": project,
            
            # Infrastructure info
            "ASSIGNED_DROPLETS": ",".join(assigned_droplets),
            "RESOURCE_HASH": resource_hash
        }
        
        # Add port only for web services, not workers
        if service_config.get('type') != 'worker':
            if service_type == "backend":
                dynamic_vars["SERVICE_PORT"] = str(8000 + (int(resource_hash, 16) % 1000))
            elif service_type == "frontend":
                dynamic_vars["SERVICE_PORT"] = str(9000 + (int(resource_hash, 16) % 1000))
        
        return dynamic_vars
    
    def create_docker_secrets(self, project: str, environment: str, service_config: dict):
        """Create Docker secrets dynamically based on service configuration"""
        
        # Get list of required secrets from service config
        required_secrets = service_config.get('secrets', [])
        
        created_secrets = []
        for secret_key in required_secrets:
            # Look for secret in environment variables using various naming patterns
            secret_value = self._find_secret_value(secret_key, project, environment)
            
            if secret_value:
                # Create Docker secret with clean name (no project/env prefix)
                docker_secret_name = f"{project}_{environment}_{secret_key}"
                self.docker_client.create_secret(docker_secret_name, secret_value)
                created_secrets.append(docker_secret_name)
                
        return created_secrets
    
    def _find_secret_value(self, secret_key: str, project: str, environment: str) -> str:
        """Find secret value using multiple naming conventions"""
        
        # Try different environment variable naming patterns
        possible_env_names = [
            f"{project.upper()}_{environment.upper()}_{secret_key.upper()}",  # HOSTOMATIC_PROD_STRIPE_KEY
            f"{secret_key.upper()}",                                          # STRIPE_KEY (global)
            f"{project.upper()}_{secret_key.upper()}",                        # HOSTOMATIC_STRIPE_KEY
            f"{environment.upper()}_{secret_key.upper()}",                    # PROD_STRIPE_KEY
        ]
        
        for env_name in possible_env_names:
            value = self.secret_manager.get_secret(env_name)
            if value:
                return value
                
        return None
    
    def generate_resource_hash(self, project: str, environment: str) -> str:
        """Generate deterministic hash for resource naming"""
        import hashlib
        hash_input = f"{project}-{environment}".encode()
        return hashlib.md5(hash_input).hexdigest()[:12]  # 12 char hash

class SecretManager:
    def __init__(self, use_vault: bool = False):
        self.use_vault = use_vault
        self.vault_client = VaultClient() if use_vault else None
        
    def get_secret(self, key: str) -> str:
        """Get secret from Vault or OS environment (fallback)"""
        if self.use_vault and self.vault_client and self.vault_client.is_available():
            try:
                return self.vault_client.get_secret(key)
            except VaultException:
                # Fallback to OS env if Vault fails
                return os.getenv(key)
        else:
            return os.getenv(key)
```

### Deployment Templates

```yaml
# templates/docker-compose.yml - Handles both web services and workers
version: '3.8'
services:
  {{service_name}}:
    image: {{image_name}}
    {{#if command}}
    command: {{command}}
    {{/if}}
    environment:
      - DB_USER={{DB_USER}}
      - DB_NAME={{DB_NAME}}
      - DB_HOST={{DB_HOST}}
      - DB_PORT={{DB_PORT}}
      {{#unless is_worker}}
      - SERVICE_PORT={{SERVICE_PORT}}
      {{/unless}}
      - ENVIRONMENT={{ENVIRONMENT}}
      - OPENSEARCH_HOST={{OPENSEARCH_HOST}}
      - OPENSEARCH_PORT={{OPENSEARCH_PORT}}
    secrets:
      {{#each secrets}}
      - {{../project}}_{{../environment}}_{{this}}
      {{/each}}
    {{#unless is_worker}}
    ports:
      - "{{SERVICE_PORT}}:{{SERVICE_PORT}}"
    {{/unless}}
    restart: unless-stopped
    networks:
      - app-network

networks:
  app-network:
    driver: bridge

secrets:
  {{#each secrets}}
  {{../project}}_{{../environment}}_{{this}}:
    external: true
  {{/each}}
```

```python
# Template generation based on service configuration
def generate_docker_compose(self, service_config, template_vars):
    """Generate docker-compose.yml based on service's secret requirements"""
    
    # Get secrets list from service config
    required_secrets = service_config.get('secrets', [])
    
    # Create template context
    template_context = {
        **template_vars,
        'secrets': required_secrets,  # Dynamic list from config
        'project': template_vars['project'],
        'environment': template_vars['environment']
    }
    
    # Render template with dynamic secrets
    template = self.jinja_env.get_template('docker-compose.yml')
    return template.render(template_context)
```

```yaml
# templates/k8s-deployment.yml - Generated dynamically
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{service_name}}
  namespace: {{project}}-{{environment}}
spec:
  replicas: {{replica_count}}
  selector:
    matchLabels:
      app: {{service_name}}
  template:
    metadata:
      labels:
        app: {{service_name}}
    spec:
      containers:
      - name: {{service_name}}
        image: {{image_name}}
        env:
        - name: DB_USER
          value: "{{DB_USER}}"
        - name: DB_HOST
          value: "{{DB_HOST}}"
        - name: SERVICE_PORT
          value: "{{SERVICE_PORT}}"
        - name: OPENSEARCH_HOST
          value: "{{OPENSEARCH_HOST}}"
        - name: OPENSEARCH_PORT
          value: "{{OPENSEARCH_PORT}}"
        volumeMounts:
        - name: secrets-volume
          mountPath: /run/secrets
        ports:
        - containerPort: {{SERVICE_PORT}}
      volumes:
      - name: secrets-volume
        secret:
          secretName: {{project}}-{{environment}}-secrets
---
apiVersion: v1
kind: Secret
metadata:
  name: {{project}}-{{environment}}-secrets
  namespace: {{project}}-{{environment}}
type: Opaque
data:
  {{#each secrets}}
  {{this}}: {{lookup ../secret_values this}}
  {{/each}}
```

### Post-Deployment Snapshot Strategy

```python
class DeploymentManager:
    def deploy_service(self, project, environment, service_type, droplets):
        """Deploy service and create recovery snapshot"""
        
        # 1. Pull latest code from Git
        repo_path = self.git_manager.pull_latest(project, branch="main")
        
        # 2. Build service container
        container_image = self.build_service(project, service_type, repo_path)
        
        # 3. Deploy to target droplets
        for droplet in droplets:
            success = self.deploy_to_droplet(container_image, droplet)
            
            if success:
                # 4. Create post-deployment snapshot with latest code
                self.snapshot_manager.create_deployment_snapshot(
                    droplet_name=droplet,
                    service_deployed=f"{project}-{environment}-{service_type}",
                    git_commit=self.git_manager.get_current_commit(project)
                )
                
        return success

class SnapshotManager:
    def create_deployment_snapshot(self, droplet_name, service_deployed, git_commit):
        """Create snapshot immediately after successful deployment"""
        
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        snapshot_name = f"{droplet_name}-deploy-{timestamp}"
        
        # Snapshot contains:
        # - Latest deployed code (just deployed)
        # - All dependencies and runtime environment
        # - Configuration and environment variables
        # - Other services already running on droplet
        
        snapshot_id = self.digitalocean.create_snapshot(droplet_name, snapshot_name)
        
        # Store metadata for recovery
        self.store_snapshot_metadata(snapshot_id, {
            "droplet_name": droplet_name,
            "timestamp": timestamp,
            "service_deployed": service_deployed,
            "git_commit": git_commit,
            "type": "post_deployment"
        })
        
        # Cleanup old snapshots (keep last 3)
        self.cleanup_old_deployment_snapshots(droplet_name, keep=3)
```

### Fast Recovery with Latest Code

```python
class RecoveryAgent:
    def recover_failed_droplet(self, failed_droplet_name):
        """Recover droplet using latest post-deployment snapshot"""
        
        # 1. Find most recent post-deployment snapshot
        latest_snapshot = self.snapshot_manager.get_latest_deployment_snapshot(
            failed_droplet_name
        )
        
        if not latest_snapshot:
            # Fallback: create fresh droplet and deploy from Git
            return self.full_deployment_recovery(failed_droplet_name)
        
        # 2. Create new droplet from snapshot (contains latest deployed code!)
        new_droplet = self.droplet_manager.create_from_snapshot(
            snapshot_id=latest_snapshot['snapshot_id'],
            name=f"{failed_droplet_name}-recovered-{int(time.time())}"
        )
        
        # 3. Update only environment-specific configurations
        self.update_droplet_environment_config(new_droplet, failed_droplet_name)
        
        # 4. Update infrastructure state
        self.update_infrastructure_state(failed_droplet_name, new_droplet)
        
        # 5. Add to load balancer
        self.load_balancer_manager.replace_upstream(failed_droplet_name, new_droplet)
        
        # Recovery complete in 3-5 minutes with latest code!
        return {
            "status": "recovered",
            "new_droplet": new_droplet,
            "recovery_time_minutes": 4,
            "code_freshness": "latest_deployed",
            "git_commit": latest_snapshot['git_commit']
        }
```

### Benefits of This Deployment Strategy

1. **Latest Code in Snapshots**: Snapshots taken after deployment contain the exact code that was just deployed
2. **Fast Recovery**: 3-5 minutes to restore failed droplet with current code  
3. **Reliable State**: Snapshots represent known-good deployments that passed tests
4. **Git Integration**: Full traceability from Git commit to deployed state to recovery snapshot
5. **Fallback Options**: If snapshot recovery fails, can always fall back to full Git deployment
6. **Zero Downtime**: Load balancer handles traffic during recovery process

### Recovery Time Objectives

- **Snapshot Recovery**: 3-5 minutes (contains latest code)
- **Full Git Deployment**: 8-15 minutes (fresh deployment)
- **Master Recovery**: 10-30 minutes (depending on complexity)
- **Database Recovery**: 5-15 minutes (from backup)

## Application Secret Access

### In Your Application Code
```javascript
// In your application - simple secret reading without project/env awareness
const fs = require('fs');

function readSecret(secretName) {
    try {
        return fs.readFileSync(`/run/secrets/${secretName}`, 'utf8').trim();
    } catch (error) {
        console.warn(`Secret ${secretName} not found`);
        return null;
    }
}

// Your application code doesn't need to know about project/environment
const dbPassword = readSecret('db_password');
const stripeKey = readSecret('stripe_key'); 
const openaiApiKey = readSecret('openai_api_key');
const jwtSecret = readSecret('jwt_secret');

// Non-sensitive config from environment variables
const dbUser = process.env.DB_USER;
const dbName = process.env.DB_NAME;
const servicePort = process.env.SERVICE_PORT;
```

```python
# Python example - clean secret access
import os

def read_secret(secret_name):
    """Read Docker secret by simple name"""
    try:
        with open(f'/run/secrets/{secret_name}', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

# Clean application code - no project/environment awareness needed
db_password = read_secret('db_password')
stripe_key = read_secret('stripe_key')
openai_api_key = read_secret('openai_api_key')
jwt_secret = read_secret('jwt_secret')

# Standard environment variables
db_user = os.getenv('DB_USER')
service_port = os.getenv('SERVICE_PORT')
```

### Secret Security Benefits

**Docker Secrets vs Environment Variables:**
- ✅ **Not visible in `docker inspect`** (secrets mounted as files)
- ✅ **Encrypted at rest** (Docker encrypts secrets in swarm mode)
- ✅ **File permissions** (secrets files have restricted access)
- ✅ **Audit trail** (Docker logs secret access)
- ✅ **No process environment exposure** (not in `ps auxe`)

**Migration Path:**
```python
# Easy migration from env vars to Docker secrets
def get_secret(name):
    # Try Docker secret first
    if os.path.exists(f'/run/secrets/{name}'):
        with open(f'/run/secrets/{name}', 'r') as f:
            return f.read().strip()
    # Fallback to environment variable
    return os.getenv(name.upper())
```
   - DigitalOcean account and API token
   - Python 3.8+ with required packages
   - SSH key generation capability
   - SMTP account for heartbeat emails (Gmail, SendGrid, etc.)

2. **Installation**
   ```bash
   pip install python-digitalocean paramiko smtplib
   git clone [repository]
   cd infrastructure-orchestrator
   ```

3. **Configuration**
   - Set `DO_TOKEN` environment variable
   - Configure email settings in `config/email_config.json`
   - Create `config/projects.csv` with your projects
   - Run initial orchestration

4. **First Deployment**
   ```bash
   python orchestrator.py --init --enable-monitoring
   ```

5. **Verify Setup**
   - Check that heartbeat emails arrive every 15 minutes
   - Test health monitoring by temporarily stopping a service
   - Verify backup systems are creating snapshots

This system provides a complete infrastructure management solution that scales from hobby projects to production workloads while maintaining cost efficiency, operational simplicity, and robust failure recovery through distributed monitoring and automated notifications.