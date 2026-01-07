"""
Node Agent - HTTP API for SSH-Free Deployments (SaaS-Ready)

This module contains the node agent code that runs on each droplet.
It's embedded into droplet snapshots during creation.

The agent provides HTTP endpoints for:
- Container management (run, stop, remove, logs)
- Docker image operations (pull, list)
- File uploads (chunked tar)
- Service control (nginx, docker)
- Firewall management (ufw)

Multi-tenancy:
- Each customer/workspace gets unique API key
- API key stored on droplet at /etc/node-agent/api-key
- Agent port 9999, firewalled to VPC only
"""

# The node agent Flask app code - embedded as a string for cloud-init
NODE_AGENT_CODE = '''#!/usr/bin/env python3
"""
Node Agent v1.0 - SSH-Free Deployments for SaaS
Runs on port 9999, protected by API key.
"""

from flask import Flask, request, jsonify
from functools import wraps
from pathlib import Path
import subprocess
import json
import os
import base64
import tarfile
import io
import shutil

app = Flask(__name__)

# Security: Allowed paths for file operations
ALLOWED_WRITE_PATHS = ['/local/', '/app/', '/etc/nginx/', '/tmp/']
ALLOWED_SERVICES = ['nginx', 'docker', 'node-agent']

# Private network ranges (VPC - no auth required by default)
PRIVATE_NETWORKS = [
    ('10.', ),           # 10.0.0.0/8
    ('172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', 
     '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', 
     '172.30.', '172.31.'),  # 172.16.0.0/12
    ('192.168.',),       # 192.168.0.0/16
]

# Security: Set to disable VPC auth bypass (require API key for ALL requests)
# Use for high-security environments where even VPC traffic shouldn't be trusted
REQUIRE_AUTH_ALWAYS = os.environ.get('NODE_AGENT_REQUIRE_AUTH_ALWAYS', '').lower() in ('1', 'true', 'yes')

# IP Allowlist: Only allow requests from these IPs (comma-separated)
# If set, only these IPs can access the agent (even with valid API key)
# Example: NODE_AGENT_ALLOWED_IPS=10.120.0.5,192.168.1.100
ALLOWED_IPS_RAW = os.environ.get('NODE_AGENT_ALLOWED_IPS', '')
ALLOWED_IPS = set(ip.strip() for ip in ALLOWED_IPS_RAW.split(',') if ip.strip()) if ALLOWED_IPS_RAW else None


def is_private_network(ip):
    """Check if IP is from private network (VPC)"""
    if not ip:
        return False
    for prefixes in PRIVATE_NETWORKS:
        for prefix in prefixes:
            if ip.startswith(prefix):
                return True
    return False


def is_ip_allowed(ip):
    """Check if IP is in allowlist (if allowlist is configured)"""
    if ALLOWED_IPS is None:
        return True  # No allowlist = all IPs allowed
    return ip in ALLOWED_IPS


def run_cmd(cmd, shell=False, timeout=60):
    """Run command and return result"""
    result = subprocess.run(
        cmd if shell else cmd.split() if isinstance(cmd, str) else cmd,
        shell=shell,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    return result


def require_api_key(f):
    """Decorator to require API key authentication.
    
    Security layers (in order):
    1. IP Allowlist (NODE_AGENT_ALLOWED_IPS) - if set, blocks all other IPs
    2. VPC bypass - skips API key for private IPs (unless REQUIRE_AUTH_ALWAYS)
    3. API key check - required for public IPs
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get client IP
        client_ip = request.remote_addr
        
        # Layer 1: IP Allowlist (strictest - if set, only these IPs can access)
        if not is_ip_allowed(client_ip):
            return jsonify({'error': 'IP not in allowlist', 'your_ip': client_ip}), 403
        
        # Layer 2: Skip auth for private network (VPC) requests, unless disabled
        if not REQUIRE_AUTH_ALWAYS and is_private_network(client_ip):
            return f(*args, **kwargs)
        
        # Layer 3: Public requests (and all if REQUIRE_AUTH_ALWAYS) require API key
        api_key = request.headers.get('X-API-Key')
        try:
            expected = Path('/etc/node-agent/api-key').read_text().strip()
            if api_key != expected:
                return jsonify({'error': 'Unauthorized'}), 401
        except FileNotFoundError:
            return jsonify({'error': 'API key not configured'}), 500
        return f(*args, **kwargs)
    return decorated


# ========================================
# HEALTH ENDPOINTS (PUBLIC - no auth)
# ========================================

@app.route('/ping', methods=['GET'])
def ping():
    """Simple health check - public"""
    return jsonify({'status': 'alive', 'version': '1.0'})


@app.route('/health', methods=['GET'])
def health():
    """Comprehensive health check - public"""
    try:
        result = run_cmd(['docker', 'info'])
        docker_ok = result.returncode == 0
        
        ps_result = run_cmd(['docker', 'ps', '--format', '{{.Names}}'])
        containers = ps_result.stdout.strip().split('\\n') if ps_result.stdout.strip() else []
        
        return jsonify({
            'docker_running': docker_ok,
            'containers': containers,
            'status': 'healthy' if docker_ok else 'degraded',
            'security': {
                'ip_allowlist_enabled': ALLOWED_IPS is not None,
                'ip_allowlist_count': len(ALLOWED_IPS) if ALLOWED_IPS else 0,
                'vpc_auth_bypass': not REQUIRE_AUTH_ALWAYS,
                'api_key_configured': Path('/etc/node-agent/api-key').exists(),
            }
        })
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
        result = run_cmd(['docker', 'ps', '-a', '--format', '{{json .}}'])
        containers = []
        for line in result.stdout.strip().split('\\n'):
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
        
        cmd = ['docker', 'run', '-d']
        
        if data.get('name'):
            cmd.extend(['--name', data['name']])
        
        if data.get('network'):
            cmd.extend(['--network', data['network']])
        
        if data.get('restart_policy'):
            cmd.extend(['--restart', data['restart_policy']])
        
        for host_port, container_port in (data.get('ports') or {}).items():
            cmd.extend(['-p', f"{host_port}:{container_port}"])
        
        for volume in (data.get('volumes') or []):
            cmd.extend(['-v', volume])
        
        for key, value in (data.get('env_vars') or {}).items():
            cmd.extend(['-e', f"{key}={value}"])
        
        cmd.append(data['image'])
        
        if data.get('command'):
            cmd.extend(data['command'] if isinstance(data['command'], list) else [data['command']])
        
        result = run_cmd(cmd, timeout=120)
        
        if result.returncode == 0:
            return jsonify({
                'status': 'started',
                'container_id': result.stdout.strip()
            })
        else:
            return jsonify({
                'status': 'error',
                'error': result.stderr
            }), 500
            
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/containers/<name>/stop', methods=['POST'])
@require_api_key
def stop_container(name):
    """Stop a container"""
    try:
        result = run_cmd(['docker', 'stop', name], timeout=30)
        if result.returncode == 0:
            return jsonify({'status': 'stopped'})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<name>/remove', methods=['POST'])
@require_api_key
def remove_container(name):
    """Remove a container"""
    try:
        result = run_cmd(['docker', 'rm', '-f', name])
        if result.returncode == 0:
            return jsonify({'status': 'removed'})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<name>/logs', methods=['GET'])
@require_api_key
def get_logs(name):
    """Get container logs"""
    try:
        lines = request.args.get('lines', '100')
        result = run_cmd(['docker', 'logs', '--tail', lines, name])
        return jsonify({'logs': result.stdout + result.stderr})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<name>/status', methods=['GET'])
@require_api_key
def container_status(name):
    """Get container status"""
    try:
        result = run_cmd([
            'docker', 'inspect', '--format',
            '{{.State.Running}} {{.State.Status}} {{.RestartCount}}',
            name
        ])
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            return jsonify({
                'name': name,
                'running': parts[0].lower() == 'true',
                'status': parts[1] if len(parts) > 1 else 'unknown',
                'restart_count': int(parts[2]) if len(parts) > 2 else 0
            })
        else:
            return jsonify({'error': 'Container not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# IMAGE OPERATIONS
# ========================================

@app.route('/images/pull', methods=['POST'])
@require_api_key
def pull_image():
    """Pull a Docker image"""
    try:
        data = request.get_json()
        image = data.get('image')
        if not image:
            return jsonify({'error': 'image required'}), 400
        
        result = run_cmd(['docker', 'pull', image], timeout=600)
        
        if result.returncode == 0:
            return jsonify({'status': 'pulled', 'image': image})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/images/load', methods=['POST'])
@require_api_key
def load_image():
    """Load Docker image from base64-encoded tar"""
    try:
        data = request.get_json()
        image_data = base64.b64decode(data.get('data', ''))
        
        # Write to temp file
        temp_path = '/tmp/image_load.tar'
        with open(temp_path, 'wb') as f:
            f.write(image_data)
        
        result = run_cmd(['docker', 'load', '-i', temp_path], timeout=300)
        os.remove(temp_path)
        
        if result.returncode == 0:
            return jsonify({'status': 'loaded', 'output': result.stdout})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/docker/build', methods=['POST'])
@require_api_key
def build_image():
    """Build Docker image from uploaded code"""
    try:
        data = request.get_json()
        context_path = data.get('context_path', '/app')
        image_tag = data.get('image_tag', 'app:latest')
        dockerfile = data.get('dockerfile')  # Optional: override Dockerfile path or content
        
        # Security: only allow building from allowed paths
        if not any(context_path.startswith(p) for p in ALLOWED_WRITE_PATHS):
            return jsonify({'error': 'Build context path not allowed'}), 403
        
        if not Path(context_path).exists():
            return jsonify({'error': f'Context path does not exist: {context_path}'}), 400
        
        # Check for Dockerfile
        dockerfile_path = Path(context_path) / 'Dockerfile'
        if dockerfile:
            # Write provided Dockerfile content
            dockerfile_path.write_text(dockerfile)
        elif not dockerfile_path.exists():
            return jsonify({'error': 'No Dockerfile found in context'}), 400
        
        # Build
        cmd = ['docker', 'build', '-t', image_tag, context_path]
        result = run_cmd(cmd, timeout=600)  # 10 min timeout for builds
        
        if result.returncode == 0:
            return jsonify({
                'status': 'built',
                'image_tag': image_tag,
                'output': result.stdout
            })
        else:
            return jsonify({
                'status': 'error',
                'error': result.stderr,
                'output': result.stdout
            }), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# FILE OPERATIONS
# ========================================

@app.route('/files/write', methods=['POST'])
@require_api_key
def write_file():
    """Write file to allowed path"""
    try:
        data = request.get_json()
        path = data.get('path')
        content = data.get('content', '')
        permissions = data.get('permissions', '644')
        
        if not path:
            return jsonify({'error': 'path required'}), 400
        
        # Security check
        if not any(path.startswith(p) for p in ALLOWED_WRITE_PATHS):
            return jsonify({'error': 'Path not allowed'}), 403
        
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        file_path.chmod(int(permissions, 8))
        
        return jsonify({'status': 'written', 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload/tar', methods=['POST'])
@require_api_key
def upload_tar():
    """Upload and extract tar.gz archive"""
    try:
        data = request.get_json()
        tar_data = base64.b64decode(data.get('data', ''))
        extract_path = data.get('extract_path', '/tmp')
        
        # Security check
        if not any(extract_path.startswith(p) for p in ALLOWED_WRITE_PATHS):
            return jsonify({'error': 'Path not allowed'}), 403
        
        Path(extract_path).mkdir(parents=True, exist_ok=True)
        
        tar_buffer = io.BytesIO(tar_data)
        with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
            tar.extractall(extract_path)
        
        return jsonify({'status': 'extracted', 'path': extract_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Chunked upload state
_upload_chunks = {}


@app.route('/git/clone', methods=['POST'])
@require_api_key
def git_clone():
    """Clone a git repository"""
    try:
        data = request.get_json()
        repo_url = data.get('repo_url')
        branch = data.get('branch', 'main')
        target_path = data.get('target_path', '/app')
        access_token = data.get('access_token')  # Optional, for private repos
        
        if not repo_url:
            return jsonify({'error': 'repo_url required'}), 400
        
        # Security check
        if not any(target_path.startswith(p) for p in ALLOWED_WRITE_PATHS):
            return jsonify({'error': 'Target path not allowed'}), 403
        
        # Clean target directory
        if Path(target_path).exists():
            import shutil
            shutil.rmtree(target_path)
        Path(target_path).mkdir(parents=True, exist_ok=True)
        
        # Embed token in URL for private repos (HTTPS only)
        clone_url = repo_url
        if access_token and 'github.com' in repo_url:
            # https://github.com/user/repo -> https://token@github.com/user/repo
            clone_url = repo_url.replace('https://github.com', f'https://{access_token}@github.com')
        elif access_token and 'gitlab.com' in repo_url:
            clone_url = repo_url.replace('https://gitlab.com', f'https://oauth2:{access_token}@gitlab.com')
        elif access_token and 'bitbucket.org' in repo_url:
            clone_url = repo_url.replace('https://bitbucket.org', f'https://x-token-auth:{access_token}@bitbucket.org')
        
        # Clone
        cmd = ['git', 'clone', '--depth', '1', '--branch', branch, clone_url, target_path]
        result = run_cmd(cmd, timeout=300)  # 5 min timeout
        
        if result.returncode == 0:
            # Get commit info
            commit_result = run_cmd(['git', '-C', target_path, 'rev-parse', 'HEAD'], timeout=10)
            commit = commit_result.stdout.strip()[:8] if commit_result.returncode == 0 else 'unknown'
            
            return jsonify({
                'status': 'cloned',
                'path': target_path,
                'branch': branch,
                'commit': commit,
            })
        else:
            # Sanitize error (remove token if present)
            error = result.stderr
            if access_token:
                error = error.replace(access_token, '***')
            return jsonify({'status': 'error', 'error': error}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload/tar/chunked', methods=['POST'])
@require_api_key
def upload_tar_chunked():
    """Upload tar in chunks"""
    try:
        data = request.get_json()
        upload_id = data.get('upload_id')
        chunk_index = data.get('chunk_index')
        total_chunks = data.get('total_chunks')
        chunk_data = base64.b64decode(data.get('chunk_data', ''))
        extract_path = data.get('extract_path', '/tmp')
        
        # Security check
        if not any(extract_path.startswith(p) for p in ALLOWED_WRITE_PATHS):
            return jsonify({'error': 'Path not allowed'}), 403
        
        # Store chunk
        if upload_id not in _upload_chunks:
            _upload_chunks[upload_id] = {}
        _upload_chunks[upload_id][chunk_index] = chunk_data
        
        # Check if all chunks received
        if len(_upload_chunks[upload_id]) == total_chunks:
            # Reassemble
            full_data = b''
            for i in range(total_chunks):
                full_data += _upload_chunks[upload_id][i]
            
            # Clean up
            del _upload_chunks[upload_id]
            
            # Extract
            Path(extract_path).mkdir(parents=True, exist_ok=True)
            tar_buffer = io.BytesIO(full_data)
            with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
                tar.extractall(extract_path)
            
            return jsonify({'status': 'complete', 'path': extract_path})
        
        return jsonify({'status': 'chunk_received', 'chunks': len(_upload_chunks[upload_id])})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# SERVICE CONTROL
# ========================================

@app.route('/services/<name>/restart', methods=['POST'])
@require_api_key
def restart_service(name):
    """Restart a service"""
    if name not in ALLOWED_SERVICES:
        return jsonify({'error': f'Service {name} not allowed'}), 403
    try:
        result = run_cmd(['systemctl', 'restart', name], timeout=30)
        return jsonify({'status': 'restarted' if result.returncode == 0 else 'failed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/services/<name>/status', methods=['GET'])
@require_api_key
def service_status(name):
    """Get service status"""
    try:
        result = run_cmd(['systemctl', 'is-active', name])
        return jsonify({'service': name, 'status': result.stdout.strip()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/nginx/reload', methods=['POST'])
@require_api_key
def reload_nginx():
    """Reload nginx configuration"""
    try:
        # Test config first
        test = run_cmd(['nginx', '-t'])
        if test.returncode != 0:
            return jsonify({'status': 'error', 'error': test.stderr}), 400
        
        result = run_cmd(['systemctl', 'reload', 'nginx'])
        return jsonify({'status': 'reloaded' if result.returncode == 0 else 'failed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# FIREWALL (UFW)
# ========================================

@app.route('/firewall/status', methods=['GET'])
@require_api_key
def firewall_status():
    """Get UFW status"""
    try:
        result = run_cmd('ufw status verbose', shell=True)
        return jsonify({'output': result.stdout})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/firewall/allow', methods=['POST'])
@require_api_key
def firewall_allow():
    """Add UFW allow rule"""
    try:
        data = request.get_json()
        port = data.get('port')
        proto = data.get('protocol', 'tcp')
        source = data.get('source')
        
        if not port:
            return jsonify({'error': 'port required'}), 400
        
        if source:
            cmd = f"ufw allow from {source} to any port {port} proto {proto}"
        else:
            cmd = f"ufw allow {port}/{proto}"
        
        result = run_cmd(cmd, shell=True)
        return jsonify({'status': 'added' if result.returncode == 0 else 'failed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# MAIN
# ========================================

if __name__ == '__main__':
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    print("=" * 50)
    print("Node Agent v2.1 - SSH-Free Deployments")
    print("=" * 50)
    print("Port: 9999")
    print("Auth: X-API-Key header")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=9999, debug=False)
'''


