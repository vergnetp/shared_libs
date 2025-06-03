"""
Configuration Management

Handles loading, validation, and creation of all configuration files
and templates for the Personal Cloud Orchestration System.
Updated to remove CSV dependency and use JSON as source of truth.
"""

import json
from pathlib import Path
from typing import Dict, Any, List


class ConfigManager:
    """
    Manages all configuration files and templates
    """
    
    def __init__(self, config_dir: str = "config", templates_dir: str = "templates"):
        # Go up one level from setup/ directory to create configs in the right place
        base_dir = Path(__file__).parent.parent
        self.config_dir = base_dir / config_dir
        self.templates_dir = base_dir / templates_dir
        
        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def _create_deployment_config(self) -> Dict[str, Any]:
        """Create deployment_config.json with worker support"""
        
        config_file = self.config_dir / "deployment_config.json"
        
        if config_file.exists():
            return {'status': 'exists', 'file': str(config_file)}
        
        deployment_config = {
            "deployment_platform": "docker",
            "auto_commit_before_deploy": True,
            "git_config": {
                "base_url": "https://github.com/yourorg",
                "url_pattern": "{base_url}/{project}.git",
                "default_branch": "main"
            },
            "global_services": {
                "nginx": {
                    "type": "global_infrastructure",
                    "ports": [80, 443],
                    "config_template": "templates/nginx.conf"
                }
            },
            "project_services": {
                "opensearch": {
                    "image": "opensearchproject/opensearch:2.8.0",
                    "type": "project_service",
                    "environment": {
                        "discovery.type": "single-node",
                        "OPENSEARCH_JAVA_OPTS": "-Xms512m -Xmx512m"
                    },
                    "secrets": ["opensearch_admin_password"]
                },
                "vault": {
                    "image": "vault:1.13.3",
                    "type": "project_service",
                    "config_template": "templates/vault-config.hcl",
                    "secrets": ["vault_root_token", "vault_unseal_key"]
                }
            },
            "projects": {
                "hostomatic": {
                    # No git_repo needed - derived from git_config.url_pattern
                    "includes_services": ["opensearch", "vault"],
                    "versioning": {
                        "auto_tag_uat": True,
                        "tag_format": "v{version}-uat-{timestamp}",
                        "prod_uses_uat_tags": True
                    },
                    "services": {
                        "backend": {
                            "containerfile_path": "backend/Dockerfile",
                            "build_context": "backend/",
                            "secrets": ["db_password", "redis_password", "stripe_key", "openai_api_key", "jwt_secret"]
                        },
                        "frontend": {
                            "containerfile_path": "frontend/Dockerfile",
                            "build_context": "frontend/",
                            "secrets": ["stripe_publishable_key", "google_oauth_client_id"]
                        },
                        "worker_email": {
                            "type": "worker",
                            "containerfile_path": "workers/Dockerfile",
                            "build_context": "workers/",
                            "command": "python email_processor.py",
                            "secrets": ["db_password", "redis_password", "sendgrid_api_key"]
                        },
                        "scheduler": {
                            "type": "worker",
                            "containerfile_path": "scheduler/Dockerfile",
                            "build_context": "scheduler/",
                            "command": "python cron_scheduler.py",
                            "secrets": ["db_password", "redis_password"]
                        }
                    }
                },
                "digitalpixo": {
                    # No git_repo needed - derived from git_config.url_pattern
                    "includes_services": ["opensearch", "vault"],
                    "services": {
                        "backend": {
                            "containerfile_path": "Dockerfile",
                            "build_context": "./",
                            "secrets": ["db_password", "openai_api_key", "sendgrid_api_key"]
                        },
                        "frontend": {
                            "containerfile_path": "frontend/Dockerfile",
                            "build_context": "frontend/",
                            "secrets": ["stripe_publishable_key"]
                        },
                        "worker_image_processing": {
                            "type": "worker",
                            "containerfile_path": "workers/Dockerfile",
                            "build_context": "workers/",
                            "command": "python image_processor.py",
                            "secrets": ["db_password", "openai_api_key"]
                        }
                    }
                }
            }
        }
        
        with open(config_file, 'w') as f:
            json.dump(deployment_config, f, indent=2)
        
        return {'status': 'created', 'file': str(config_file)}
        
    def initialize_all_configs(self) -> Dict[str, Any]:
        """Initialize all configuration files with defaults"""
        
        results = {
            'infrastructure_json': self._create_infrastructure_json(),
            'deployment_config': self._create_deployment_config(),
            'email_config': self._create_email_config(),
            'sms_config': self._create_sms_config(),
            'templates': self._create_all_templates()
        }
        
        return results
    
    def _create_infrastructure_json(self) -> Dict[str, Any]:
        """Create example infrastructure.json"""
        
        json_file = self.config_dir / "infrastructure.json"
        
        if json_file.exists():
            return {'status': 'exists', 'file': str(json_file)}
        
        # Create example infrastructure configuration
        infrastructure_data = {
  "droplets": {
    "master": {"ip": "192.168.1.10", "role": "master", "size": "s-2vcpu-4gb", "region": "lon1"},
    "web1": {"ip": "192.168.1.11", "role": "web", "size": "s-1vcpu-1gb", "region": "lon1"},
    "web2": {"ip": "192.168.1.12", "role": "web", "size": "s-1vcpu-1gb", "region": "lon1"}
  },
  "projects": {
    "hostomatic": {
      "prod": {
        "backend": {"port": 8001, "assigned_droplets": ["web1", "web2"]},
        "frontend": {"port": 9001, "assigned_droplets": ["web1", "web2"]},
        "worker_cleaner": {"assigned_droplets": ["master"]}
      },
      "uat": {
        "backend": {"port": 8002, "assigned_droplets": ["web1", "web2"]},
        "frontend": {"port": 9002, "assigned_droplets": ["web1", "web2"]},
        "worker_cleaner": {"assigned_droplets": ["web1"]}
      },
      "test": {
        "backend": {"port": 8003, "assigned_droplets": ["web1"]},
        "frontend": {"port": 9003, "assigned_droplets": ["web1"]}     
      }
    }
  }
}
        
        with open(json_file, 'w') as f:
            json.dump(infrastructure_data, f, indent=2)
        
        return {'status': 'created', 'file': str(json_file)}

    def _create_email_config(self) -> Dict[str, Any]:
        """Create email_config.json"""
        
        config_file = self.config_dir / "email_config.json"
        
        if config_file.exists():
            return {'status': 'exists', 'file': str(config_file)}
        
        email_config = {
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
            "use_ssl": True
        }
        
        with open(config_file, 'w') as f:
            json.dump(email_config, f, indent=2)
        
        return {'status': 'created', 'file': str(config_file)}
    
    def _create_sms_config(self) -> Dict[str, Any]:
        """Create sms_config.json"""
        
        config_file = self.config_dir / "sms_config.json"
        
        if config_file.exists():
            return {'status': 'exists', 'file': str(config_file)}
        
        sms_config = {
            "provider": "twilio",
            "account_sid": "TWILIO_ACCOUNT_SID",
            "auth_token": "TWILIO_AUTH_TOKEN",
            "from_number": "+1234567890",
            "recipients": {
                "admin": "+1987654321",
                "emergency": "+1987654321"
            },
            "notification_settings": {
                "critical_failures": True,
                "recovery_failures": True,
                "master_failures": True
            }
        }
        
        with open(config_file, 'w') as f:
            json.dump(sms_config, f, indent=2)
        
        return {'status': 'created', 'file': str(config_file)}
    
    def _create_all_templates(self) -> Dict[str, Any]:
        """Create all deployment templates"""
        
        results = {}
        
        # Docker Compose template
        results['docker_compose'] = self._create_docker_compose_template()
        
        # Kubernetes templates
        results['k8s_deployment'] = self._create_k8s_deployment_template()
        results['k8s_service'] = self._create_k8s_service_template()
        results['k8s_namespace'] = self._create_k8s_namespace_template()
        
        # Nginx templates
        results['nginx_conf'] = self._create_nginx_template()
        
        # Vault template
        results['vault_config'] = self._create_vault_template()
        
        # Email templates
        results['email_templates'] = self._create_email_templates()
        
        # Environment file templates
        results['env_templates'] = self._create_env_templates()
        
        return results
    
    def _create_docker_compose_template(self) -> Dict[str, Any]:
        """Create Docker Compose template with worker support"""
        
        template_file = self.templates_dir / "docker-compose.yml"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Docker Compose Template for Personal Cloud Orchestration System
