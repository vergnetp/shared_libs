"""
Load Balancer Manager

Manages nginx load balancer configuration with dynamic upstream generation
based on deployed services and health status.
"""

import tempfile
from typing import Dict, List, Any, Optional
from pathlib import Path

from infrastructure_state import InfrastructureState
from ssh_key_manager import SSHKeyManager


class LoadBalancerManager:
    """
    Manages nginx load balancer with dynamic configuration
    """
    
    def __init__(self, infrastructure_state: InfrastructureState, ssh_manager: SSHKeyManager):
        self.state = infrastructure_state
        self.ssh_manager = ssh_manager
        
    def generate_nginx_config(self, include_ssl: bool = True) -> str:
        """Generate complete nginx configuration with dynamic upstreams"""
        
        upstreams = self._generate_upstreams()
        locations = self._generate_locations()
        ssl_config = self._generate_ssl_config() if include_ssl else ""
        
        nginx_config = f"""
# Generated nginx configuration for Personal Cloud Orchestration System
# Generated at: {self._get_timestamp()}

events {{
    worker_connections 1024;
    use epoll;
    multi_accept on;
}}

http {{
    # Basic HTTP configuration
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    
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
    
    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml+rss
        application/atom+xml
        image/svg+xml;
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=web:10m rate=20r/s;
    
    # Upstream configurations
{upstreams}
    
    # Main server configuration
    server {{
        listen 80;
        listen [::]:80;
        server_name _;
        
{ssl_config}
        
        # Security headers
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";
        add_header Referrer-Policy "strict-origin-when-cross-origin";
        
        # Health check endpoint
        location /health {{
            access_log off;
            return 200 "healthy\\n";
            add_header Content-Type text/plain;
        }}
        
        # Load balancer status
        location /lb-status {{
            access_log off;
            return 200 '{self._generate_status_json()}';
            add_header Content-Type application/json;
        }}
        
        # Service locations
{locations}
        
        # Default fallback
        location / {{
            return 404 "Service not found\\n";
            add_header Content-Type text/plain;
        }}
    }}
}}
"""
        
        return nginx_config.strip()
    
    def _generate_upstreams(self) -> str:
        """Generate upstream blocks for all web services"""
        
        upstreams = []
        
        for project, services in self.state.get_all_projects().items():
            if project == 'infrastructure':
                continue  # Skip infrastructure services
            
            for service_type, service_config in services.items():
                # Skip workers and services without ports
                if service_config.get('type') == 'worker' or 'port' not in service_config:
                    continue
                
                upstream_name = f"{project}_{service_type}".replace('-', '_')
                targets = self.state.get_load_balancer_targets(project, service_type)
                
                if targets:
                    upstream_block = f"""
    upstream {upstream_name} {{
        # Health check and load balancing
        least_conn;
        keepalive 32;
        
        # Backend servers"""
                    
                    for target in targets:
                        # Add health check and backup options
                        upstream_block += f"""
        server {target} max_fails=3 fail_timeout=30s;"""
                    
                    upstream_block += f"""
        
        # Health check endpoint
        # Custom health checks can be added here
    }}"""
                    
                    upstreams.append(upstream_block)
        
        return '\n'.join(upstreams)
    
    def _generate_locations(self) -> str:
        """Generate location blocks for all web services"""
        
        locations = []
        
        for project, services in self.state.get_all_projects().items():
            if project == 'infrastructure':
                continue
            
            for service_type, service_config in services.items():
                # Skip workers and services without ports
                if service_config.get('type') == 'worker' or 'port' not in service_config:
                    continue
                
                upstream_name = f"{project}_{service_type}".replace('-', '_')
                
                # Generate URL path: /project/environment/service_type/
                # e.g., /hostomatic/prod/backend/ or /digitalpixo/uat/frontend/
                parts = project.split('-')
                if len(parts) >= 2:
                    project_name = parts[0]
                    environment = parts[1]
                    location_path = f"/{project_name}/{environment}/{service_type}/"
                else:
                    location_path = f"/{project}/{service_type}/"
                
                # Apply rate limiting based on service type
                rate_limit = "limit_req zone=api burst=20 nodelay;" if service_type == "backend" else "limit_req zone=web burst=50 nodelay;"
                
                location_block = f"""
        location {location_path} {{
            # Rate limiting
            {rate_limit}
            
            # Proxy to upstream
            proxy_pass http://{upstream_name}/;
            
            # Proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Host $server_name;
            
            # Proxy settings
            proxy_connect_timeout 30s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
            proxy_buffering on;
            proxy_buffer_size 4k;
            proxy_buffers 8 4k;
            
            # Health check
            proxy_next_upstream error timeout invalid_header http_500 http_502 http_503;
            proxy_next_upstream_tries 3;
            proxy_next_upstream_timeout 10s;
        }}"""
                
                locations.append(location_block)
        
        return '\n'.join(locations)
    
    def _generate_ssl_config(self) -> str:
        """Generate SSL configuration"""
        return """
        # SSL Configuration
        listen 443 ssl http2;
        listen [::]:443 ssl http2;
        
        # SSL certificates (to be configured)
        # ssl_certificate /etc/nginx/ssl/cert.pem;
        # ssl_certificate_key /etc/nginx/ssl/key.pem;
        
        # SSL settings
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;
        
        # HSTS
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;"""
    
    def _generate_status_json(self) -> str:
        """Generate load balancer status JSON"""
        import json
        
        status = {
            "status": "healthy",
            "timestamp": self._get_timestamp(),
            "upstreams": {},
            "total_services": 0
        }
        
        for project, services in self.state.get_all_projects().items():
            if project == 'infrastructure':
                continue
            
            for service_type, service_config in services.items():
                if service_config.get('type') == 'worker' or 'port' not in service_config:
                    continue
                
                upstream_name = f"{project}_{service_type}".replace('-', '_')
                targets = self.state.get_load_balancer_targets(project, service_type)
                
                status["upstreams"][upstream_name] = {
                    "targets": targets,
                    "healthy_targets": len(targets),  # TODO: Add actual health checking
                    "project": project,
                    "service_type": service_type
                }
                
                status["total_services"] += 1
        
        return json.dumps(status, indent=2)
    
    def _get_timestamp(self) -> str:
        """Get current timestamp for generated configs"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    
    def deploy_nginx_config(self) -> Dict[str, Any]:
        """Deploy nginx configuration to master droplet"""
        
        master_droplet = self.state.get_master_droplet()
        if not master_droplet:
            return {
                'success': False,
                'error': 'No master droplet found'
            }
        
        master_ip = master_droplet['ip']
        
        try:
            # Generate nginx configuration
            nginx_config = self.generate_nginx_config()
            
            # Create temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
                f.write(nginx_config)
                temp_config_path = f.name
            
            # Copy config to master droplet
            remote_config_path = "/opt/app/nginx.conf"
            
            if not self.ssh_manager.copy_file_to_server(master_ip, temp_config_path, remote_config_path):
                return {
                    'success': False,
                    'error': 'Failed to copy nginx config to master droplet'
                }
            
            # Test nginx configuration
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                master_ip,
                "nginx -t -c /opt/app/nginx.conf",
                timeout=30
            )
            
            if not success:
                return {
                    'success': False,
                    'error': f'Nginx config test failed: {stderr}'
                }
            
            # Deploy nginx with new configuration
            deployment_result = self._deploy_nginx_container(master_ip)
            
            # Cleanup temp file
            import os
            os.unlink(temp_config_path)
            
            return deployment_result
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to deploy nginx config: {str(e)}'
            }
    
    def _deploy_nginx_container(self, master_ip: str) -> Dict[str, Any]:
        """Deploy or update nginx container on master droplet"""
        
        # Docker compose for nginx
        nginx_compose = """version: '3.8'