def get_node_agent_install_script(
    api_key: str,
    allowed_ips: list[str] = None,
    require_auth_always: bool = False,
) -> str:
    """
    Generate shell script to install node agent.
    
    Args:
        api_key: The API key for authentication
        allowed_ips: Optional list of IPs that can access the agent
        require_auth_always: If True, require API key even from VPC
        
    Returns:
        Shell script lines as string (ready to embed in cloud-init)
    """
    # Build environment section for systemd
    env_lines = []
    if allowed_ips:
        env_lines.append(f"Environment=NODE_AGENT_ALLOWED_IPS={','.join(allowed_ips)}")
    if require_auth_always:
        env_lines.append("Environment=NODE_AGENT_REQUIRE_AUTH_ALWAYS=1")
    
    env_section = "\n".join(env_lines) if env_lines else "# No extra environment variables"
    
    return f'''
# ========================================
# INSTALL NODE AGENT
# ========================================

log 'Installing node agent dependencies...'

# Install dependencies (ensure pip3 exists)
log 'Ensuring python3-pip is installed...'
apt-get install -y python3-pip || true
pip3 --version || echo 'WARNING: pip3 not found after install!'

# Install Flask (--ignore-installed to bypass blinker conflict on Ubuntu 24)
log 'Installing Flask with --ignore-installed...'
pip3 install --ignore-installed --break-system-packages flask
log 'Flask installed'

# Write node agent script
log 'Writing node agent script to /usr/local/bin/node_agent.py...'
cat > /usr/local/bin/node_agent.py << 'AGENT_EOF'
{NODE_AGENT_CODE}
AGENT_EOF

chmod +x /usr/local/bin/node_agent.py
log 'Node agent script written'

# Create API key
log 'Creating API key file...'
mkdir -p /etc/node-agent
echo '{api_key}' > /etc/node-agent/api-key
chmod 600 /etc/node-agent/api-key
log 'API key configured'

# Create systemd service
log 'Creating systemd service...'
cat > /etc/systemd/system/node-agent.service << 'SERVICE_EOF'
[Unit]
Description=Node Agent API
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/usr/local/bin
ExecStart=/usr/bin/python3 /usr/local/bin/node_agent.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
{env_section}

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Configure firewall - allow agent port AND SSH
log 'Configuring firewall...'
ufw allow 22/tcp comment 'SSH' || true
ufw allow 9999/tcp comment 'Node Agent API' || true

# Enable and start
log 'Enabling and starting node-agent service...'
systemctl daemon-reload
systemctl enable node-agent
systemctl start node-agent

log 'Waiting 2s for service to start...'
sleep 2

log 'Node agent service status:'
systemctl is-active node-agent || echo 'WARNING: node-agent not active yet'

log 'Node Agent installation script finished'
'''
