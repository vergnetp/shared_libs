"""
Health Agent - HTTP API for remote server management

This file should be copied to: /usr/local/bin/health_agent.py on each server

NEW in this version:
- Added /deploy/cron endpoint for cron container deployment
- Added /cron/<name> DELETE endpoint for cron removal
"""

from flask import Flask, request, jsonify
from functools import wraps
from pathlib import Path
import subprocess
import json


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
        restart_policy = data.get('restart_policy', 'unless-stopped')
        cmd.extend(['--restart', restart_policy])
        
        # Add image
        cmd.append(data['image'])
        
        # Run command
        container_id = run_docker_cmd(cmd)
        
        return jsonify({'status': 'started', 'container_id': container_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/images/<path:image>/pull', methods=['POST'])
@require_api_key
def pull_image(image):
    try:
        run_docker_cmd(['docker', 'pull', image])
        return jsonify({'status': 'pulled', 'image': image})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========================================
# NEW ENDPOINTS FOR FIX #2
# ========================================

@app.route('/deploy/cron', methods=['POST'])
@require_api_key
def deploy_cron_container():
    """
    Deploy a container with cron schedule.
    
    Request JSON:
    {
        "image": "username/health-monitor:latest",
        "schedule": "* * * * *",
        "name": "health_monitor_system",
        "volumes": ["/local:/app/local:ro"],
        "env_vars": {},
        "user": "root"
    }
    
    Returns:
    {
        "status": "success",
        "message": "Cron container deployed",
        "details": {
            "image": "...",
            "schedule": "...",
            "log_file": "/var/log/..."
        }
    }
    """
    try:
        data = request.json
        
        image = data.get('image')
        schedule = data.get('schedule')
        name = data.get('name')
        volumes = data.get('volumes', [])
        env_vars = data.get('env_vars', {})
        user = data.get('user', 'root')
        
        if not image or not schedule or not name:
            return jsonify({
                'status': 'error',
                'message': 'Missing required fields: image, schedule, name'
            }), 400
        
        # 1. Pull image
        result = subprocess.run(
            ['docker', 'pull', image],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            return jsonify({
                'status': 'error',
                'message': f'Failed to pull image: {result.stderr}'
            }), 500
        
        # 2. Build docker run command
        docker_cmd_parts = ['docker', 'run', '--rm']
        
        # Add volumes
        for volume in volumes:
            docker_cmd_parts.extend(['-v', volume])
        
        # Add env vars
        for key, value in env_vars.items():
            docker_cmd_parts.extend(['-e', f'{key}={value}'])
        
        # Add image
        docker_cmd_parts.append(image)
        
        # Build complete command
        docker_cmd = ' '.join(
            f'"{part}"' if ' ' in str(part) else str(part) 
            for part in docker_cmd_parts
        )
        
        # 3. Create cron entry
        log_file = f"/var/log/cron_{user}_{name}.log"
        cron_entry = f"{schedule} {docker_cmd} >> {log_file} 2>&1"
        identifier = f"# MANAGED_CRON_{user}_{name}"
        
        # 4. Install cron job
        # Remove old cron with same identifier
        subprocess.run(
            f"crontab -l 2>/dev/null | grep -v '{identifier}' | crontab - 2>/dev/null || true",
            shell=True,
            check=False
        )
        
        # Add new cron
        subprocess.run(
            f"(crontab -l 2>/dev/null; echo '{identifier}'; echo '{cron_entry}') | crontab -",
            shell=True,
            check=True
        )
        
        return jsonify({
            'status': 'success',
            'message': 'Cron container deployed',
            'details': {
                'image': image,
                'schedule': schedule,
                'log_file': log_file
            }
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({
            'status': 'error',
            'message': 'Image pull timed out'
        }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/cron/<name>', methods=['DELETE'])
@require_api_key
def remove_cron_container(name):
    """
    Remove a cron container.
    
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
        "chunk": "<base64_data>",
        "chunk_index": 0,
        "total_chunks": 5,
        "upload_id": "unique-id",
        "target_path": "/path/to/extract"
    }
    """
    try:
        data = request.json
        
        chunk = data.get('chunk')
        chunk_index = data.get('chunk_index')
        total_chunks = data.get('total_chunks')
        upload_id = data.get('upload_id')
        target_path = data.get('target_path')
        
        if not all([chunk, chunk_index is not None, total_chunks, upload_id, target_path]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Create temp directory for chunks
        temp_dir = Path(f'/tmp/uploads/{upload_id}')
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Save chunk
        chunk_file = temp_dir / f'chunk_{chunk_index}'
        import base64
        chunk_file.write_bytes(base64.b64decode(chunk))
        
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
                tar.extractall(target_path)
            
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
    app.run(host='0.0.0.0', port=9999)