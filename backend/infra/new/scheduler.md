# Centralized Job Scheduler

A powerful, centralized scheduling system that integrates with your existing container infrastructure. Manage all scheduled tasks from one container using familiar cron syntax with dynamic job management.

## 🎯 Benefits

### **Resource Efficiency**
- **One cron daemon** instead of N cron daemons across containers
- **Shared codebase** - no duplication of your project files
- **Minimal overhead** - lightweight scheduler container

### **Operational Excellence**
- **Dynamic job management** - add/remove jobs without rebuilding
- **Centralized logging** - all job logs in one place
- **Live monitoring** - see all scheduled tasks at once
- **Configuration as code** - jobs defined in YAML/JSON files

### **Scalability**
- **Single server** - one scheduler per server
- **Multiple servers** - role-based or hierarchical scheduling  
- **Large codebases** - handles complex Python projects seamlessly

## 🏗️ Architecture

```
┌─────────────────────────────────────┐
│  Scheduler Container                │
├─────────────────────────────────────┤
│  • Cron daemon                     │
│  • Job management API              │
│  • Your complete codebase          │
│  • All job scripts                 │
├─────────────────────────────────────┤
│  Mounted Volumes:                   │
│  /app/jobs/     (job scripts)       │
│  /app/config/   (job configs)       │
│  /app/backups/  (backup storage)    │
│  /var/log/jobs/ (job logs)          │
│  /run/secrets/  (shared secrets)    │
└─────────────────────────────────────┘
```

## 🚀 Quick Start

### 1. **Build the Scheduler Container**

```python
# In your test.py or setup script
from services_config import CommonServiceConfigs
from container_generator import ContainerGenerator
from container_manager import ContainerManager

# Create scheduler configuration
scheduler_config = CommonServiceConfigs.centralized_scheduler()

# Generate Dockerfile
dockerfile = ContainerGenerator.generate_container_file_content(
    ServiceTypes.WORKER, "myproject", Envs.PROD, "scheduler", 
    service_config=scheduler_config
)

# Build image
ContainerManager.build_image(dockerfile, "myproject-prod-scheduler", "latest")
```

### 2. **Create Job Scripts**

```python
# jobs/backup_job.py
import sys
sys.path.insert(0, '/app')

from backup_manager import PostgreSQLBackupManager
from enums import Envs

def main():
    project, env, service = sys.argv[1:4]
    mgr = PostgreSQLBackupManager(project, Envs(env), service)
    result = mgr.create_backup()
    if result:
        print(f"✅ Backup created: {result}")
        mgr.cleanup_old_backups(7)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### 3. **Configure Jobs**

```yaml
# config/jobs.yml
jobs:
  - name: backup_maindb
    schedule: "0 2 * * *"
    script_path: "jobs/backup_job.py"
    args: ["myproject", "prod", "maindb"]
    description: "Daily database backup"
    enabled: true

  - name: health_check
    schedule: "*/10 * * * *"
    script_path: "jobs/health_check_job.py"
    args: ["myproject", "prod", "maindb", "cache"]
    description: "Service health monitoring"
    enabled: true
```

### 4. **Run the Scheduler**

```bash
# Start scheduler with volume mounts
docker run -d --name scheduler \
  --network myproject_prod_network \
  -v ./jobs:/app/jobs \
  -v ./config:/app/config \
  -v ./backups:/app/backups \
  -v ./logs:/var/log/jobs \
  -v ./secrets:/run/secrets \
  myproject-prod-scheduler:latest
```

## 🎮 Job Management

### **Dynamic Job Management**

```bash
# Add a new job (no rebuild required!)
docker exec scheduler python scheduler.py add \
  weekly_cleanup "0 4 * * 0" jobs/cleanup_job.py myproject prod

# List all jobs
docker exec scheduler python scheduler.py list

# Run a job manually
docker exec scheduler python scheduler.py run backup_maindb

# Enable/disable jobs
docker exec scheduler python scheduler.py enable backup_maindb
docker exec scheduler python scheduler.py disable backup_maindb

# Remove a job
docker exec scheduler python scheduler.py remove old_job
```

### **Configuration Management**

```bash
# Load jobs from config file
docker exec scheduler python scheduler.py load-config jobs.yml

# Save current jobs to config
docker exec scheduler python scheduler.py save-config current_jobs.yml
```

## 📊 Monitoring & Logging

### **Real-time Monitoring**

```bash
# Watch scheduler logs
docker logs -f scheduler

# Monitor cron daemon
docker exec scheduler tail -f /var/log/cron.log

# Monitor specific job logs
docker exec scheduler tail -f /var/log/jobs/backup_maindb.log

# Check scheduler status
docker exec scheduler python scheduler.py status
```

### **Log Structure**

```
/var/log/jobs/
├── scheduler.log        # Scheduler service logs
├── backup_maindb.log    # Individual job logs
├── health_check.log
└── cleanup.log

/var/log/cron.log        # Cron daemon logs
```

## 🛠️ Job Templates

### **Using Existing Codebase**

All job scripts can use your existing modules:

```python
# jobs/comprehensive_maintenance.py
import sys
sys.path.insert(0, '/app')

# Use ALL your existing modules
from backup_manager import PostgreSQLBackupManager
from container_manager import ContainerManager  
from service_locator import ServiceLocator
from secrets_manager import SecretsManager

