"""
Health Agent - Complete HTTP API for SSH-Free Deployments

COMPLETE IMPLEMENTATION - All 30 endpoints for SSH-free deployment operations.

This file should be copied to: /usr/local/bin/health_agent.py on each server

Version: 2.0 - SSH-Free Deployments
- 19 existing endpoints (containers, files, nginx, credentials, cron)
- 11 NEW endpoints (chmod, upload, service control x4, firewall x5)
"""

from flask import Flask, request, jsonify
from functools import wraps
from pathlib import Path
import subprocess
import json
import os
import shlex
import base64
import tarfile
import io
import shutil


app = Flask(__name__)

# Security: Allowed paths for file write operations
ALLOWED_WRITE_PATHS = [
    '/local/',           # User data directories
    '/app/local/',       # Container-mounted user data
    '/etc/nginx/',       # Nginx configurations
    '/tmp/',             # Temporary files
]

# Security: Whitelisted services for control operations
ALLOWED_SERVICES_RESTART = ['nginx', 'docker']
ALLOWED_SERVICES_START_STOP = ['nginx', 'docker', 'health-agent']
ALLOWED_SERVICES_STATUS = ['nginx', 'docker', 'health-agent', 'ufw']


# ========================================
# UTILITY FUNCTIONS
# ========================================

def run_docker_cmd(cmd_list):
    """Run docker command and return output"""
    result = subprocess.run(
        cmd_list,
        capture_output=True,
        text=True,
        check=False
    )
    if result.returncode != 0:
        raise Exception(f"Docker command failed: {result.stderr}")
    return result.stdout.strip()


def run_shell_cmd(cmd_str, timeout=60):
    """Run shell command and return CompletedProcess"""
    result = subprocess.run(
        cmd_str,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout
    )
    return result


# Auth decorator
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        expected = Path('/etc/health-agent/api-key').read_text().strip()
        if api_key != expected:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


# ========================================
# HEALTH & UTILITY ENDPOINTS
# ========================================

@app.route('/ping', methods=['GET'])
@require_api_key
def ping():
    """Simple ping to check if agent is alive"""
    return jsonify({'status': 'alive'})


@app.route('/health', methods=['GET'])
@require_api_key
def health_check():
    """Comprehensive health check"""
    try:
        # Check if Docker is running
        run_docker_cmd(['docker', 'info'])
        
        # Get container list
        output = run_docker_cmd(['docker', 'ps', '--format', '{{.Names}}'])
        containers = output.split('\n') if output else []
        
        return jsonify({
            'docker_running': True,
            'containers': containers
        })
    except Exception as e:
        return jsonify({
            'docker_running': False,
            'error': str(e)
        }), 500


