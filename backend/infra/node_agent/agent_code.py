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

# Module-level version constant (importable)
AGENT_VERSION = "2.4.0"

# The node agent Flask app code - embedded as a string for cloud-init
NODE_AGENT_CODE = '''#!/usr/bin/env python3
"""
Node Agent - SSH-Free Deployments for SaaS
Runs on port 9999, protected by API key.
"""

AGENT_VERSION = "2.4.0"  # Unified container health endpoint (GET/POST same response)

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
import socket
import time
import urllib.request
import urllib.error

app = Flask(__name__)

# Security: Allowed paths for file operations
ALLOWED_WRITE_PATHS = ['/local/', '/app/', '/etc/nginx/', '/tmp/']
ALLOWED_SERVICES = ['nginx', 'docker', 'node-agent']

def is_path_allowed(path: str) -> bool:
    """Check if path is in allowed write paths. Normalizes paths for comparison."""
    # Normalize: ensure path ends with / for directory comparison
    normalized = path.rstrip('/') + '/'
    return any(normalized.startswith(p) for p in ALLOWED_WRITE_PATHS)

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
REQUIRE_AUTH_ALWAYS = os.environ.get('NODE_AGENT_REQUIRE_AUTH_ALWAYS', 'true').lower() in ('1', 'true', 'yes')

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
    return jsonify({'status': 'alive', 'version': AGENT_VERSION})


@app.route('/health', methods=['GET'])
def health():
    """Comprehensive health check - public"""
    try:
        result = run_cmd(['docker', 'info'])
        docker_ok = result.returncode == 0
        
        ps_result = run_cmd(['docker', 'ps', '--format', '{{.Names}}'])
        containers = ps_result.stdout.strip().split('\\n') if ps_result.stdout.strip() else []
        
        return jsonify({
            'version': AGENT_VERSION,
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
        return jsonify({'status': 'unhealthy', 'error': str(e), 'version': AGENT_VERSION}), 500


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
        
        # Security: Validate volume mounts
        volumes = data.get('volumes') or []
        valid, error = validate_volume_mounts(volumes)
        if not valid:
            return jsonify({'status': 'error', 'error': error}), 403
        
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


@app.route('/containers/<n>/start', methods=['POST'])
@require_api_key
def start_container(n):
    """Start a stopped container"""
    try:
        result = run_cmd(['docker', 'start', n], timeout=30)
        if result.returncode == 0:
            return jsonify({'status': 'started'})
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



@app.route('/metrics', methods=['GET'])
@require_api_key
def get_metrics():
    """Get container and system metrics"""
    try:
        # Get docker stats for all running containers
        result = run_cmd(['docker', 'stats', '--no-stream', '--format', 
            '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","memory":"{{.MemUsage}}","mem_perc":"{{.MemPerc}}","net":"{{.NetIO}}","block":"{{.BlockIO}}"}'])
        
        containers = []
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\\n'):
                try:
                    containers.append(json.loads(line))
                except:
                    pass
        
        # Get system metrics
        import shutil
        disk = shutil.disk_usage('/')
        
        # Get memory info
        mem_info = {}
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        if key in ('MemTotal', 'MemAvailable', 'MemFree'):
                            mem_info[key] = int(val) * 1024
        except:
            pass
        
        # Get CPU load
        load_avg = (0, 0, 0)
        try:
            load_avg = os.getloadavg()
        except:
            pass
        
        return jsonify({
            'containers': containers,
            'system': {
                'disk_total': disk.total,
                'disk_used': disk.used,
                'disk_free': disk.free,
                'disk_percent': round(disk.used / disk.total * 100, 1),
                'mem_total': mem_info.get('MemTotal', 0),
                'mem_available': mem_info.get('MemAvailable', 0),
                'mem_used': mem_info.get('MemTotal', 0) - mem_info.get('MemAvailable', 0),
                'mem_percent': round((1 - mem_info.get('MemAvailable', 0) / max(mem_info.get('MemTotal', 1), 1)) * 100, 1) if mem_info.get('MemTotal') else 0,
                'load_1m': round(load_avg[0], 2),
                'load_5m': round(load_avg[1], 2),
                'load_15m': round(load_avg[2], 2),
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/health/containers', methods=['GET'])
@require_api_key
def health_check_containers():
    """Health check all running containers"""
    try:
        result = run_cmd(['docker', 'ps', '--format', 
            '{"name":"{{.Names}}","status":"{{.Status}}","state":"{{.State}}","image":"{{.Image}}","ports":"{{.Ports}}"}'])
        
        containers = []
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\\n'):
                try:
                    c = json.loads(line)
                    status = c.get('status', '')
                    health = 'unknown'
                    if '(healthy)' in status:
                        health = 'healthy'
                    elif '(unhealthy)' in status:
                        health = 'unhealthy'
                    elif '(starting)' in status:
                        health = 'starting'
                    elif c.get('state', '').lower() == 'running':
                        health = 'running'
                    
                    uptime = ''
                    if 'Up ' in status:
                        uptime = status.split('Up ')[1].split(' (')[0] if ' (' in status else status.split('Up ')[1]
                    
                    containers.append({
                        'name': c.get('name', ''),
                        'image': c.get('image', ''),
                        'state': c.get('state', ''),
                        'health': health,
                        'uptime': uptime,
                        'ports': c.get('ports', ''),
                    })
                except:
                    pass
        
        total = len(containers)
        healthy = sum(1 for c in containers if c['health'] in ('healthy', 'running'))
        unhealthy = sum(1 for c in containers if c['health'] == 'unhealthy')
        
        return jsonify({
            'containers': containers,
            'summary': {
                'total': total,
                'healthy': healthy,
                'unhealthy': unhealthy,
                'status': 'healthy' if unhealthy == 0 and total > 0 else 'unhealthy' if unhealthy > 0 else 'empty'
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<n>/health', methods=['GET', 'POST'])
@require_api_key
def container_health(name):
    """
    Comprehensive health check for a container.
    
    GET: Query params ?port=6379&since=2026-01-21T15:00:00Z (all optional)
    POST: JSON body {"port": 6379, "since": "..."} (all optional)
    
    Response:
        {
            "status": "healthy|degraded|unhealthy",
            "container": { ... },              // Docker state info
            "port_check": { ... },             // Only if port provided
            "logs": { ... },                   // Log analysis
            "details": { "reason": "..." }     // Explains status
        }
    """
    try:
        # Get params from query string (GET) or JSON body (POST)
        if request.method == 'POST':
            data = request.get_json() or {}
            port = data.get('port')
            since = data.get('since')
        else:
            port = request.args.get('port', type=int)
            since = request.args.get('since')
        
        # =================================================================
        # Step 1: Docker inspect - get container state
        # =================================================================
        result = run_cmd(['docker', 'inspect', '--format', 
            '{{.State.Running}}|{{.State.Health.Status}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|{{.State.Status}}|{{.State.OOMKilled}}|{{.RestartCount}}|{{.State.Error}}', name])
        
        if result.returncode != 0:
            return jsonify({'error': f'Container not found: {name}'}), 404
        
        parts = result.stdout.strip().split('|')
        running = parts[0].lower() == 'true' if parts else False
        docker_health = parts[1] if len(parts) > 1 else 'none'
        started_at = parts[2] if len(parts) > 2 else ''
        finished_at = parts[3] if len(parts) > 3 else ''
        exit_code = int(parts[4]) if len(parts) > 4 and parts[4].lstrip('-').isdigit() else None
        docker_status = parts[5] if len(parts) > 5 else 'unknown'
        oom_killed = parts[6].lower() == 'true' if len(parts) > 6 else False
        restart_count = int(parts[7]) if len(parts) > 7 and parts[7].isdigit() else 0
        docker_error = parts[8] if len(parts) > 8 and parts[8] else None
        
        container_info = {
            'name': name,
            'running': running,
            'docker_health': docker_health if docker_health != 'none' else None,
            'status': docker_status,
            'started_at': started_at,
            'finished_at': finished_at,
            'exit_code': exit_code,
            'oom_killed': oom_killed,
            'restart_count': restart_count,
            'error': docker_error,
        }
        
        # =================================================================
        # Step 2: Determine health status
        # =================================================================
        status = 'healthy'
        details = {}
        port_check_result = None
        logs_result = None
        
        # Check 1: Container not running = unhealthy
        if not running:
            status = 'unhealthy'
            if oom_killed:
                details['reason'] = f'Container was killed due to Out Of Memory (OOM). Exit code: {exit_code}'
            elif exit_code is not None and exit_code != 0:
                details['reason'] = f'Container exited with code {exit_code}'
                # Common exit codes
                exit_meanings = {
                    1: 'General error',
                    137: 'SIGKILL (likely OOM or manual kill)',
                    139: 'SIGSEGV (segmentation fault)',
                    143: 'SIGTERM (graceful shutdown)',
                    255: 'Exit status out of range',
                }
                if exit_code in exit_meanings:
                    details['reason'] += f' - {exit_meanings[exit_code]}'
            elif docker_error:
                details['reason'] = f'Container error: {docker_error}'
            else:
                details['reason'] = f'Container is not running (status: {docker_status})'
        
        # Check 2: TCP port check (if port provided and container running)
        if port and running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                start_time = time.time()
                conn_result = sock.connect_ex(('localhost', int(port)))
                elapsed_ms = (time.time() - start_time) * 1000
                sock.close()
                
                port_check_result = {
                    'port': port,
                    'reachable': conn_result == 0,
                    'response_time_ms': round(elapsed_ms, 2),
                }
                
                if conn_result != 0:
                    status = 'unhealthy'
                    details['reason'] = f'Port {port} is not responding (container running but service not accepting connections)'
                    port_check_result['error'] = f'Connection refused (errno: {conn_result})'
                    
            except socket.timeout:
                port_check_result = {
                    'port': port,
                    'reachable': False,
                    'error': 'Connection timed out',
                }
                status = 'unhealthy'
                details['reason'] = f'Port {port} timed out'
            except Exception as e:
                port_check_result = {
                    'port': port,
                    'reachable': False,
                    'error': str(e),
                }
                status = 'unhealthy'
                details['reason'] = f'Port check failed: {e}'
        
        # Check 3: Log analysis (if container running and not already unhealthy)
        if running and status != 'unhealthy':
            error_patterns = [
                'error', 'Error', 'ERROR',
                'exception', 'Exception', 'EXCEPTION',
                'fatal', 'Fatal', 'FATAL',
                'panic', 'Panic', 'PANIC',
                'Traceback',
                'CRITICAL',
                'failed', 'Failed', 'FAILED',
            ]
            
            # Get logs
            log_cmd = ['docker', 'logs', '--tail', '100']
            if since:
                log_cmd.extend(['--since', since])
            log_cmd.append(name)
            
            log_result = run_cmd(log_cmd)
            logs = (log_result.stdout + log_result.stderr) if log_result.returncode == 0 else ''
            
            # If no logs with since filter, get last 20 lines
            if not logs.strip() and since:
                log_cmd = ['docker', 'logs', '--tail', '20', name]
                log_result = run_cmd(log_cmd)
                logs = (log_result.stdout + log_result.stderr) if log_result.returncode == 0 else ''
            
            # Analyze logs for errors
            error_lines = []
            for line in logs.split('\\n'):
                line_stripped = line.strip()
                if line_stripped and any(pattern in line_stripped for pattern in error_patterns):
                    # Avoid false positives
                    lower_line = line_stripped.lower()
                    if 'no error' in lower_line or 'without error' in lower_line or 'error=nil' in lower_line:
                        continue
                    if 'error_count=0' in lower_line or 'errors=0' in lower_line:
                        continue
                    error_lines.append(line_stripped)
            
            logs_result = {
                'has_errors': len(error_lines) > 0,
                'error_count': len(error_lines),
                'sample_errors': error_lines[:5],  # First 5 errors
                'lines_checked': len(logs.split('\\n')),
            }
            
            if error_lines:
                status = 'degraded'
                details['reason'] = f'Found {len(error_lines)} error(s) in logs'
                details['sample_error'] = error_lines[0][:200]  # First error, truncated
        
        # =================================================================
        # Build response
        # =================================================================
        response = {
            'status': status,
            'container': container_info,
        }
        
        if port_check_result:
            response['port_check'] = port_check_result
        
        if logs_result:
            response['logs'] = logs_result
        
        if details:
            response['details'] = details
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Internal Health Checks (TCP/HTTP to localhost)
# =============================================================================

@app.route('/health/tcp', methods=['POST'])
@require_api_key
def health_check_tcp():
    """
    TCP health check to internal port.
    
    Request body:
        {
            "port": 6379,
            "host": "localhost",  # optional, defaults to localhost
            "timeout": 5          # optional, defaults to 5 seconds
        }
    
    Response:
        {
            "status": "healthy" | "unhealthy",
            "response_time_ms": 12.5,
            "error": "..." (only if unhealthy)
        }
    """
    try:
        data = request.get_json() or {}
        port = data.get('port')
        host = data.get('host', 'localhost')
        timeout = data.get('timeout', 5)
        
        if not port:
            return jsonify({'error': 'port is required'}), 400
        
        if not isinstance(port, int) or port < 1 or port > 65535:
            return jsonify({'error': 'port must be an integer between 1 and 65535'}), 400
        
        start_time = time.time()
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            if result == 0:
                return jsonify({
                    'status': 'healthy',
                    'response_time_ms': round(elapsed_ms, 2),
                    'port': port,
                    'host': host,
                })
            else:
                return jsonify({
                    'status': 'unhealthy',
                    'response_time_ms': round(elapsed_ms, 2),
                    'error': f'Connection refused (errno: {result})',
                    'port': port,
                    'host': host,
                })
                
        except socket.timeout:
            elapsed_ms = (time.time() - start_time) * 1000
            return jsonify({
                'status': 'unhealthy',
                'response_time_ms': round(elapsed_ms, 2),
                'error': f'Connection timed out after {timeout}s',
                'port': port,
                'host': host,
            })
        except socket.error as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return jsonify({
                'status': 'unhealthy',
                'response_time_ms': round(elapsed_ms, 2),
                'error': str(e),
                'port': port,
                'host': host,
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health/http', methods=['POST'])
@require_api_key
def health_check_http():
    """
    HTTP health check to internal port.
    
    Request body:
        {
            "port": 8000,
            "path": "/health",    # optional, defaults to /
            "host": "localhost",  # optional, defaults to localhost
            "timeout": 5,         # optional, defaults to 5 seconds
            "method": "GET"       # optional, defaults to GET
        }
    
    Response:
        {
            "status": "healthy" | "unhealthy",
            "status_code": 200,
            "response_time_ms": 45.2,
            "error": "..." (only if unhealthy)
        }
    """
    try:
        data = request.get_json() or {}
        port = data.get('port')
        path = data.get('path', '/')
        host = data.get('host', 'localhost')
        timeout = data.get('timeout', 5)
        method = data.get('method', 'GET').upper()
        
        if not port:
            return jsonify({'error': 'port is required'}), 400
        
        if not isinstance(port, int) or port < 1 or port > 65535:
            return jsonify({'error': 'port must be an integer between 1 and 65535'}), 400
        
        # Ensure path starts with /
        if not path.startswith('/'):
            path = '/' + path
        
        url = f'http://{host}:{port}{path}'
        start_time = time.time()
        
        try:
            req = urllib.request.Request(url, method=method)
            req.add_header('User-Agent', f'NodeAgent/{AGENT_VERSION}')
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status_code = resp.getcode()
                elapsed_ms = (time.time() - start_time) * 1000
                
                # 2xx is healthy
                if 200 <= status_code < 300:
                    return jsonify({
                        'status': 'healthy',
                        'status_code': status_code,
                        'response_time_ms': round(elapsed_ms, 2),
                        'url': url,
                    })
                else:
                    return jsonify({
                        'status': 'unhealthy',
                        'status_code': status_code,
                        'response_time_ms': round(elapsed_ms, 2),
                        'error': f'Non-2xx status code: {status_code}',
                        'url': url,
                    })
                    
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return jsonify({
                'status': 'unhealthy',
                'status_code': e.code,
                'response_time_ms': round(elapsed_ms, 2),
                'error': f'HTTP {e.code}: {e.reason}',
                'url': url,
            })
        except urllib.error.URLError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return jsonify({
                'status': 'unhealthy',
                'response_time_ms': round(elapsed_ms, 2),
                'error': f'Connection failed: {e.reason}',
                'url': url,
            })
        except socket.timeout:
            elapsed_ms = (time.time() - start_time) * 1000
            return jsonify({
                'status': 'unhealthy',
                'response_time_ms': round(elapsed_ms, 2),
                'error': f'Request timed out after {timeout}s',
                'url': url,
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<n>/restart', methods=['POST'])
@require_api_key
def restart_container(name):
    """Restart a container"""
    try:
        result = run_cmd(['docker', 'restart', name])
        if result.returncode == 0:
            return jsonify({'status': 'restarted', 'name': name})
        else:
            return jsonify({'error': result.stderr.strip() or f'Failed to restart: {name}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Cron/Scheduler Management
# =============================================================================

CRON_MARKER = "# DEPLOY_API_MANAGED"

# Security: Block dangerous volume mounts that could escape container sandbox
BLOCKED_VOLUME_PREFIXES = [
    '/',           # Root filesystem
    '/etc',        # System config
    '/var/run',    # Docker socket, runtime
    '/root',       # Root home
    '/home',       # User homes
    '/boot',       # Boot partition
    '/proc',       # Process info
    '/sys',        # System info
    '/dev',        # Devices
    '/lib',        # System libraries
    '/usr',        # System binaries
    '/bin',        # Binaries
    '/sbin',       # System binaries
]

def validate_volume_mounts(volumes):
    """
    Validate volume mounts for security.
    Returns (valid, error_message).
    """
    if not volumes:
        return True, None
    
    for vol in volumes:
        # Parse host:container or host:container:mode
        parts = vol.split(':')
        if len(parts) < 2:
            continue
        host_path = parts[0]
        
        # Normalize path
        host_path = host_path.rstrip('/')
        if not host_path:
            host_path = '/'
        
        # Check against blocked prefixes
        for blocked in BLOCKED_VOLUME_PREFIXES:
            if host_path == blocked or host_path.startswith(blocked + '/'):
                return False, f'Volume mount not allowed for security: {host_path}'
        
        # Block docker socket specifically
        if 'docker.sock' in host_path:
            return False, 'Docker socket mount not allowed'
    
    return True, None

@app.route('/cron/jobs', methods=['GET'])
@require_api_key
def list_cron_jobs():
    """List all managed cron jobs"""
    try:
        result = run_cmd(['crontab', '-l'])
        if result.returncode != 0:
            return jsonify({'jobs': [], 'raw': ''})
        
        jobs = []
        lines = result.stdout.strip().split('\\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Look for our marker comments
            if line.startswith(CRON_MARKER):
                # Parse marker: # DEPLOY_API_MANAGED:job_id:description
                parts = line.split(':', 2)
                job_id = parts[1] if len(parts) > 1 else 'unknown'
                description = parts[2] if len(parts) > 2 else ''
                # Next line is the actual cron entry
                if i + 1 < len(lines):
                    cron_line = lines[i + 1].strip()
                    if cron_line and not cron_line.startswith('#'):
                        # Parse cron: minute hour day month weekday command
                        cron_parts = cron_line.split(None, 5)
                        if len(cron_parts) >= 6:
                            jobs.append({
                                'id': job_id,
                                'description': description,
                                'schedule': ' '.join(cron_parts[:5]),
                                'command': cron_parts[5],
                                'raw': cron_line,
                            })
                    i += 1
            i += 1
        
        return jsonify({'jobs': jobs, 'count': len(jobs)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cron/remove', methods=['POST'])
@require_api_key
def remove_cron_job():
    """Remove a cron job by ID"""
    try:
        data = request.get_json() or {}
        job_id = data.get('id')
        
        if not job_id:
            return jsonify({'error': 'Missing required field: id'}), 400
        
        # Get current crontab
        result = run_cmd(['crontab', '-l'])
        if result.returncode != 0:
            return jsonify({'status': 'not_found', 'id': job_id})
        
        # Remove job with matching ID
        marker = f"{CRON_MARKER}:{job_id}:"
        new_lines = []
        lines = result.stdout.strip().split('\\n')
        found = False
        i = 0
        while i < len(lines):
            if marker in lines[i]:
                found = True
                i += 2  # Skip marker and cron line
                continue
            if lines[i].strip():
                new_lines.append(lines[i])
            i += 1
        
        if not found:
            return jsonify({'status': 'not_found', 'id': job_id})
        
        # Install updated crontab
        if new_lines:
            new_cron = '\\n'.join(new_lines) + '\\n'
            proc = subprocess.run(['crontab', '-'], input=new_cron, text=True, capture_output=True)
        else:
            # Remove crontab entirely if empty
            proc = subprocess.run(['crontab', '-r'], capture_output=True)
        
        if proc.returncode != 0 and 'no crontab' not in proc.stderr.lower():
            return jsonify({'error': f'Failed to update cron: {proc.stderr}'}), 500
        
        return jsonify({'status': 'removed', 'id': job_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cron/run-docker', methods=['POST'])
@require_api_key
def schedule_docker_run():
    """Schedule a Docker container to run on a schedule"""
    try:
        data = request.get_json() or {}
        job_id = data.get('id')
        schedule = data.get('schedule')  # e.g., "0 2 * * *"
        image = data.get('image')
        container_name = data.get('container_name', '')
        env_vars = data.get('env', {})
        volumes = data.get('volumes', [])
        network = data.get('network', '')
        command = data.get('command', '')
        description = data.get('description', '')
        
        if not job_id or not schedule or not image:
            return jsonify({'error': 'Missing required fields: id, schedule, image'}), 400
        
        # Security: Validate volume mounts
        valid, error = validate_volume_mounts(volumes)
        if not valid:
            return jsonify({'error': error}), 403
        
        # Build docker run command
        docker_cmd = ['docker', 'run', '--rm']
        
        if container_name:
            # Use timestamp to avoid conflicts
            docker_cmd.extend(['--name', f'{container_name}_$(date +%Y%m%d_%H%M%S)'])
        
        for key, val in env_vars.items():
            docker_cmd.extend(['-e', f'{key}={val}'])
        
        for vol in volumes:
            docker_cmd.extend(['-v', vol])
        
        if network:
            docker_cmd.extend(['--network', network])
        
        docker_cmd.append(image)
        
        if command:
            docker_cmd.extend(command.split())
        
        # Build full command with logging
        full_cmd = ' '.join(docker_cmd) + f' >> /var/log/cron_{job_id}.log 2>&1'
        
        # Use the add endpoint logic
        request_data = {
            'id': job_id,
            'schedule': schedule,
            'command': full_cmd,
            'description': description or f'Docker: {image}',
        }
        
        # Get current crontab
        result = run_cmd(['crontab', '-l'])
        current_cron = result.stdout if result.returncode == 0 else ''
        
        # Remove existing job with same ID
        marker = f"{CRON_MARKER}:{job_id}:"
        new_lines = []
        lines = current_cron.strip().split('\\n') if current_cron.strip() else []
        i = 0
        while i < len(lines):
            if marker in lines[i]:
                i += 2
                continue
            if lines[i].strip():
                new_lines.append(lines[i])
            i += 1
        
        # Add new job
        new_lines.append(f"{CRON_MARKER}:{job_id}:{request_data['description']}")
        new_lines.append(f"{schedule} {full_cmd}")
        
        # Install new crontab
        new_cron = '\\n'.join(new_lines) + '\\n'
        proc = subprocess.run(['crontab', '-'], input=new_cron, text=True, capture_output=True)
        
        if proc.returncode != 0:
            return jsonify({'error': f'Failed to install cron: {proc.stderr}'}), 500
        
        return jsonify({'status': 'scheduled', 'id': job_id, 'schedule': schedule, 'command': full_cmd})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<n>/logs', methods=['GET'])
@require_api_key
def get_logs(name):
    """Get container logs with diagnostic info for failed containers
    
    Query params:
        lines: Number of lines (default: 100)
        since: ISO timestamp or duration (e.g., "2024-01-01T00:00:00Z" or "5m")
    """
    try:
        lines = request.args.get('lines', '100')
        since = request.args.get('since')
        
        # Build docker logs command
        cmd = ['docker', 'logs', '--tail', lines]
        if since:
            cmd.extend(['--since', since])
        cmd.append(name)
        
        # Get logs
        result = run_cmd(cmd)
        if result.returncode != 0:
            return jsonify({'error': result.stderr.strip() or f'No such container: {name}'}), 404
        logs = result.stdout + result.stderr
        
        # Get container state for diagnostics (especially useful for failed containers)
        state_info = {}
        inspect_result = run_cmd([
            'docker', 'inspect', '--format',
            '{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}|{{.State.OOMKilled}}|{{.Config.Image}}|{{.State.StartedAt}}|{{.State.FinishedAt}}',
            name
        ])
        if inspect_result.returncode == 0:
            parts = inspect_result.stdout.strip().split('|')
            state_info = {
                'status': parts[0] if len(parts) > 0 else 'unknown',
                'exit_code': int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                'error': parts[2] if len(parts) > 2 and parts[2] else None,
                'oom_killed': parts[3].lower() == 'true' if len(parts) > 3 else False,
                'image': parts[4] if len(parts) > 4 else None,
                'started_at': parts[5] if len(parts) > 5 else None,
                'finished_at': parts[6] if len(parts) > 6 else None,
            }
        
        # Build response
        response = {'logs': logs}
        
        # Add diagnostics header for non-running containers
        if state_info.get('status') not in ('running', None):
            diagnostics = []
            diagnostics.append(f"Container Status: {state_info.get('status', 'unknown')}")
            if state_info.get('exit_code') is not None and state_info['exit_code'] != 0:
                diagnostics.append(f"Exit Code: {state_info['exit_code']}")
            if state_info.get('oom_killed'):
                diagnostics.append("⚠️ Container was killed due to Out Of Memory (OOM)")
            if state_info.get('error'):
                diagnostics.append(f"Error: {state_info['error']}")
            if state_info.get('image'):
                diagnostics.append(f"Image: {state_info['image']}")
            
            if diagnostics:
                header = "=== CONTAINER DIAGNOSTICS ===\\n" + "\\n".join(diagnostics) + "\\n" + "=" * 30 + "\\n\\n"
                response['logs'] = header + logs
        
        response['state'] = state_info
        return jsonify(response)
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


@app.route('/containers/<n>/inspect', methods=['GET'])
@require_api_key
def inspect_container(n):
    """Get full container inspection data (for recreating with same config)"""
    try:
        result = run_cmd(['docker', 'inspect', n])
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            if data and len(data) > 0:
                return jsonify(data[0])
            return jsonify({'error': 'No data returned'}), 404
        else:
            return jsonify({'error': 'Container not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# IMAGE OPERATIONS
# ========================================

@app.route('/images/pull', methods=['POST'])
@require_api_key
def pull_image():
    """Pull a Docker image, optionally with registry credentials"""
    try:
        data = request.get_json()
        image = data.get('image')
        if not image:
            return jsonify({'error': 'image required'}), 400
        
        # Optional registry auth
        registry = data.get('registry')  # e.g., registry.digitalocean.com
        username = data.get('username')
        password = data.get('password')  # Can be token for DO registry
        
        # Login if credentials provided
        if registry and username and password:
            login_cmd = ['docker', 'login', registry, '-u', username, '--password-stdin']
            login_result = subprocess.run(
                login_cmd,
                input=password.encode(),
                capture_output=True,
                timeout=30
            )
            if login_result.returncode != 0:
                return jsonify({
                    'status': 'error',
                    'error': f'Registry login failed: {login_result.stderr.decode()}'
                }), 500
        
        result = run_cmd(['docker', 'pull', image], timeout=600)
        
        if result.returncode == 0:
            # Get the image digest for precise rollback
            inspect_result = run_cmd(['docker', 'inspect', '--format', '{{.Id}}', image], timeout=30)
            digest = inspect_result.stdout.strip() if inspect_result.returncode == 0 else None
            return jsonify({'status': 'pulled', 'image': image, 'digest': digest})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/images/tag', methods=['POST'])
@require_api_key
def tag_image():
    """Tag an image for deployment history/rollback"""
    try:
        data = request.get_json()
        source = data.get('source')  # e.g., "myapp:latest" or image ID
        target = data.get('target')  # e.g., "myapp:deploy_abc123"
        
        if not source or not target:
            return jsonify({'error': 'source and target required'}), 400
        
        result = run_cmd(['docker', 'tag', source, target], timeout=30)
        
        if result.returncode == 0:
            return jsonify({'status': 'tagged', 'source': source, 'target': target})
        else:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/images/list', methods=['GET'])
@require_api_key
def list_images():
    """List Docker images, optionally filtered by prefix"""
    try:
        prefix = request.args.get('prefix', '')
        
        # Get all images in JSON format
        result = run_cmd(['docker', 'images', '--format', '{{json .}}'], timeout=30)
        
        if result.returncode != 0:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
        
        images = []
        for line in result.stdout.strip().split('\\n'):
            if not line:
                continue
            try:
                img = json.loads(line)
                repo_tag = f"{img.get('Repository', '')}:{img.get('Tag', '')}"
                # Filter by prefix if provided
                if prefix and not repo_tag.startswith(prefix):
                    continue
                images.append({
                    'repository': img.get('Repository'),
                    'tag': img.get('Tag'),
                    'id': img.get('ID'),
                    'created': img.get('CreatedAt'),
                    'size': img.get('Size'),
                })
            except json.JSONDecodeError:
                continue
        
        return jsonify({'status': 'ok', 'images': images, 'count': len(images)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/images/cleanup', methods=['POST'])
@require_api_key
def cleanup_images():
    """
    Cleanup old deployment images, keeping the last N.
    
    Request body:
        prefix: Image name prefix (e.g., "abc123_prod_api")
        keep: Number of recent images to keep (default: 20)
        tag_pattern: Tag pattern to match (default: "deploy_")
    
    This finds all images matching {prefix}:{tag_pattern}* and removes
    all but the most recent {keep} images.
    """
    try:
        data = request.get_json() or {}
        prefix = data.get('prefix')  # e.g., "abc123_prod_api"
        keep = int(data.get('keep', 20))
        tag_pattern = data.get('tag_pattern', 'deploy_')
        
        if not prefix:
            return jsonify({'error': 'prefix required'}), 400
        
        if keep < 1:
            return jsonify({'error': 'keep must be >= 1'}), 400
        
        # Get all images with this prefix
        result = run_cmd(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}} {{.CreatedAt}}', prefix], timeout=30)
        
        if result.returncode != 0:
            return jsonify({'status': 'error', 'error': result.stderr}), 500
        
        # Parse and filter deployment images
        deployment_images = []
        for line in result.stdout.strip().split('\\n'):
            if not line:
                continue
            parts = line.split(' ', 1)
            if len(parts) < 2:
                continue
            image_tag, created = parts[0], parts[1]
            
            # Check if this is a deployment tag
            if ':' in image_tag:
                repo, tag = image_tag.rsplit(':', 1)
                if tag.startswith(tag_pattern):
                    deployment_images.append({
                        'image': image_tag,
                        'tag': tag,
                        'created': created,
                    })
        
        # Sort by tag (deployment IDs are sortable by time if using timestamps or sequential)
        # Actually, sort by created date for safety
        deployment_images.sort(key=lambda x: x['created'], reverse=True)
        
        # Keep the most recent N, delete the rest
        to_delete = deployment_images[keep:]
        deleted = []
        errors = []
        
        for img in to_delete:
            del_result = run_cmd(['docker', 'rmi', img['image']], timeout=30)
            if del_result.returncode == 0:
                deleted.append(img['image'])
            else:
                # Image might be in use, that's OK
                errors.append({'image': img['image'], 'error': del_result.stderr})
        
        return jsonify({
            'status': 'ok',
            'found': len(deployment_images),
            'kept': min(keep, len(deployment_images)),
            'deleted': deleted,
            'deleted_count': len(deleted),
            'errors': errors,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/docker/login', methods=['POST'])
@require_api_key
def docker_login():
    """Login to a Docker registry"""
    try:
        data = request.get_json()
        registry = data.get('registry')  # e.g., registry.digitalocean.com
        username = data.get('username')
        password = data.get('password')
        
        if not all([registry, username, password]):
            return jsonify({'error': 'registry, username, and password required'}), 400
        
        login_cmd = ['docker', 'login', registry, '-u', username, '--password-stdin']
        result = subprocess.run(
            login_cmd,
            input=password.encode(),
            capture_output=True,
            timeout=30
        )
        
        if result.returncode == 0:
            return jsonify({'status': 'logged_in', 'registry': registry})
        else:
            return jsonify({
                'status': 'error',
                'error': result.stderr.decode()
            }), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/git/clone', methods=['POST'])
@require_api_key
def git_clone():
    """Clone a git repository with optional credentials"""
    try:
        data = request.get_json()
        repo_url = data.get('url')  # https://github.com/user/repo.git or git@github.com:user/repo.git
        branch = data.get('branch', 'main')
        target_path = data.get('target_path', '/app/')
        
        # Auth options
        token = data.get('token')  # GitHub/GitLab personal access token
        ssh_key = data.get('ssh_key')  # Private SSH key content
        
        if not repo_url:
            return jsonify({'error': 'url required'}), 400
        
        if not is_path_allowed(target_path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        # Clean target directory
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
        os.makedirs(target_path, exist_ok=True)
        
        env = os.environ.copy()
        
        # Handle HTTPS with token
        if token and repo_url.startswith('https://'):
            # Insert token into URL: https://token@github.com/user/repo.git
            if 'github.com' in repo_url:
                repo_url = repo_url.replace('https://github.com/', f'https://{token}@github.com/')
            elif 'gitlab.com' in repo_url:
                repo_url = repo_url.replace('https://gitlab.com/', f'https://oauth2:{token}@gitlab.com/')
            else:
                # Generic: https://user:token@host/repo
                repo_url = repo_url.replace('https://', f'https://git:{token}@')
        
        # Handle SSH key
        ssh_key_path = None
        if ssh_key:
            ssh_key_path = '/tmp/git_ssh_key'
            with open(ssh_key_path, 'w') as f:
                f.write(ssh_key)
            os.chmod(ssh_key_path, 0o600)
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_path} -o StrictHostKeyChecking=no'
        
        try:
            # Clone
            clone_cmd = ['git', 'clone', '--depth', '1', '--branch', branch, repo_url, target_path]
            result = run_cmd(clone_cmd, timeout=300)
            
            if result.returncode == 0:
                return jsonify({
                    'status': 'cloned',
                    'path': target_path,
                    'branch': branch
                })
            else:
                return jsonify({
                    'status': 'error',
                    'error': result.stderr
                }), 500
        finally:
            # Cleanup SSH key
            if ssh_key_path and os.path.exists(ssh_key_path):
                os.remove(ssh_key_path)
                
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


@app.route('/docker/dockerfile', methods=['POST'])
@require_api_key
def get_dockerfile():
    """Get or generate Dockerfile for preview/editing before build"""
    try:
        data = request.get_json()
        context_path = data.get('context_path', '/app/')
        
        if not is_path_allowed(context_path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        context = Path(context_path)
        if not context.exists():
            return jsonify({'error': f'Path does not exist: {context_path}'}), 400
        
        # List files for user reference
        files = [f.name for f in context.iterdir()][:30]
        subdirs = [f.name for f in context.iterdir() if f.is_dir()][:10]
        
        # Check if Dockerfile exists
        dockerfile_path = context / 'Dockerfile'
        if dockerfile_path.exists():
            return jsonify({
                'dockerfile': dockerfile_path.read_text(),
                'source': 'existing',
                'files': files,
                'subdirs': subdirs,
            })
        
        # Try to auto-generate
        generated = auto_generate_dockerfile(context_path)
        if generated:
            return jsonify({
                'dockerfile': generated,
                'source': 'generated',
                'files': files,
                'subdirs': subdirs,
            })
        
        # Return template - use python as sensible default
        template = """FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN echo "TODO: Add install command (e.g. pip install -r requirements.txt)"
EXPOSE 8000
CMD ["python", "main.py"]
"""
        return jsonify({
            'dockerfile': template,
            'source': 'template',
            'files': files,
            'subdirs': subdirs,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/docker/build', methods=['POST'])
@require_api_key
def build_image():
    """Build Docker image from uploaded code"""
    try:
        data = request.get_json()
        context_path = data.get('context_path', '/app/')
        image_tag = data.get('image_tag', 'app:latest')
        dockerfile = data.get('dockerfile')  # Required: Dockerfile content
        
        # Security: only allow building from allowed paths
        if not is_path_allowed(context_path):
            return jsonify({'error': 'Build context path not allowed'}), 403
        
        if not Path(context_path).exists():
            return jsonify({'error': f'Context path does not exist: {context_path}'}), 400
        
        if not dockerfile:
            return jsonify({'error': 'dockerfile content is required'}), 400
        
        # DEBUG: Log file timestamps and content before build
        print(f"[DEBUG] BUILD: context_path={context_path}, image_tag={image_tag}")
        context = Path(context_path)
        for html_file in context.rglob('*index*.html'):
            try:
                stat = html_file.stat()
                content = html_file.read_text(errors='ignore')
                import re
                title_match = re.search(r'<title>([^<]+)</title>', content, re.IGNORECASE)
                title = title_match.group(1) if title_match else "NO TITLE"
                print(f"[DEBUG] BUILD FILE: {html_file} mtime={stat.st_mtime} title={title}")
            except Exception as e:
                print(f"[DEBUG] BUILD FILE ERROR: {html_file}: {e}")
        
        # Write Dockerfile
        dockerfile_path = Path(context_path) / 'Dockerfile'
        dockerfile_path.write_text(dockerfile)
        print(f"[DEBUG] DOCKERFILE written to {dockerfile_path}")
        print("[DEBUG] DOCKERFILE content:")
        print(dockerfile[:500] + "...")
        
        # Build image (cache-friendly - Dockerfile structure handles cache busting)
        cmd = ['docker', 'build', '-t', image_tag, context_path]
        print(f"[DEBUG] BUILD CMD: {' '.join(cmd)}")
        result = run_cmd(cmd, timeout=600)  # 10 min timeout for builds
        
        # Log build output for debugging cache behavior
        print(f"[DEBUG] BUILD STDOUT (last 2000 chars): {result.stdout[-2000:] if result.stdout else 'empty'}")
        print(f"[DEBUG] BUILD STDERR (last 1000 chars): {result.stderr[-1000:] if result.stderr else 'empty'}")
        
        if result.returncode == 0:
            return jsonify({
                'status': 'built',
                'image_tag': image_tag,
                'output': result.stdout,
                'dockerfile': dockerfile
            })
        else:
            return jsonify({
                'status': 'error',
                'error': result.stderr,
                'output': result.stdout,
                'dockerfile': dockerfile
            }), 500
    except Exception as e:
        import traceback
        print(f"[DEBUG] BUILD EXCEPTION: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/docker/load', methods=['POST'])
@require_api_key
def load_image_tar():
    """Load Docker image from tar file (docker save output).
    
    Accepts either:
    - multipart/form-data with 'image_tar' file (preferred, streams to disk)
    - JSON with 'image_tar_b64' base64 encoded (legacy, high memory)
    """
    try:
        import tempfile
        
        # Check for multipart upload first (streaming, low memory)
        if 'image_tar' in request.files:
            tar_file = request.files['image_tar']
            with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as f:
                tar_path = f.name
                # Stream directly to disk - no memory spike
                tar_file.save(tar_path)
        else:
            # Legacy JSON/base64 method (high memory)
            data = request.get_json()
            image_tar_b64 = data.get('image_tar_b64')
            
            if not image_tar_b64:
                return jsonify({'error': 'image_tar or image_tar_b64 is required'}), 400
            
            # Decode and save to temp file
            image_tar = base64.b64decode(image_tar_b64)
            
            with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as f:
                f.write(image_tar)
                tar_path = f.name
        
        return _docker_load_from_path(tar_path)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/docker/load/stream', methods=['POST'])
@require_api_key
def load_image_tar_stream():
    """Load Docker image from streamed tar data.
    
    Accepts raw tar bytes in request body - enables true streaming
    from upstream without buffering entire file.
    
    Content-Type should be application/octet-stream or application/x-tar.
    """
    try:
        import tempfile
        
        # Stream request body directly to temp file
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as f:
            tar_path = f.name
            chunk_size = 64 * 1024  # 64KB chunks
            total = 0
            
            while True:
                chunk = request.stream.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        
        if total == 0:
            return jsonify({'error': 'No data received'}), 400
        
        return _docker_load_from_path(tar_path)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _docker_load_from_path(tar_path: str):
    """Common logic to load docker image from tar path."""
    try:
        # Load image
        cmd = ['docker', 'load', '-i', tar_path]
        result = run_cmd(cmd, timeout=600)
        
        if result.returncode == 0:
            # Parse loaded image name from output
            # Output is like "Loaded image: myapp:latest"
            loaded_image = None
            for line in result.stdout.splitlines():
                if 'Loaded image:' in line:
                    loaded_image = line.split('Loaded image:')[-1].strip()
                    break
            
            # Inspect image to get exposed port
            exposed_port = None
            if loaded_image:
                inspect_cmd = ['docker', 'inspect', '--format', '{{json .Config.ExposedPorts}}', loaded_image]
                inspect_result = run_cmd(inspect_cmd, timeout=10)
                if inspect_result.returncode == 0 and inspect_result.stdout.strip():
                    # Parse {"8000/tcp":{}} format
                    import json
                    try:
                        ports_data = json.loads(inspect_result.stdout.strip())
                        if ports_data:
                            # Get first exposed port
                            first_port = list(ports_data.keys())[0]  # "8000/tcp"
                            exposed_port = int(first_port.split('/')[0])
                    except:
                        pass
            
            return jsonify({
                'status': 'loaded',
                'image': loaded_image,
                'exposed_port': exposed_port,
                'output': result.stdout,
            })
        else:
            return jsonify({
                'status': 'error',
                'error': result.stderr,
                'output': result.stdout,
            }), 500
    finally:
        # Clean up temp file
        Path(tar_path).unlink(missing_ok=True)


def auto_generate_dockerfile(context_path: str) -> str:
    context = Path(context_path)
    
    # Check if local/base:latest exists (from custom snapshot)
    has_local_base = False
    try:
        result = subprocess.run(
            ['docker', 'images', '-q', 'local/base:latest'],
            capture_output=True, text=True, timeout=5
        )
        has_local_base = bool(result.stdout.strip())
    except:
        pass
    
    # Python project
    if (context / 'requirements.txt').exists():
        # Check for common entry points
        entry_point = None
        for candidate in ['main.py', 'app.py', 'api.py', 'server.py', 'run.py']:
            if (context / candidate).exists():
                entry_point = candidate
                break
        
        # Check for FastAPI/Flask in requirements
        requirements = (context / 'requirements.txt').read_text().lower()
        port = '8000' if 'fastapi' in requirements or 'uvicorn' in requirements else '5000'
        
        if entry_point:
            if 'uvicorn' in requirements:
                cmd = f'CMD ["uvicorn", "{entry_point[:-3]}:app", "--host", "0.0.0.0", "--port", "{port}"]'
            else:
                cmd = f'CMD ["python", "{entry_point}"]'
        else:
            cmd = 'CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]' if 'flask' in requirements else 'CMD ["python"]'
        
        base_image = 'local/base:latest' if has_local_base else 'python:3.11-slim'
        
        return f"""FROM {base_image}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE {port}
{cmd}
"""
    
    # Node.js project
    if (context / 'package.json').exists():
        import json
        try:
            pkg = json.loads((context / 'package.json').read_text())
            main = pkg.get('main', 'index.js')
            scripts = pkg.get('scripts', {})
            
            # Determine start command
            if 'start' in scripts:
                cmd = 'CMD ["npm", "start"]'
            else:
                cmd = f'CMD ["node", "{main}"]'
            
            # Check for yarn.lock
            use_yarn = (context / 'yarn.lock').exists()
            install_cmd = 'RUN yarn install --production' if use_yarn else 'RUN npm ci --only=production'
            copy_lock = 'COPY yarn.lock* ./' if use_yarn else 'COPY package-lock.json* ./'
            
        except:
            cmd = 'CMD ["npm", "start"]'
            install_cmd = 'RUN npm install --production'
            copy_lock = ''
        
        # Use local/base:latest if available (has node pre-installed), else node:20-alpine
        base_image = 'local/base:latest' if has_local_base else 'node:20-alpine'
        
        return f"""FROM {base_image}
WORKDIR /app
COPY package.json ./
{copy_lock}
{install_cmd}
COPY . .
EXPOSE 3000
{cmd}
"""
    
    # Go project
    if (context / 'go.mod').exists():
        return """FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o main .

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/main .
EXPOSE 8080
CMD ["./main"]
"""
    
    # Static site (index.html)
    if (context / 'index.html').exists():
        return """FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""
    
    return None


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
        if not is_path_allowed(path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        file_path.chmod(int(permissions, 8))
        
        return jsonify({'status': 'written', 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/files/mkdir', methods=['POST'])
@require_api_key
def make_directory():
    """Create directory with parents"""
    try:
        data = request.get_json()
        path = data.get('path')
        mode = data.get('mode', '755')
        
        if not path:
            return jsonify({'error': 'path required'}), 400
        
        # Security check
        if not is_path_allowed(path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        dir_path = Path(path)
        dir_path.mkdir(parents=True, exist_ok=True)
        dir_path.chmod(int(mode, 8))
        
        return jsonify({'status': 'created', 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/files/read', methods=['GET'])
@require_api_key
def read_file():
    """Read file contents"""
    try:
        path = request.args.get('path')
        
        if not path:
            return jsonify({'error': 'path required'}), 400
        
        # Security check
        if not is_path_allowed(path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        file_path = Path(path)
        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404
        
        content = file_path.read_text()
        return jsonify({'content': content, 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/files/exists', methods=['GET'])
@require_api_key
def file_exists():
    """Check if file exists"""
    try:
        path = request.args.get('path')
        
        if not path:
            return jsonify({'error': 'path required'}), 400
        
        file_path = Path(path)
        return jsonify({'exists': file_path.exists(), 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/files/delete', methods=['POST'])
@require_api_key
def delete_file():
    """Delete a file"""
    try:
        data = request.get_json()
        path = data.get('path')
        
        if not path:
            return jsonify({'error': 'path required'}), 400
        
        # Security check
        if not is_path_allowed(path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        file_path = Path(path)
        if file_path.exists():
            file_path.unlink()
            return jsonify({'status': 'deleted', 'path': path})
        else:
            return jsonify({'status': 'not_found', 'path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/containers/<n>/exec', methods=['POST'])
@require_api_key
def exec_in_container(n):
    """Execute command in running container"""
    try:
        data = request.get_json()
        command = data.get('command', [])
        
        if not command:
            return jsonify({'error': 'command required'}), 400
        
        # Build docker exec command
        cmd = ['docker', 'exec', n] + command
        result = run_cmd(cmd)
        
        return jsonify({
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# DOCKER NETWORKS
# ========================================

@app.route('/networks/create', methods=['POST'])
@require_api_key
def create_network():
    """Create Docker network"""
    try:
        data = request.get_json()
        name = data.get('name')
        
        if not name:
            return jsonify({'error': 'name required'}), 400
        
        result = run_cmd(['docker', 'network', 'create', name])
        if result.returncode == 0:
            return jsonify({'status': 'created', 'name': name})
        else:
            return jsonify({'error': result.stderr}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/networks/<n>', methods=['GET'])
@require_api_key
def get_network(n):
    """Get Docker network details"""
    try:
        result = run_cmd(['docker', 'network', 'inspect', n])
        if result.returncode == 0:
            return jsonify({'network': json.loads(result.stdout)})
        else:
            return jsonify({'error': 'Network not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload/tar', methods=['POST'])
@require_api_key
def upload_tar():
    """Upload and extract tar.gz archive.
    
    Accepts either:
    - multipart/form-data with 'tar_file' file (preferred, streams to disk)
    - JSON with 'data' base64 encoded (legacy, high memory)
    """
    try:
        # Check for multipart upload first (streaming, low memory)
        if 'tar_file' in request.files:
            tar_file = request.files['tar_file']
            extract_path = request.form.get('extract_path', '/tmp')
            clean = request.form.get('clean', 'true').lower() == 'true'
            
            # Security check
            if not is_path_allowed(extract_path):
                return jsonify({'error': 'Path not allowed'}), 403
            
            extract_dir = Path(extract_path)
            
            # Clean existing content if requested
            if clean and extract_dir.exists():
                import shutil
                shutil.rmtree(extract_dir)
            
            extract_dir.mkdir(parents=True, exist_ok=True)
            
            # Stream directly to tarfile - no memory spike
            with tarfile.open(fileobj=tar_file.stream, mode='r:gz') as tar:
                tar.extractall(extract_path)
            
            # DEBUG: Check for version in HTML files after extract
            for html_file in extract_dir.rglob('*index*.html'):
                try:
                    content = html_file.read_text(errors='ignore')
                    import re
                    title_match = re.search(r'<title>([^<]+)</title>', content, re.IGNORECASE)
                    if title_match:
                        print(f"[DEBUG] AGENT EXTRACT: {html_file} has title: {title_match.group(1)}")
                except:
                    pass
            
            return jsonify({'status': 'extracted', 'path': extract_path})
        
        # Legacy JSON/base64 method (high memory)
        data = request.get_json()
        tar_data = base64.b64decode(data.get('data', ''))
        extract_path = data.get('extract_path', '/tmp')
        clean = data.get('clean', True)  # Default: clean before extract
        
        # Security check
        if not is_path_allowed(extract_path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        extract_dir = Path(extract_path)
        
        # Clean existing content if requested
        if clean and extract_dir.exists():
            import shutil
            shutil.rmtree(extract_dir)
        
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        tar_buffer = io.BytesIO(tar_data)
        with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
            tar.extractall(extract_path)
        
        # DEBUG: Check for version in HTML files after extract
        for html_file in extract_dir.rglob('*index*.html'):
            try:
                content = html_file.read_text(errors='ignore')
                import re
                title_match = re.search(r'<title>([^<]+)</title>', content, re.IGNORECASE)
                if title_match:
                    print(f"[DEBUG] AGENT EXTRACT: {html_file} has title: {title_match.group(1)}")
            except:
                pass
        
        return jsonify({'status': 'extracted', 'path': extract_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload/tar/stream', methods=['POST'])
@require_api_key
def upload_tar_stream():
    """Upload and extract tar.gz archive from streamed data.
    
    Accepts raw tar.gz bytes in request body with extract_path in query params.
    Enables true streaming from upstream without buffering.
    """
    try:
        extract_path = request.args.get('extract_path', '/app/')
        clean = request.args.get('clean', 'true').lower() == 'true'
        
        # Security check
        if not is_path_allowed(extract_path):
            return jsonify({'error': 'Path not allowed'}), 403
        
        extract_dir = Path(extract_path)
        
        # Clean existing content if requested
        if clean and extract_dir.exists():
            import shutil
            shutil.rmtree(extract_dir)
        
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        # Stream directly to tarfile extraction
        with tarfile.open(fileobj=request.stream, mode='r:gz') as tar:
            tar.extractall(extract_path)
        
        return jsonify({'status': 'extracted', 'path': extract_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500# Chunked upload state
_upload_chunks = {}


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
        if not is_path_allowed(extract_path):
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
    """Reload nginx configuration (works with both Docker and systemctl)"""
    try:
        # Try Docker first - use docker inspect for reliable check
        docker_check = run_cmd(['docker', 'inspect', '-f', '{{.State.Running}}', 'nginx'])
        if docker_check.returncode == 0 and docker_check.stdout.strip() == 'true':
            # Nginx running in container
            test = run_cmd(['docker', 'exec', 'nginx', 'nginx', '-t'])
            if test.returncode != 0:
                return jsonify({'status': 'error', 'error': test.stderr}), 400
            
            result = run_cmd(['docker', 'exec', 'nginx', 'nginx', '-s', 'reload'])
            return jsonify({
                'status': 'reloaded' if result.returncode == 0 else 'failed',
                'mode': 'docker'
            })
        
        # Fallback to systemctl (nginx on host)
        test = run_cmd(['nginx', '-t'])
        if test.returncode != 0:
            return jsonify({'status': 'error', 'error': test.stderr}), 400
        
        result = run_cmd(['systemctl', 'reload', 'nginx'])
        return jsonify({
            'status': 'reloaded' if result.returncode == 0 else 'failed',
            'mode': 'systemctl'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/nginx/test', methods=['GET'])
@require_api_key
def test_nginx_config():
    """Test nginx configuration"""
    try:
        # Try Docker first - use docker inspect for reliable check
        docker_check = run_cmd(['docker', 'inspect', '-f', '{{.State.Running}}', 'nginx'])
        if docker_check.returncode == 0 and docker_check.stdout.strip() == 'true':
            result = run_cmd(['docker', 'exec', 'nginx', 'nginx', '-t'])
        else:
            result = run_cmd(['nginx', '-t'])
        
        return jsonify({
            'valid': result.returncode == 0,
            'output': result.stderr or result.stdout,
        })
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
    import gzip
    import base64
    
    # Compress the agent code to fit within cloud-init 64KB limit
    compressed = gzip.compress(NODE_AGENT_CODE.encode('utf-8'))
    b64_agent = base64.b64encode(compressed).decode('ascii')
    
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

# Write node agent script (compressed to fit cloud-init limit)
log 'Decompressing and writing node agent script...'
echo '{b64_agent}' | base64 -d | gunzip > /usr/local/bin/node_agent.py

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

# Configure firewall - allow agent port only (SSH is configured by cloud-init)
log 'Configuring firewall for node agent...'
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