def comprehensive_maintenance(project: str, env: str):
    # Complex job using entire codebase
    mgr = PostgreSQLBackupManager(project, env)
    backup_file = mgr.create_backup()
    
    cm = ContainerManager()
    cm.cleanup_local_images(project, keep_count=10)
    
    # ... more complex operations
```

### **Pre-built Job Templates**

```python
from scheduler import JobTemplates

# Create backup job
backup_job = JobTemplates.backup_job("myproject", Envs.PROD, "maindb")

# Create cleanup job  
cleanup_job = JobTemplates.cleanup_job("myproject", Envs.PROD)

# Create health check job
health_job = JobTemplates.health_check_job("myproject", Envs.PROD, ["maindb", "cache"])
```

## 📋 Available Job Scripts

The system includes pre-built job scripts:

- **`backup_job.py`** - Database backups using your PostgreSQLBackupManager
- **`cleanup_job.py`** - Container cleanup using your ContainerManager  
- **`health_check_job.py`** - Service health checks using your ServiceLocator
- **`secrets_check_job.py`** - Verify secrets using your SecretsManager
- **`monitoring_job.py`** - Comprehensive monitoring combining multiple checks

## 🔧 Configuration Examples

### **Development Environment**

```yaml
# config/dev_jobs.yml
jobs:
  - name: dev_backup
    schedule: "0 */6 * * *"      # Every 6 hours
    script_path: "jobs/backup_job.py"
    args: ["testlocal", "dev", "maindb"]
    enabled: true

  - name: dev_health_check  
    schedule: "*/5 * * * *"      # Every 5 minutes
    script_path: "jobs/health_check_job.py"
    args: ["testlocal", "dev", "maindb", "cache"]
    enabled: true
```

### **Production Environment**

```yaml
# config/prod_jobs.yml  
jobs:
  - name: prod_backup_primary
    schedule: "0 2 * * *"        # 2 AM daily
    script_path: "jobs/backup_job.py"
    args: ["ecommerce", "prod", "maindb"]
    enabled: true

  - name: prod_backup_analytics
    schedule: "0 3 * * *"        # 3 AM daily
    script_path: "jobs/backup_job.py" 
    args: ["ecommerce", "prod", "analytics"]
    enabled: true

  - name: prod_weekly_cleanup
    schedule: "0 5 * * 0"        # 5 AM Sunday
    script_path: "jobs/cleanup_job.py"
    args: ["ecommerce", "prod", "20"]
    enabled: true

  - name: prod_health_check
    schedule: "*/2 * * * *"      # Every 2 minutes
    script_path: "jobs/health_check_job.py"
    args: ["ecommerce", "prod", "maindb", "cache", "search", "api"]
    enabled: true
```

## 🌐 Multi-Server Deployment

### **Single Server**
```bash
# One scheduler per server
docker run scheduler --config=server1_jobs.yml
```

### **Multiple Servers**
```bash
# Role-based scheduling
DB_SERVER:  scheduler --config=db_jobs.yml
WEB_SERVER: scheduler --config=web_jobs.yml  
OPS_SERVER: scheduler --config=ops_jobs.yml
```

### **Large Scale**
```bash
# Hierarchical scheduling
REGION_US: scheduler --config=us_jobs.yml
REGION_EU: scheduler --config=eu_jobs.yml
GLOBAL:    scheduler --config=global_jobs.yml
```

## 🧪 Testing

### **Run the Demo**

```bash
# Test centralized scheduler
python test.py scheduler

# Test job management
python test.py scheduler-test
```

### **Manual Testing**

```bash
# Add a demo job that runs every minute
docker exec scheduler python scheduler.py add \
  demo_backup "*/1 * * * *" jobs/backup_job.py testlocal dev maindb demo

# Watch it run
docker exec scheduler tail -f /var/log/cron.log
docker exec scheduler tail -f /var/log/jobs/demo_backup.log
```

## 🔄 Migration from Individual Workers

### **Before (Multiple Backup Workers)**
```python
# Old approach - each service has backup worker
services = [
    ("maindb", ServiceTypes.POSTGRES, ServiceConfig()),
    ("maindb_backup", ServiceTypes.WORKER, backup_worker_config),
    ("cache", ServiceTypes.REDIS, ServiceConfig()),  
    ("cache_backup", ServiceTypes.WORKER, backup_worker_config),
]
# = Multiple containers, multiple cron daemons, code duplication
```

### **After (Centralized Scheduler)**
```python
# New approach - one scheduler for all jobs
services = [
    ("maindb", ServiceTypes.POSTGRES, ServiceConfig()),
    ("cache", ServiceTypes.REDIS, ServiceConfig()),
    ("scheduler", ServiceTypes.WORKER, CommonServiceConfigs.centralized_scheduler()),
]
# = One scheduler container with multiple jobs
```

## 💡 Best Practices

### **Job Design**
- ✅ Make jobs idempotent (safe to run multiple times)
- ✅ Include proper error handling and logging
- ✅ Use your existing modules for consistency
- ✅ Keep jobs focused on single responsibilities

### **Scheduling**
- ✅ Stagger job start times to avoid resource conflicts
- ✅ Use appropriate intervals (not too frequent)
- ✅ Consider job dependencies and ordering
- ✅ Test schedules in development first

### **Operations**
- ✅ Monitor job logs regularly
- ✅ Keep job configurations in version control
- ✅ Use descriptive job names and descriptions
- ✅ Document custom jobs and their purposes