@app.route('/docker/health', methods=['GET'])
@require_api_key
def docker_health():
    """Check Docker daemon health"""
    try:
        run_docker_cmd(['docker', 'info'])
        return jsonify({'status': 'healthy'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


# ========================================
# CONTAINER OPERATIONS
# ========================================

@app.route('/containers', methods=['GET'])
@require_api_key
def list_containers():
    """List all containers"""
    try:
        # Use JSON format for easy parsing
        output = run_docker_cmd([
            'docker', 'ps', '-a',
            '--format', '{{json .}}'
        ])
        
        containers = []
        if output:
            for line in output.split('\n'):
                if line.strip():
                    containers.append(json.loads(line))
        
        return jsonify({'containers': containers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/run', methods=['POST'])
@require_api_key
def run_container():
    """Start a Docker container"""
    try:
        data = request.get_json()
        
        # Build docker run command
        cmd = ['docker', 'run', '-d']
        
        if data.get('name'):
            cmd.extend(['--name', data['name']])
        
        if data.get('network'):
            cmd.extend(['--network', data['network']])
        
        if data.get('restart_policy'):
            cmd.extend(['--restart', data['restart_policy']])
        
        # Ports
        if data.get('ports'):
            for host_port, container_port in data['ports'].items():
                cmd.extend(['-p', f"{host_port}:{container_port}"])
        
        # Volumes
        if data.get('volumes'):
            for volume in data['volumes']:
                cmd.extend(['-v', volume])
        
        # Environment variables
        if data.get('env_vars'):
            for key, value in data['env_vars'].items():
                cmd.extend(['-e', f"{key}={value}"])
        
        # Image
        cmd.append(data['image'])
        
        # Command (optional)
        if data.get('command'):
            cmd.extend(data['command'])
        
        # Run container
        container_id = run_docker_cmd(cmd)
        
        return jsonify({
            'status': 'started',
            'container_id': container_id
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/containers/<name>/stop', methods=['POST'])
@require_api_key
def stop_container(name):
    """Stop a container"""
    try:
        run_docker_cmd(['docker', 'stop', name])
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<name>', methods=['DELETE'])
@require_api_key
def remove_container(name):
    """Remove a container"""
    try:
        run_docker_cmd(['docker', 'rm', '-f', name])
        return jsonify({'status': 'removed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<name>/logs', methods=['GET'])
@require_api_key
def get_container_logs(name):
    """Get container logs"""
    try:
        lines = request.args.get('lines', '100')
        output = run_docker_cmd(['docker', 'logs', '--tail', lines, name])
        return jsonify({'logs': output})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========================================
# DOCKER PS OPERATION (NEW - REQUIRED BY INTERCEPTOR)
# ========================================

@app.route('/docker/ps', methods=['GET'])
@require_api_key
def docker_ps():
    """
    Run docker ps with customizable filters and format.
    Supports the patterns used by execute_cmd.py interceptor.
    """
    try:
        # Parse query parameters
        all_containers = request.args.get('all', 'false').lower() == 'true'
        filter_name = request.args.get('filter_name', '')
        format_str = request.args.get('format', '')
        
        # Build docker ps command
        cmd = ['docker', 'ps']
        
        if all_containers:
            cmd.append('-a')
        
        if filter_name:
            cmd.extend(['--filter', f'name={filter_name}'])
        
        if format_str:
            cmd.extend(['--format', format_str])
        
        # Execute command
        output = run_docker_cmd(cmd)
        
        return jsonify({
            'output': output,
            'status': 'success'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# DOCKER INSPECT OPERATION (NEW - REQUIRED BY INTERCEPTOR)
# ========================================

@app.route('/containers/<name>/inspect', methods=['GET'])
@require_api_key
def inspect_container(name):
    """
    Inspect container and optionally apply format string.
    Supports the patterns used by execute_cmd.py interceptor.
    """
    try:
        format_str = request.args.get('format', '')
        
        # Build docker inspect command
        cmd = ['docker', 'inspect', name]
        
        if format_str:
            cmd.extend(['--format', format_str])
        
        # Execute command
        output = run_docker_cmd(cmd)
        
        return jsonify({
            'output': output,
            'status': 'success'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# DOCKER PORT OPERATION (NEW - REQUIRED BY INTERCEPTOR)
# ========================================

@app.route('/containers/<name>/port', methods=['GET'])
@require_api_key
def get_container_ports(name):
    """
    Get port mappings for a container.
    Returns dict of container_port -> host_port mappings.
    """
    try:
        # Run docker port command
        output = run_docker_cmd(['docker', 'port', name])
        
        # Parse output like: "5432/tcp -> 0.0.0.0:8357"
        port_map = {}
        for line in output.split('\n'):
            if '->' in line:
                container_port, host_binding = line.split('->')
                container_port = container_port.strip()
                host_port = host_binding.strip().split(':')[-1]
                port_map[container_port] = host_port
        
        return jsonify({
            'ports': port_map,
            'status': 'success'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# DOCKER START OPERATION (NEW - REQUIRED BY INTERCEPTOR)
# ========================================

@app.route('/containers/<name>/start', methods=['POST'])
@require_api_key
def start_container(name):
    """Start a stopped container"""
    try:
        run_docker_cmd(['docker', 'start', name])
        return jsonify({
            'status': 'started',
            'container': name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# DOCKER REMOVE OPERATION (ALIAS - REQUIRED BY INTERCEPTOR)
# ========================================

@app.route('/containers/<name>/remove', methods=['DELETE'])
@require_api_key
def remove_container_alias(name):
    """
    Remove container (alias endpoint for interceptor compatibility).
    The existing DELETE /containers/<name> endpoint serves the same purpose,
    but interceptor expects /containers/<name>/remove path.
    """
    try:
        run_docker_cmd(['docker', 'rm', '-f', name])
        return jsonify({
            'status': 'removed',
            'container': name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# ========================================
# IMAGE OPERATIONS
# ========================================

@app.route('/images/<path:image>/pull', methods=['POST'])
@require_api_key
def pull_image(image):
    """Pull Docker image"""
    try:
        run_docker_cmd(['docker', 'pull', image])
        return jsonify({'status': 'pulled'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# FILE OPERATIONS
# ========================================

@app.route('/files/write', methods=['POST'])
@require_api_key
def write_file():
    """Write file to server (existing endpoint)"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('path') or 'content' not in data:
            return jsonify({'error': 'path and content required'}), 400
        
        file_path = Path(data['path'])
        content = data['content']
        permissions = data.get('permissions')
        
        # Security check: Only allow writes to whitelisted paths
        path_str = str(file_path.resolve())
        
        if not any(path_str.startswith(base) for base in ALLOWED_WRITE_PATHS):
            return jsonify({
                'error': f'Path not allowed: {data["path"]}. '
                        f'Must be under: {", ".join(ALLOWED_WRITE_PATHS)}'
            }), 403
        
        # Create parent directory if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        file_path.write_text(content)
        
        # Set permissions if specified
        if permissions:
            try:
                perm_int = int(permissions, 8)
                file_path.chmod(perm_int)
            except ValueError:
                return jsonify({'error': 'Invalid permissions format (use octal like "644")'}), 400
        
        return jsonify({
            'status': 'success',
            'path': str(file_path)
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/files/mkdir', methods=['POST'])
@require_api_key
def make_directories():
    """Create directories (existing endpoint)"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('paths'):
            return jsonify({'error': 'paths required'}), 400
        
        paths = data['paths']
        mode = data.get('mode', '755')
        
        if not isinstance(paths, list):
            paths = [paths]
        
        created = []
        
        for path_str in paths:
            dir_path = Path(path_str)
            
            # Security check: Only allow creation under whitelisted paths
            resolved = str(dir_path.resolve())
            if not any(resolved.startswith(base) for base in ALLOWED_WRITE_PATHS):
                return jsonify({
                    'error': f'Path not allowed: {path_str}. '
                            f'Must be under: {", ".join(ALLOWED_WRITE_PATHS)}'
                }), 403
            
            # Create directory (mkdir -p behavior)
            dir_path.mkdir(parents=True, exist_ok=True)
            
            # Set permissions
            try:
                mode_int = int(mode, 8)
                dir_path.chmod(mode_int)
            except ValueError:
                return jsonify({'error': 'Invalid mode format (use octal like "755")'}), 400
            
            created.append(str(dir_path))
        
        return jsonify({
            'status': 'success',
            'created': created
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/files/chmod', methods=['POST'])
@require_api_key
def change_permissions():
    """
    NEW: Change file/directory permissions
    
    Supports both simple and recursive chmod operations.
    """
    try:
        data = request.get_json()
        
        if not data.get('paths'):
            return jsonify({'error': 'paths required'}), 400
        
        paths = data['paths'] if isinstance(data['paths'], list) else [data['paths']]
        recursive = data.get('recursive', False)
        
        updated = []
        
        for path_str in paths:
            path = Path(path_str)
            
            # Security check
            resolved = str(path.resolve())
            if not any(resolved.startswith(base) for base in ALLOWED_WRITE_PATHS):
                return jsonify({'error': f'Path not allowed: {path_str}'}), 403
            
            if not path.exists():
                return jsonify({'error': f'Path not found: {path_str}'}), 404
            
            if recursive and path.is_dir():
                # Recursive chmod
                dir_mode = int(data.get('dir_mode', '755'), 8)
                file_mode = int(data.get('file_mode', '644'), 8)
                
                # Change directory permissions
                for root, dirs, files in os.walk(path):
                    for d in dirs:
                        dir_path = Path(root) / d
                        dir_path.chmod(dir_mode)
                        updated.append(str(dir_path))
                    
                    for f in files:
                        file_path = Path(root) / f
                        file_path.chmod(file_mode)
                        updated.append(str(file_path))
                
                # Change root directory too
                path.chmod(dir_mode)
                updated.append(str(path))
            else:
                # Single file/directory
                mode = int(data.get('mode', '644'), 8)
                path.chmod(mode)
                updated.append(str(path))
        
        return jsonify({
            'status': 'success',
            'paths_updated': updated
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/files/upload', methods=['POST'])
@require_api_key
def upload_tar():
    """
    NEW: Upload and extract tar.gz archive
    
    Handles large file uploads via base64-encoded tar data.
    Supports permission setting after extraction.
    """
    try:
        data = request.get_json()
        
        if not data.get('tar_data') or not data.get('extract_path'):
            return jsonify({'error': 'tar_data and extract_path required'}), 400
        
        extract_path = Path(data['extract_path'])
        
        # Security check - only allow extraction to safe paths
        resolved = str(extract_path.resolve())
        allowed_extract_paths = ['/local/', '/app/local/', '/tmp/']
        if not any(resolved.startswith(base) for base in allowed_extract_paths):
            return jsonify({
                'error': f'Extract path not allowed: {data["extract_path"]}. '
                        f'Must be under: {", ".join(allowed_extract_paths)}'
            }), 403
        
        # Decode base64 tar data
        tar_data = base64.b64decode(data['tar_data'])
        
        # Extract tar
        files_extracted = 0
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode='r:gz') as tar:
            tar.extractall(extract_path)
            files_extracted = len(tar.getmembers())
        
        # Set permissions if requested
        if data.get('set_permissions'):
            dir_mode = int(data.get('dir_mode', '755'), 8)
            file_mode = int(data.get('file_mode', '644'), 8)
            
            for root, dirs, files in os.walk(extract_path):
                for d in dirs:
                    (Path(root) / d).chmod(dir_mode)
                for f in files:
                    (Path(root) / f).chmod(file_mode)
        
        return jsonify({
            'status': 'success',
            'extract_path': str(extract_path),
            'files_extracted': files_extracted
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# SERVICE CONTROL (SYSTEMD)
# ========================================

@app.route('/system/service/<service_name>/status', methods=['GET'])
@require_api_key
def get_service_status(service_name):
    """Get systemd service status (existing endpoint)"""
    try:
        # Whitelist allowed services
        if service_name not in ALLOWED_SERVICES_STATUS:
            return jsonify({'error': f'Service {service_name} not allowed'}), 403
        
        result = run_shell_cmd(f'systemctl is-active {service_name}', timeout=5)
        
        return jsonify({
            'service': service_name,
            'status': result.stdout.strip(),
            'active': result.returncode == 0
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/service/<service_name>/restart', methods=['POST'])
@require_api_key
def restart_service(service_name):
    """Restart systemd service (existing endpoint)"""
    try:
        # Whitelist allowed services
        if service_name not in ALLOWED_SERVICES_RESTART:
            return jsonify({'error': f'Service {service_name} not allowed'}), 403
        
        result = run_shell_cmd(f'systemctl restart {service_name}', timeout=30)
        
        return jsonify({
            'status': 'restarted' if result.returncode == 0 else 'failed',
            'service': service_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/service/<service_name>/start', methods=['POST'])
@require_api_key
def start_service(service_name):
    """NEW: Start systemd service"""
    try:
        if service_name not in ALLOWED_SERVICES_START_STOP:
            return jsonify({'error': f'Service {service_name} not allowed'}), 403
        
        result = run_shell_cmd(f'systemctl start {service_name}', timeout=30)
        
        return jsonify({
            'status': 'started' if result.returncode == 0 else 'failed',
            'service': service_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/service/<service_name>/stop', methods=['POST'])
@require_api_key
def stop_service(service_name):
    """NEW: Stop systemd service"""
    try:
        if service_name not in ALLOWED_SERVICES_START_STOP:
            return jsonify({'error': f'Service {service_name} not allowed'}), 403
        
        result = run_shell_cmd(f'systemctl stop {service_name}', timeout=30)
        
        return jsonify({
            'status': 'stopped' if result.returncode == 0 else 'failed',
            'service': service_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/service/<service_name>/enable', methods=['POST'])
@require_api_key
def enable_service(service_name):
    """NEW: Enable systemd service (start on boot)"""
    try:
        if service_name not in ALLOWED_SERVICES_START_STOP:
            return jsonify({'error': f'Service {service_name} not allowed'}), 403
        
        result = run_shell_cmd(f'systemctl enable {service_name}', timeout=30)
        
        return jsonify({
            'status': 'enabled' if result.returncode == 0 else 'failed',
            'service': service_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/service/daemon-reload', methods=['POST'])
@require_api_key
def daemon_reload():
    """NEW: Reload systemd daemon configuration"""
    try:
        result = run_shell_cmd('systemctl daemon-reload', timeout=30)
        
        return jsonify({
            'status': 'reloaded' if result.returncode == 0 else 'failed'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# FIREWALL MANAGEMENT (UFW)
# ========================================

@app.route('/system/firewall/reset', methods=['POST'])
@require_api_key
def reset_firewall():
    """NEW: Reset UFW firewall to defaults"""
    try:
        data = request.get_json()
        
        # Safety check - require explicit confirmation
        if not data or not data.get('confirm'):
            return jsonify({'error': 'Must confirm firewall reset with "confirm": true'}), 400
        
        result = run_shell_cmd('ufw --force reset', timeout=30)
        
        return jsonify({
            'status': 'reset' if result.returncode == 0 else 'failed'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/firewall/defaults', methods=['POST'])
@require_api_key
def set_firewall_defaults():
    """NEW: Set UFW default policies"""
    try:
        data = request.get_json()
        
        incoming = data.get('incoming', 'deny')
        outgoing = data.get('outgoing', 'allow')
        routed = data.get('routed', 'deny')
        
        # Validate values
        valid_policies = ['allow', 'deny', 'reject']
        if incoming not in valid_policies or outgoing not in valid_policies or routed not in valid_policies:
            return jsonify({'error': 'Invalid policy value. Must be: allow, deny, or reject'}), 400
        
        # Set defaults
        commands = [
            f'ufw default {incoming} incoming',
            f'ufw default {outgoing} outgoing',
            f'ufw default {routed} routed'
        ]
        
        for cmd in commands:
            result = run_shell_cmd(cmd, timeout=10)
            if result.returncode != 0:
                return jsonify({'error': f'Command failed: {cmd}', 'stderr': result.stderr}), 500
        
        return jsonify({
            'status': 'success',
            'policies': {
                'incoming': incoming,
                'outgoing': outgoing,
                'routed': routed
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/firewall/rules/bulk', methods=['POST'])
@require_api_key
def add_firewall_rules_bulk():
    """NEW: Add multiple UFW rules at once"""
    try:
        data = request.get_json()
        
        if not data.get('rules'):
            return jsonify({'error': 'rules required'}), 400
        
        rules = data['rules']
        results = []
        
        for rule in rules:
            port = rule.get('port')
            protocol = rule.get('protocol', 'tcp')
            sources = rule.get('sources', [])
            comment = rule.get('comment', '')
            
            if not port:
                results.append({'error': 'port required', 'status': 'skipped'})
                continue
            
            # Validate protocol
            if protocol not in ['tcp', 'udp']:
                results.append({
                    'port': port,
                    'protocol': protocol,
                    'error': 'Invalid protocol (must be tcp or udp)',
                    'status': 'skipped'
                })
                continue
            
            # Add rule for each source
            for source in sources:
                cmd = f"ufw allow from {source} to any port {port} proto {protocol}"
                if comment:
                    cmd += f" comment '{comment}'"
                
                result = run_shell_cmd(cmd, timeout=10)
                
                if result.returncode == 0:
                    results.append({
                        'port': port,
                        'protocol': protocol,
                        'source': source,
                        'status': 'added'
                    })
                else:
                    results.append({
                        'port': port,
                        'protocol': protocol,
                        'source': source,
                        'status': 'failed',
                        'error': result.stderr
                    })
        
        return jsonify({
            'status': 'success',
            'rules_added': len([r for r in results if r.get('status') == 'added']),
            'details': results
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/firewall/enable', methods=['POST'])
@require_api_key
def enable_firewall():
    """NEW: Enable UFW firewall"""
    try:
        result = run_shell_cmd('ufw --force enable', timeout=30)
        
        return jsonify({
            'status': 'enabled' if result.returncode == 0 else 'failed'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/system/firewall/rule', methods=['DELETE'])
@require_api_key
def delete_firewall_rule():
    """NEW: Delete specific UFW rule"""
    try:
        data = request.get_json()
        
        port = data.get('port')
        protocol = data.get('protocol', 'tcp')
        source = data.get('source')
        
        if not port:
            return jsonify({'error': 'port required'}), 400
        
        if source:
            cmd = f"ufw delete allow from {source} to any port {port} proto {protocol}"
        else:
            cmd = f"ufw delete allow {port}/{protocol}"
        
        result = run_shell_cmd(cmd, timeout=10)
        
        return jsonify({
            'status': 'deleted' if result.returncode == 0 else 'failed',
            'stderr': result.stderr if result.returncode != 0 else None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# NGINX OPERATIONS
# ========================================

@app.route('/system/nginx/reload', methods=['POST'])
@require_api_key
def reload_nginx():
    """Reload nginx configuration (existing endpoint)"""
    try:
        # Test config first
        test_result = run_shell_cmd('nginx -t', timeout=10)
        if test_result.returncode != 0:
            return jsonify({
                'status': 'error',
                'message': 'Nginx config test failed',
                'output': test_result.stderr
            }), 400
        
        # Reload
        reload_result = run_shell_cmd('systemctl reload nginx', timeout=10)
        
        return jsonify({
            'status': 'reloaded' if reload_result.returncode == 0 else 'failed'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# CREDENTIALS MANAGEMENT
# ========================================

@app.route('/credentials/write', methods=['POST'])
@require_api_key
def write_credentials():
    """Write credentials file securely (existing endpoint)"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('path') or not data.get('content'):
            return jsonify({'error': 'path and content required'}), 400
        
        file_path = Path(data['path'])
        content = data['content']
        permissions = data.get('permissions', '600')
        
        # Security check: Only allow writes to /local/ or /app/local/
        allowed_bases = ['/local/', '/app/local/']
        path_str = str(file_path.resolve())
        
        if not any(path_str.startswith(base) for base in allowed_bases):
            return jsonify({
                'error': f'Path must be under {" or ".join(allowed_bases)}'
            }), 403
        
        # Create parent directory if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        file_path.write_text(content)
        
        # Set permissions (convert octal string to int)
        perm_int = int(permissions, 8)
        file_path.chmod(perm_int)
        
        return jsonify({
            'status': 'success',
            'path': str(file_path),
            'permissions': permissions
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ========================================
# CRON DEPLOYMENT
# ========================================

@app.route('/deploy/cron', methods=['POST'])
@require_api_key
def deploy_cron():
    """Deploy a container via cron (existing endpoint)"""
    try:
        data = request.get_json()
        
        # Build docker run command
        docker_cmd = ['docker', 'run', '--rm', '--name', data['name']]
        
        if data.get('network'):
            docker_cmd.extend(['--network', data['network']])
        
        if data.get('volumes'):
            for volume in data['volumes']:
                docker_cmd.extend(['-v', volume])
        
        if data.get('env_vars'):
            for key, value in data['env_vars'].items():
                docker_cmd.extend(['-e', f"{key}={value}"])
        
        docker_cmd.append(data['image'])
        
        if data.get('command'):
            docker_cmd.extend(data['command'])
        
        # Convert to shell command
        cmd_str = ' '.join(shlex.quote(arg) for arg in docker_cmd)
        
        # Add cron entry
        cron_line = f"{data['schedule']} {cmd_str}\n"
        
        # Add to crontab
        result = subprocess.run(
            "crontab -l 2>/dev/null || true",
            shell=True,
            capture_output=True,
            text=True
        )
        current_cron = result.stdout
        
        # Append new entry
        new_cron = current_cron + cron_line
        
        # Write back
        subprocess.run(
            "crontab -",
            shell=True,
            input=new_cron,
            text=True,
            check=True
        )
        
        return jsonify({
            'status': 'deployed',
            'schedule': data['schedule']
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/cron/<name>', methods=['DELETE'])
@require_api_key
def delete_cron(name):
    """Remove cron job (existing endpoint)"""
    try:
        user = request.args.get('user', 'root')
        identifier = f"# MANAGED_CRON_{user}_{name}"
        
        # Remove cron job
        subprocess.run(
            f"crontab -l 2>/dev/null | grep -v '{identifier}' | crontab - 2>/dev/null || true",
            shell=True,
            check=False
        )
        
        return jsonify({
            'status': 'success',
            'message': 'Cron container removed'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ========================================
# MAIN
# ========================================

if __name__ == '__main__':
    # Disable Flask request logging (reduces log noise)
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    print("=" * 60)
    print("Health Agent v2.0 - SSH-Free Deployments")
    print("=" * 60)
    print(f"Listening on: 0.0.0.0:9999")
    print(f"Total endpoints: 30")
    print(f"  - Health/Utility: 3")
    print(f"  - Containers: 6")
    print(f"  - Images: 1")
    print(f"  - Files: 4")
    print(f"  - Services: 6")
    print(f"  - Firewall: 5")
    print(f"  - Nginx: 1")
    print(f"  - Credentials: 1")
    print(f"  - Cron: 2")
    print(f"  - Volume: 1 (via Docker)")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=9999, debug=False)