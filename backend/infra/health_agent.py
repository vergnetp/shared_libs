"""
Health Agent - HTTP API for remote server management

This file should be copied to: /usr/local/bin/health_agent.py on each server

NEW in this version:
- Added /credentials/write endpoint for secure credentials management (no SSH needed)
- Added /deploy/cron endpoint for cron container deployment
- Added /cron/<n> DELETE endpoint for cron removal
"""

from flask import Flask, request, jsonify
from functools import wraps
from pathlib import Path
import subprocess
import json
import os


app = Flask(__name__)

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

@app.route('/containers/<name>', methods=['GET'])
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

@app.route('/containers/<name>/restart', methods=['POST'])
@require_api_key
def restart_container(name):
    try:
        run_docker_cmd(['docker', 'restart', name])
        return jsonify({'status': 'restarted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<name>/stop', methods=['POST'])
@require_api_key
def stop_container(name):
    try:
        run_docker_cmd(['docker', 'stop', name])
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<name>/remove', methods=['POST'])
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
# CREDENTIALS MANAGEMENT (NEW!)
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
        "name": "backup_prod",
        "schedule": "0 2 * * *",
        "image": "myapp/backup:latest",
        "command": "python backup.py",
        "env_vars": {"DB_HOST": "localhost"},
        "volumes": ["/data:/app/data"],
        "network": "myapp_network",
        "user": "root"
    }
    
    Returns:
    {
        "status": "success",
        "cron_entry": "0 2 * * * ..."
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required = ['name', 'schedule', 'image']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({'error': f'Missing required fields: {missing}'}), 400
        
        name = data['name']
        schedule = data['schedule']
        image = data['image']
        command = data.get('command', '')
        env_vars = data.get('env_vars', {})
        volumes = data.get('volumes', [])
        network = data.get('network', '')
        user = data.get('user', 'root')
        
        # Build docker run command
        docker_cmd = f'docker run --rm --name {name}'
        
        for key, value in env_vars.items():
            docker_cmd += f' -e {key}="{value}"'
        
        for volume in volumes:
            docker_cmd += f' -v {volume}'
        
        if network:
            docker_cmd += f' --network {network}'
        
        docker_cmd += f' {image}'
        
        if command:
            docker_cmd += f' {command}'
        
        # Create cron entry with identifier for management
        identifier = f'# MANAGED_CRON_{user}_{name}'
        cron_entry = f'{schedule} {docker_cmd} {identifier}'
        
        # Get existing crontab
        result = subprocess.run(
            'crontab -l 2>/dev/null || echo ""',
            shell=True,
            capture_output=True,
            text=True
        )
        existing_crontab = result.stdout
        
        # Remove old entry for this job if it exists
        lines = [line for line in existing_crontab.split('\n') 
                if identifier not in line]
        
        # Add new entry
        lines.append(cron_entry)
        
        # Install new crontab
        new_crontab = '\n'.join(lines)
        subprocess.run(
            'crontab -',
            input=new_crontab,
            shell=True,
            text=True,
            check=True
        )
        
        return jsonify({
            'status': 'success',
            'cron_entry': cron_entry
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/cron/<name>', methods=['DELETE'])
@require_api_key
def remove_cron(name):
    """
    Remove cron container deployment.
    
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