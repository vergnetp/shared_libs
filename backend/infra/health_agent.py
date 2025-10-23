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
        for host_path, container_path in data.get('volumes', {}).items():
            cmd.extend(['-v', f'{host_path}:{container_path}'])
        
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

# File upload remains the same (no docker library needed)
@app.route('/upload/tar/chunked', methods=['POST'])
@require_api_key
def upload_tar_chunked():
    # ... (same as before)
    pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9999)