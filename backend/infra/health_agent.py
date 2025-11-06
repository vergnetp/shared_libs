"""
Health Agent - HTTP API for remote server management

This file should be copied to: /usr/local/bin/health_agent.py on each server

NEW in this version:
- Added /credentials/write endpoint for secure credentials management (no SSH needed)
- Added /deploy/cron endpoint for cron container deployment
- Added /cron/<n> DELETE endpoint for cron removal
- Added /files/write endpoint for general file operations (nginx configs, etc.)
- Added /files/mkdir endpoint for directory creation
"""

from flask import Flask, request, jsonify
from functools import wraps
from pathlib import Path
import subprocess
import json
import os


app = Flask(__name__)

# Security: Allowed paths for file write operations
ALLOWED_WRITE_PATHS = [
    '/local/',           # User data directories
    '/app/local/',       # Container-mounted user data
    '/etc/nginx/',       # Nginx configurations
    '/tmp/',             # Temporary files
]

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

@app.route('/ping', methods=['GET'])
@require_api_key
def ping():
    """Simple ping to check if agent is alive"""
    return jsonify({'status': 'alive'})

@app.route('/health', methods=['GET'])
@require_api_key
def health_check():
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

@app.route('/containers', methods=['GET'])
@require_api_key
def list_containers():
    try:
        # Use JSON format for easy parsing
        output = run_docker_cmd([
            'docker', 'ps', '-a',
            '--format', '{{json .}}'
        ])
        
        containers = []
        for line in output.split('\n'):
            if line:
                c = json.loads(line)
                containers.append({
                    'name': c['Names'],
                    'status': c['State'],
                    'image': c['Image']
                })
        
        return jsonify({'containers': containers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<n>', methods=['GET'])
@require_api_key
def get_container(name):
    """Get container status"""
    try:
        output = run_docker_cmd([
            'docker', 'inspect', name, '--format', '{{json .}}'
        ])
        container = json.loads(output)
        
        return jsonify({
            'name': container['Name'].lstrip('/'),
            'status': container['State']['Status'],
            'running': container['State']['Running'],
            'image': container['Config']['Image']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/containers/<n>/restart', methods=['POST'])
@require_api_key
def restart_container(name):
    try:
        run_docker_cmd(['docker', 'restart', name])
        return jsonify({'status': 'restarted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<n>/stop', methods=['POST'])
@require_api_key
def stop_container(name):
    try:
        run_docker_cmd(['docker', 'stop', name])
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<n>/remove', methods=['POST'])
@require_api_key
def remove_container(name):
    try:
        run_docker_cmd(['docker', 'rm', '-f', name])
        return jsonify({'status': 'removed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/run', methods=['POST'])
@require_api_key
def run_container():
    data = request.json
    
    if not data.get('name') or not data.get('image'):
        return jsonify({'error': 'name and image required'}), 400
    
    try:
        # Build docker run command
        cmd = ['docker', 'run', '-d', '--name', data['name']]
        
        # Add ports
        for host_port, container_port in data.get('ports', {}).items():
            cmd.extend(['-p', f'{host_port}:{container_port}'])
        
        # Add volumes
        for volume in data.get('volumes', []):
            cmd.extend(['-v', volume])
        
        # Add environment variables
        for key, value in data.get('env_vars', {}).items():
            cmd.extend(['-e', f'{key}={value}'])
        
        # Add network
        if data.get('network'):
            cmd.extend(['--network', data['network']])
        
        # Add restart policy
        if data.get('restart_policy'):
            cmd.extend(['--restart', data['restart_policy']])
        
        # Add image
        cmd.append(data['image'])
        
        # Add command (if any)
        if data.get('command'):
            cmd.extend(data['command'].split())
        
        # Run container
        container_id = run_docker_cmd(cmd)
        
        return jsonify({
            'status': 'started',
            'container_id': container_id
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<n>/logs', methods=['GET'])
@require_api_key
def get_container_logs(name):
    """Get container logs"""
    try:
        lines = request.args.get('lines', '100')
        output = run_docker_cmd(['docker', 'logs', '--tail', lines, name])
        return jsonify({'logs': output})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
# FILE OPERATIONS (NEW!)
# ========================================

@app.route('/files/write', methods=['POST'])
@require_api_key
def write_file():
    """
    Write file to allowed locations (no SSH needed).
    
    Request JSON:
    {
        "path": "/etc/nginx/nginx.conf",
        "content": "...",
        "permissions": "644"  # Optional, octal string
    }
    
    Returns:
    {
        "status": "success",
        "path": "/etc/nginx/nginx.conf"
    }
    
    Security:
    - Requires API key authentication
    - Only allows writes to whitelisted paths
    - Creates parent directories if needed
    - Supports setting permissions
    """
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
                'error': f'Path not allowed. Must be under: {", ".join(ALLOWED_WRITE_PATHS)}'
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
    """
    Create directories (no SSH needed).
    
    Request JSON:
    {
        "paths": ["/etc/nginx/conf.d", "/etc/nginx/stream.d"],
        "mode": "755"  # Optional, octal string
    }
    
    Returns:
    {
        "status": "success",
        "created": ["/etc/nginx/conf.d", "/etc/nginx/stream.d"]
    }
    
    Security:
    - Requires API key authentication
    - Only allows creation under whitelisted paths
    - Creates parent directories automatically (mkdir -p behavior)
    """
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
                    'error': f'Path not allowed: {path_str}. Must be under: {", ".join(ALLOWED_WRITE_PATHS)}'
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


# ========================================
# CREDENTIALS MANAGEMENT
# ========================================

@app.route('/credentials/write', methods=['POST'])
@require_api_key
def write_credentials():
    """
    Write credentials file securely (no SSH needed).
    
    Request JSON:
    {
        "path": "/local/userB/myapp/prod/secrets/infra/credentials.json",
        "content": "{...json content...}",
        "permissions": "600"
    }
    
    Returns:
    {
        "status": "success",
        "path": "/local/..."
    }
    
    Security:
    - Requires API key authentication
    - Sets file permissions to 600 (owner read/write only)
    - Creates parent directories if needed
    - Path must be under /local/ or /app/local/
    """
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
    """
    Deploy a container via cron.
    
    Request JSON:
    {
        "schedule": "0 2 * * *",
        "name": "backup",
        "image": "postgres:15",
        "command": "pg_dump ...",
        "volumes": [...],
        "env_vars": {...},
        "network": "myapp-prod-network"
    }
    """
    try:
        data = request.json
        
        # Build docker run command for cron
        cmd_parts = ['docker', 'run', '--rm', '--name', data['name']]
        
        if data.get('network'):
            cmd_parts.extend(['--network', data['network']])
        
        for volume in data.get('volumes', []):
            cmd_parts.extend(['-v', volume])
        
        for key, value in data.get('env_vars', {}).items():
            cmd_parts.extend(['-e', f'{key}={value}'])
        
        cmd_parts.append(data['image'])
        
        if data.get('command'):
            if isinstance(data['command'], list):
                cmd_parts.extend(data['command'])
            else:
                cmd_parts.extend(data['command'].split())
        
        docker_cmd = ' '.join(f'"{part}"' if ' ' in part else part for part in cmd_parts)
        
        # Add to crontab
        schedule = data['schedule']
        user = data.get('user', 'root')
        identifier = f"# MANAGED_CRON_{user}_{data['name']}"
        cron_line = f"{schedule} {docker_cmd} {identifier}"
        
        # Get existing crontab
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, check=False)
        existing = result.stdout if result.returncode == 0 else ""
        
        # Remove old entry if exists
        lines = [line for line in existing.split('\n') if identifier not in line]
        
        # Add new entry
        lines.append(cron_line)
        
        # Install new crontab
        new_crontab = '\n'.join(lines) + '\n'
        subprocess.run(['crontab', '-'], input=new_crontab.encode(), check=True)
        
        return jsonify({
            'status': 'success',
            'schedule': schedule,
            'command': docker_cmd
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cron/<n>', methods=['DELETE'])
@require_api_key
def remove_cron(name):
    """
    Remove cron job by name.
    
    Args:
        name: Cron job name (from deployment)
        
    Query params:
        user: User context (default: root)
    
    Returns:
    {
        "status": "success",
        "message": "Cron container removed"
    }
    """
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
# FILE UPLOAD (for pushing config/secrets)
# ========================================

@app.route('/upload/tar/chunked', methods=['POST'])
@require_api_key
def upload_tar_chunked():
    """
    Upload and extract tar file in chunks.
    
    Request JSON:
    {
        "chunk_data": "<base64_data>",
        "chunk_index": 0,
        "total_chunks": 5,
        "upload_id": "unique-id",
        "extract_path": "/local/myapp/prod"
    }
    """
    try:
        data = request.json
        
        chunk_data = data.get('chunk_data')
        chunk_index = data.get('chunk_index')
        total_chunks = data.get('total_chunks')
        upload_id = data.get('upload_id')
        extract_path = data.get('extract_path')
        
        if not all([chunk_data, chunk_index is not None, total_chunks, upload_id, extract_path]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Create temp directory for chunks
        temp_dir = Path(f'/tmp/uploads/{upload_id}')
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Save chunk
        chunk_file = temp_dir / f'chunk_{chunk_index}'
        import base64
        chunk_file.write_bytes(base64.b64decode(chunk_data))
        
        # If this is the last chunk, assemble and extract
        if chunk_index == total_chunks - 1:
            # Assemble all chunks
            tar_file = temp_dir / 'archive.tar.gz'
            with tar_file.open('wb') as f:
                for i in range(total_chunks):
                    chunk_path = temp_dir / f'chunk_{i}'
                    f.write(chunk_path.read_bytes())
            
            # Extract tar
            import tarfile
            with tarfile.open(tar_file, 'r:gz') as tar:
                tar.extractall(extract_path)
            
            # Cleanup
            import shutil
            shutil.rmtree(temp_dir)
            
            return jsonify({
                'status': 'complete',
                'message': 'File uploaded and extracted'
            })
        else:
            return jsonify({
                'status': 'chunk_received',
                'chunk_index': chunk_index
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Disable Flask request logging (reduces log noise)
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app.run(host='0.0.0.0', port=9999)