# Generated dynamically based on service configuration
version: '3.8'

services:
  {{service_name}}:
    image: {{image_name}}
    container_name: {{service_name}}
    {{#if command}}
    command: {{command}}
    {{/if}}
    environment:
      # Infrastructure configuration
      - DB_USER={{DB_USER}}
      - DB_NAME={{DB_NAME}}
      - DB_HOST={{DB_HOST}}
      - DB_PORT={{DB_PORT}}
      - REDIS_HOST={{REDIS_HOST}}
      - REDIS_PORT={{REDIS_PORT}}
      - VAULT_HOST={{VAULT_HOST}}
      - VAULT_PORT={{VAULT_PORT}}
      - OPENSEARCH_HOST={{OPENSEARCH_HOST}}
      - OPENSEARCH_PORT={{OPENSEARCH_PORT}}
      - OPENSEARCH_INDEX={{OPENSEARCH_INDEX}}
      
      # Service configuration
      {{#unless is_worker}}
      - SERVICE_PORT={{SERVICE_PORT}}
      {{/unless}}
      - SERVICE_NAME={{SERVICE_NAME}}
      - ENVIRONMENT={{ENVIRONMENT}}
      - PROJECT={{PROJECT}}
      - RESOURCE_HASH={{RESOURCE_HASH}}
      - ASSIGNED_DROPLETS={{ASSIGNED_DROPLETS}}
    
    secrets:
      {{#each secrets}}
      - {{this}}
      {{/each}}
    
    {{#unless is_worker}}
    ports:
      - "{{SERVICE_PORT}}:{{SERVICE_PORT}}"
    {{/unless}}
    
    restart: unless-stopped
    
    networks:
      - app-network
    
    {{#if volumes}}
    volumes:
      {{#each volumes}}
      - {{this}}
      {{/each}}
    {{/if}}
    
    {{#unless is_worker}}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{{SERVICE_PORT}}/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    {{else}}
    # Worker health check - check if process is running
    healthcheck:
      test: ["CMD", "pgrep", "-f", "{{command}}"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    {{/unless}}

networks:
  app-network:
    driver: bridge

secrets:
  {{#each secrets}}
  {{this}}:
    external: true
  {{/each}}

volumes:
  app-data:
    driver: local
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}
    
    def _create_k8s_deployment_template(self) -> Dict[str, Any]:
        """Create Kubernetes deployment template with worker support"""
        
        template_file = self.templates_dir / "k8s-deployment.yml"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Kubernetes Deployment Template
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{service_name}}
  namespace: {{project}}-{{environment}}
  labels:
    app: {{service_name}}
    project: {{project}}
    environment: {{environment}}
    service-type: {{service_type}}
    worker: "{{is_worker}}"
spec:
  replicas: {{replica_count}}
  selector:
    matchLabels:
      app: {{service_name}}
  template:
    metadata:
      labels:
        app: {{service_name}}
        project: {{project}}
        environment: {{environment}}
        worker: "{{is_worker}}"
    spec:
      containers:
      - name: {{service_name}}
        image: {{image_name}}
        {{#if command}}
        command: ["/bin/sh", "-c"]
        args: ["{{command}}"]
        {{/if}}
        env:
        # Infrastructure configuration
        - name: DB_USER
          value: "{{DB_USER}}"
        - name: DB_NAME
          value: "{{DB_NAME}}"
        - name: DB_HOST
          value: "{{DB_HOST}}"
        - name: DB_PORT
          value: "{{DB_PORT}}"
        - name: REDIS_HOST
          value: "{{REDIS_HOST}}"
        - name: REDIS_PORT
          value: "{{REDIS_PORT}}"
        - name: VAULT_HOST
          value: "{{VAULT_HOST}}"
        - name: VAULT_PORT
          value: "{{VAULT_PORT}}"
        - name: OPENSEARCH_HOST
          value: "{{OPENSEARCH_HOST}}"
        - name: OPENSEARCH_PORT
          value: "{{OPENSEARCH_PORT}}"
        - name: OPENSEARCH_INDEX
          value: "{{OPENSEARCH_INDEX}}"
        
        # Service configuration
        {{#unless is_worker}}
        - name: SERVICE_PORT
          value: "{{SERVICE_PORT}}"
        {{/unless}}
        - name: SERVICE_NAME
          value: "{{SERVICE_NAME}}"
        - name: ENVIRONMENT
          value: "{{ENVIRONMENT}}"
        - name: PROJECT
          value: "{{PROJECT}}"
        
        # Secrets from Kubernetes secrets
        {{#each secrets}}
        - name: {{this}}
          valueFrom:
            secretKeyRef:
              name: {{../project}}-{{../environment}}-secrets
              key: {{this}}
        {{/each}}
        
        {{#unless is_worker}}
        ports:
        - containerPort: {{SERVICE_PORT}}
          name: http
        {{/unless}}
        
        resources:
          requests:
            memory: "{{#if is_worker}}64Mi{{else}}128Mi{{/if}}"
            cpu: "{{#if is_worker}}50m{{else}}100m{{/if}}"
          limits:
            memory: "{{#if is_worker}}256Mi{{else}}512Mi{{/if}}"
            cpu: "{{#if is_worker}}200m{{else}}500m{{/if}}"
        
        {{#unless is_worker}}
        livenessProbe:
          httpGet:
            path: /health
            port: {{SERVICE_PORT}}
          initialDelaySeconds: 30
          periodSeconds: 10
        
        readinessProbe:
          httpGet:
            path: /health
            port: {{SERVICE_PORT}}
          initialDelaySeconds: 5
          periodSeconds: 5
        {{else}}
        # Worker liveness probe - check if process is still running
        livenessProbe:
          exec:
            command:
            - /bin/sh
            - -c
            - "pgrep -f '{{command}}' || exit 1"
          initialDelaySeconds: 30
          periodSeconds: 30
        {{/unless}}
      
      restartPolicy: Always
---
apiVersion: v1
kind: Secret
metadata:
  name: {{project}}-{{environment}}-secrets
  namespace: {{project}}-{{environment}}
type: Opaque
data:
  {{#each secret_values}}
  {{@key}}: {{this}}
  {{/each}}
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}
    
    def _create_k8s_service_template(self) -> Dict[str, Any]:
        """Create Kubernetes service template"""
        
        template_file = self.templates_dir / "k8s-service.yml"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Kubernetes Service Template
{{#unless is_worker}}
apiVersion: v1
kind: Service
metadata:
  name: {{service_name}}-service
  namespace: {{project}}-{{environment}}
  labels:
    app: {{service_name}}
spec:
  selector:
    app: {{service_name}}
  ports:
  - name: http
    port: 80
    targetPort: {{SERVICE_PORT}}
    protocol: TCP
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{service_name}}-ingress
  namespace: {{project}}-{{environment}}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  rules:
  - host: {{project}}-{{environment}}-{{service_type}}.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {{service_name}}-service
            port:
              number: 80
{{/unless}}
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}
    
    def _create_k8s_namespace_template(self) -> Dict[str, Any]:
        """Create Kubernetes namespace template"""
        
        template_file = self.templates_dir / "k8s-namespace.yml"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Kubernetes Namespace Template
apiVersion: v1
kind: Namespace
metadata:
  name: {{project}}-{{environment}}
  labels:
    project: {{project}}
    environment: {{environment}}
    managed-by: personal-cloud-orchestrator
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: {{project}}-{{environment}}-quota
  namespace: {{project}}-{{environment}}
spec:
  hard:
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 8Gi
    pods: "10"
    services: "5"
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}
    
    def _create_nginx_template(self) -> Dict[str, Any]:
        """Create nginx configuration template"""
        
        template_file = self.templates_dir / "nginx.conf"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Nginx Configuration Template
# Generated dynamically by Personal Cloud Orchestration System

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    # Basic configuration
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # Logging
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                   '$status $body_bytes_sent "$http_referer" '
                   '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log warn;
    
    # Performance
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    # Security
    server_tokens off;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=web:10m rate=20r/s;
    
    # This section will be dynamically generated with upstreams
    {{upstreams}}
    
    # Main server
    server {
        listen 80;
        listen [::]:80;
        server_name _;
        
        # Security headers
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        
        # Health check
        location /health {
            access_log off;
            return 200 "healthy\n";
            add_header Content-Type text/plain;
        }
        
        # Load balancer status
        location /lb-status {
            access_log off;
            return 200 '{{status_json}}';
            add_header Content-Type application/json;
        }
        
        # This section will be dynamically generated with locations
        {{locations}}
        
        # Default
        location / {
            return 404 "Service not found\n";
            add_header Content-Type text/plain;
        }
    }
}
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}
    
    def _create_vault_template(self) -> Dict[str, Any]:
        """Create Vault configuration template"""
        
        template_file = self.templates_dir / "vault-config.hcl"
        
        if template_file.exists():
            return {'status': 'exists', 'file': str(template_file)}
        
        template_content = """# Vault Configuration Template
# Project: {{project}}-{{environment}}

storage "file" {
  path = "/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:{{VAULT_PORT}}"
  tls_disable = 1
}

ui = true

# Cluster settings
cluster_name = "{{project}}-{{environment}}-vault"

# API settings
api_addr = "http://{{VAULT_HOST}}:{{VAULT_PORT}}"
cluster_addr = "http://{{VAULT_HOST}}:{{VAULT_PORT}}"

# Logging
log_level = "INFO"
log_format = "json"

# Disable mlock for development
disable_mlock = true

# Enable raw endpoint
raw_storage_endpoint = true

# Default lease settings
default_lease_ttl = "768h"
max_lease_ttl = "8760h"
"""
        
        with open(template_file, 'w') as f:
            f.write(template_content)
        
        return {'status': 'created', 'file': str(template_file)}

    def _create_email_templates(self) -> Dict[str, Any]:
        """Create email notification templates"""
        
        email_templates_dir = self.templates_dir / "email-templates"
        email_templates_dir.mkdir(exist_ok=True)
        
        results = {}
        
        # Heartbeat email template
        heartbeat_file = email_templates_dir / "heartbeat.html"
        if not heartbeat_file.exists():
            heartbeat_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Infrastructure Heartbeat</title>
</head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color: #28a745;">🟢 All Systems Operational</h2>
    
    <div style="background-color: #d4edda; border: 1px solid #c3e6cb; padding: 10px; border-radius: 5px; margin-bottom: 15px;">
        <p><strong>Heartbeat Leader:</strong> {{leader_droplet}}</p>
        <p><strong>Leadership Method:</strong> Deterministic (Lowest IP)</p>
        <p><strong>Available Peers:</strong> {{peer_count}}</p>
    </div>
    
    <table style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">
        <tr style="background-color: #f8f9fa;">
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Master Droplet:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{master_status}}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Web Droplets:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{web_count}} healthy</td>
        </tr>
        <tr style="background-color: #f8f9fa;">
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Services:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{total_services}} running</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Backend Services:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{backend_services}} running</td>
        </tr>
        <tr style="background-color: #f8f9fa;">
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Frontend Services:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{frontend_services}} running</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Worker Services:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{worker_services}} running</td>
        </tr>
        <tr style="background-color: #f8f9fa;">
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Last Check:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{{timestamp}}</td>
        </tr>
    </table>
    
    <p style="color: #6c757d; font-size: 14px; margin-top: 15px;">
        No action needed. Leadership rotates automatically based on server availability.
        Next heartbeat leader will be determined by lowest IP among healthy servers.
    </p>
</body>
</html>"""
            
            with open(heartbeat_file, 'w') as f:
                f.write(heartbeat_content)
            
            results['heartbeat'] = {'status': 'created', 'file': str(heartbeat_file)}

    def _create_env_templates(self) -> Dict[str, Any]:
        """Create environment file templates"""
        
        results = {}
        
        # Backend environment template
        backend_env_file = self.templates_dir / "backend.env"
        if not backend_env_file.exists():
            backend_env_content = """# Backend Service Environment Template
# These variables are dynamically generated by the orchestrator

# Database Configuration
DB_HOST={{DB_HOST}}
DB_PORT={{DB_PORT}}
DB_NAME={{DB_NAME}}
DB_USER={{DB_USER}}
# DB_PASSWORD is provided via Docker secrets

# Redis Configuration  
REDIS_HOST={{REDIS_HOST}}
REDIS_PORT={{REDIS_PORT}}
# REDIS_PASSWORD is provided via Docker secrets

# Service Configuration
SERVICE_PORT={{SERVICE_PORT}}
SERVICE_NAME={{SERVICE_NAME}}
ENVIRONMENT={{ENVIRONMENT}}
PROJECT={{PROJECT}}

# Infrastructure Services
VAULT_HOST={{VAULT_HOST}}
VAULT_PORT={{VAULT_PORT}}
OPENSEARCH_HOST={{OPENSEARCH_HOST}}
OPENSEARCH_PORT={{OPENSEARCH_PORT}}
OPENSEARCH_INDEX={{OPENSEARCH_INDEX}}

# Application Settings
DEBUG=false
LOG_LEVEL=info
API_VERSION=v1

# External Service Configuration (secrets via Docker secrets)
# STRIPE_KEY, OPENAI_API_KEY, JWT_SECRET, etc.
"""
            
            with open(backend_env_file, 'w') as f:
                f.write(backend_env_content)
            
            results['backend'] = {'status': 'created', 'file': str(backend_env_file)}
        
        # Worker environment template
        worker_env_file = self.templates_dir / "worker.env"
        if not worker_env_file.exists():
            worker_env_content = """# Worker Service Environment Template
# These variables are dynamically generated by the orchestrator

# Database Configuration
DB_HOST={{DB_HOST}}
DB_PORT={{DB_PORT}}
DB_NAME={{DB_NAME}}
DB_USER={{DB_USER}}
# DB_PASSWORD is provided via Docker secrets

# Redis Configuration (for job queues)
REDIS_HOST={{REDIS_HOST}}
REDIS_PORT={{REDIS_PORT}}
# REDIS_PASSWORD is provided via Docker secrets

# Service Configuration
SERVICE_NAME={{SERVICE_NAME}}
ENVIRONMENT={{ENVIRONMENT}}
PROJECT={{PROJECT}}
WORKER_TYPE={{service_type}}

# Infrastructure Services
VAULT_HOST={{VAULT_HOST}}
VAULT_PORT={{VAULT_PORT}}
OPENSEARCH_HOST={{OPENSEARCH_HOST}}
OPENSEARCH_PORT={{OPENSEARCH_PORT}}
OPENSEARCH_INDEX={{OPENSEARCH_INDEX}}

# Worker Settings
WORKER_CONCURRENCY=1
WORKER_LOGLEVEL=info
WORKER_PREFETCH_MULTIPLIER=4

# Queue Settings
QUEUE_NAME={{service_type}}_queue
QUEUE_ROUTING_KEY={{service_type}}

# External Service Configuration (secrets via Docker secrets)
# SENDGRID_API_KEY, OPENAI_API_KEY, etc.
"""
            
            with open(worker_env_file, 'w') as f:
                f.write(worker_env_content)
            
            results['worker'] = {'status': 'created', 'file': str(worker_env_file)}
        
        # Frontend environment template
        frontend_env_file = self.templates_dir / "frontend.env"
        if not frontend_env_file.exists():
            frontend_env_content = """# Frontend Service Environment Template
# These variables are dynamically generated by the orchestrator

# Service Configuration
SERVICE_PORT={{SERVICE_PORT}}
SERVICE_NAME={{SERVICE_NAME}}
ENVIRONMENT={{ENVIRONMENT}}
PROJECT={{PROJECT}}

# API Configuration (points to backend service)
API_URL=http://{{backend_host}}:{{backend_port}}
API_VERSION=v1

# Build Configuration
NODE_ENV={{ENVIRONMENT}}
PUBLIC_URL=/

# External Services (public keys via Docker secrets)
# STRIPE_PUBLISHABLE_KEY, GOOGLE_OAUTH_CLIENT_ID, etc.
"""
            
            with open(frontend_env_file, 'w') as f:
                f.write(frontend_env_content)
            
            results['frontend'] = {'status': 'created', 'file': str(frontend_env_file)}
        
        return results
    
    def validate_all_configs(self) -> Dict[str, Any]:
        """Validate all configuration files"""
        
        results = {
            'valid': True,
            'issues': [],
            'warnings': []
        }
        
        # Check infrastructure JSON
        try:
            json_file = self.config_dir / "infrastructure.json"
            if json_file.exists():
                with open(json_file, 'r') as f:
                    config = json.load(f)
                    
                    # Check required sections
                    required_sections = ['infrastructure_spec']
                    for section in required_sections:
                        if section not in config:
                            results['issues'].append(f"Missing {section} section in infrastructure.json")
                            results['valid'] = False
                    
                    # Check infrastructure spec structure
                    if 'infrastructure_spec' in config:
                        spec = config['infrastructure_spec']
                        
                        if 'projects' not in spec:
                            results['issues'].append("No projects defined in infrastructure_spec")
                            results['valid'] = False
                        else:
                            # Check each project has required fields
                            for project, project_config in spec['projects'].items():
                                required_fields = ['environments', 'web_droplets', 'web_droplet_spec']
                                for field in required_fields:
                                    if field not in project_config:
                                        results['issues'].append(f"Project {project} missing {field}")
                                        results['valid'] = False
            else:
                results['issues'].append("infrastructure.json not found")
                results['valid'] = False
        except Exception as e:
            results['issues'].append(f"Error validating infrastructure.json: {str(e)}")
            results['valid'] = False
        
        # Check deployment config
        try:
            config_file = self.config_dir / "deployment_config.json"
            if config_file.exists():
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    
                    if 'projects' not in config:
                        results['issues'].append("No projects section in deployment_config.json")
                        results['valid'] = False
                    
                    if 'deployment_platform' not in config:
                        results['warnings'].append("No deployment_platform specified, defaulting to docker")
                    
                    # Check for worker services in projects
                    for project, project_config in config.get('projects', {}).items():
                        services = project_config.get('services', {})
                        worker_services = [s for s, c in services.items() if c.get('type') == 'worker']
                        if worker_services:
                            print(f"Found worker services in {project}: {worker_services}")
            else:
                results['issues'].append("deployment_config.json not found")
                results['valid'] = False
        except Exception as e:
            results['issues'].append(f"Error validating deployment_config.json: {str(e)}")
            results['valid'] = False
        
        # Check templates exist
        required_templates = [
            "docker-compose.yml",
            "k8s-deployment.yml", 
            "nginx.conf",
            "vault-config.hcl"
        ]
        
        for template in required_templates:
            template_file = self.templates_dir / template
            if not template_file.exists():
                results['warnings'].append(f"Template {template} not found")
        
        return results
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Get summary of all configuration files"""
        
        config_files = {
            'infrastructure_json': self.config_dir / "infrastructure.json",
            'deployment_config': self.config_dir / "deployment_config.json",
            'email_config': self.config_dir / "email_config.json",
            'sms_config': self.config_dir / "sms_config.json"
        }
        
        template_files = {
            'docker_compose': self.templates_dir / "docker-compose.yml",
            'k8s_deployment': self.templates_dir / "k8s-deployment.yml",
            'nginx_conf': self.templates_dir / "nginx.conf",
            'vault_config': self.templates_dir / "vault-config.hcl",
            'worker_env': self.templates_dir / "worker.env"
        }
        
        summary = {
            'config_files': {},
            'template_files': {},
            'directories': {
                'config_dir': str(self.config_dir),
                'templates_dir': str(self.templates_dir)
            }
        }
        
        # Check config files
        for name, path in config_files.items():
            summary['config_files'][name] = {
                'exists': path.exists(),
                'path': str(path),
                'size': path.stat().st_size if path.exists() else 0
            }
        
        # Check template files
        for name, path in template_files.items():
            summary['template_files'][name] = {
                'exists': path.exists(),
                'path': str(path),
                'size': path.stat().st_size if path.exists() else 0
            }
        
        return summary