services:
  nginx:
    image: nginx:alpine
    container_name: infrastructure_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /opt/app/nginx.conf:/etc/nginx/nginx.conf:ro
      - nginx_logs:/var/log/nginx
    restart: unless-stopped
    networks:
      - infrastructure
    healthcheck:
      test: ["CMD", "nginx", "-t"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  nginx_logs:

networks:
  infrastructure:
    driver: bridge
"""
        
        try:
            # Create temporary compose file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
                f.write(nginx_compose)
                temp_compose_path = f.name
            
            # Copy compose file to master
            remote_compose_path = "/opt/app/nginx-compose.yml"
            
            if not self.ssh_manager.copy_file_to_server(master_ip, temp_compose_path, remote_compose_path):
                return {
                    'success': False,
                    'error': 'Failed to copy nginx compose file'
                }
            
            # Deploy nginx using docker-compose
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                master_ip,
                "cd /opt/app && docker-compose -f nginx-compose.yml up -d",
                timeout=120
            )
            
            # Cleanup temp file
            import os
            os.unlink(temp_compose_path)
            
            if success:
                return {
                    'success': True,
                    'message': 'Nginx deployed successfully',
                    'output': stdout
                }
            else:
                return {
                    'success': False,
                    'error': f'Nginx deployment failed: {stderr}'
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'Error deploying nginx container: {str(e)}'
            }
    
    def update_upstream(self, project: str, service_type: str, 
                       new_targets: List[str] = None) -> Dict[str, Any]:
        """Update upstream configuration for a specific service"""
        
        if new_targets is not None:
            # Update targets in infrastructure state
            # This would typically be called when scaling services
            pass
        
        # Regenerate and deploy nginx config
        return self.deploy_nginx_config()
    
    def remove_upstream(self, project: str, service_type: str) -> Dict[str, Any]:
        """Remove upstream configuration for a service"""
        
        # Remove service from infrastructure state
        self.state.remove_project_service(project, service_type)
        
        # Regenerate and deploy nginx config
        return self.deploy_nginx_config()
    
    def get_load_balancer_status(self) -> Dict[str, Any]:
        """Get current load balancer status"""
        
        master_droplet = self.state.get_master_droplet()
        if not master_droplet:
            return {
                'status': 'error',
                'error': 'No master droplet found'
            }
        
        master_ip = master_droplet['ip']
        
        try:
            # Check nginx status
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                master_ip,
                "docker ps --filter name=infrastructure_nginx --format '{{.Status}}'",
                timeout=10
            )
            
            if success and 'Up' in stdout:
                nginx_status = 'running'
            else:
                nginx_status = 'stopped'
            
            # Get nginx access logs summary
            log_success, log_output, log_error = self.ssh_manager.execute_remote_command(
                master_ip,
                "docker exec infrastructure_nginx tail -n 100 /var/log/nginx/access.log | wc -l",
                timeout=10
            )
            
            recent_requests = int(log_output.strip()) if log_success and log_output.strip().isdigit() else 0
            
            return {
                'status': 'healthy' if nginx_status == 'running' else 'unhealthy',
                'nginx_status': nginx_status,
                'master_ip': master_ip,
                'recent_requests': recent_requests,
                'upstreams': self._get_upstream_summary(),
                'last_config_update': self._get_timestamp()
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def _get_upstream_summary(self) -> Dict[str, Any]:
        """Get summary of all upstream configurations"""
        
        summary = {}
        
        for project, services in self.state.get_all_projects().items():
            if project == 'infrastructure':
                continue
            
            for service_type, service_config in services.items():
                if service_config.get('type') == 'worker' or 'port' not in service_config:
                    continue
                
                upstream_name = f"{project}_{service_type}".replace('-', '_')
                targets = self.state.get_load_balancer_targets(project, service_type)
                
                summary[upstream_name] = {
                    'project': project,
                    'service_type': service_type,
                    'target_count': len(targets),
                    'targets': targets,
                    'load_balanced': len(targets) > 1
                }
        
        return summary
    
    def test_service_connectivity(self, project: str, service_type: str) -> Dict[str, Any]:
        """Test connectivity to a specific service through load balancer"""
        
        master_droplet = self.state.get_master_droplet()
        if not master_droplet:
            return {
                'success': False,
                'error': 'No master droplet found'
            }
        
        master_ip = master_droplet['ip']
        
        # Generate URL path
        parts = project.split('-')
        if len(parts) >= 2:
            project_name = parts[0]
            environment = parts[1]
            test_path = f"/{project_name}/{environment}/{service_type}/health"
        else:
            test_path = f"/{project}/{service_type}/health"
        
        try:
            # Test HTTP connectivity through nginx
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                master_ip,
                f"curl -f -s -m 10 http://localhost{test_path}",
                timeout=15
            )
            
            return {
                'success': success,
                'path': test_path,
                'response': stdout if success else stderr,
                'load_balancer_ip': master_ip
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
