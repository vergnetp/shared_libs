"""
Kubernetes platform implementation for container operations
"""

import subprocess
import tempfile
import os
import yaml
import base64
import time
from typing import Dict, List, Any
from .base import ContainerRuntime, TemplateEngine, SecretHandler, PlatformCapabilities


class KubernetesRuntime(ContainerRuntime):
    """Kubernetes container runtime implementation"""
    
    def __init__(self):
        self.capabilities = PlatformCapabilities("kubernetes")
        self.capabilities.supports_secrets = True
        self.capabilities.supports_networking = True
        self.capabilities.supports_volumes = True
        self.capabilities.supports_health_checks = True
        self.capabilities.supports_auto_scaling = True
        self.capabilities.supports_rolling_updates = True
    
    def get_platform_name(self) -> str:
        return "kubernetes"
    
    def build_image(self, image_name: str, containerfile_path: str, build_context: str, 
                   build_args: Dict[str, str] = None) -> bool:
        """Build image using buildah or kaniko for Kubernetes"""
        try:
            # Try buildah first
            cmd = [
                'buildah', 'build',
                '-t', image_name,
                '-f', containerfile_path,
                build_context
            ]
            
            # Add build arguments if provided
            if build_args:
                for key, value in build_args.items():
                    cmd.extend(['--build-arg', f'{key}={value}'])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode == 0:
                print(f"Successfully built Kubernetes image with buildah: {image_name}")
                return True
            else:
                # Fallback to docker if buildah is not available
                print(f"Buildah failed, falling back to docker: {result.stderr}")
                return self._build_with_docker(image_name, containerfile_path, build_context, build_args)
                
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("Buildah not available, falling back to docker")
            return self._build_with_docker(image_name, containerfile_path, build_context, build_args)
        except Exception as e:
            print(f"Error building Kubernetes image: {e}")
            return False
    
    def _build_with_docker(self, image_name: str, containerfile_path: str, build_context: str, 
                          build_args: Dict[str, str] = None) -> bool:
        """Fallback to Docker for image building"""
        try:
            cmd = [
                'docker', 'build',
                '-t', image_name,
                '-f', containerfile_path,
                build_context
            ]
            
            if build_args:
                for key, value in build_args.items():
                    cmd.extend(['--build-arg', f'{key}={value}'])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode == 0:
                print(f"Successfully built Kubernetes image with docker: {image_name}")
                return True
            else:
                print(f"Docker build failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error building with docker: {e}")
            return False
    
    def deploy_service(self, config_file: str, working_dir: str = "/opt/app") -> bool:
        """Deploy service using kubectl"""
        try:
            result = subprocess.run([
                'kubectl', 'apply', '-f', config_file
            ], cwd=working_dir, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                print(f"Successfully deployed Kubernetes service from {config_file}")
                return True
            else:
                print(f"Kubernetes deployment failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"Kubernetes deployment timed out for {config_file}")
            return False
        except Exception as e:
            print(f"Error deploying Kubernetes service: {e}")
            return False
    
    def check_service_status(self, service_name: str) -> str:
        """Check Kubernetes deployment status"""
        try:
            result = subprocess.run([
                'kubectl', 'get', 'deployment', service_name,
                '--no-headers', '-o', 'custom-columns=:status.readyReplicas/:spec.replicas'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                if not output or output == "<none>":
                    return "not_found"
                
                # Parse ready/total replicas
                try:
                    if '/' in output:
                        ready, total = output.split('/')
                        ready = int(ready) if ready != "<none>" else 0
                        total = int(total)
                        
                        if ready == total and ready > 0:
                            return "running"
                        elif ready > 0:
                            return "partially_running"
                        else:
                            return "stopped"
                    else:
                        return "unknown"
                except (ValueError, IndexError):
                    return "error"
            else:
                return "not_found"
                
        except Exception as e:
            print(f"Error checking Kubernetes service status: {e}")
            return "error"
    
    def get_service_logs(self, service_name: str, lines: int = 100) -> str:
        """Get Kubernetes pod logs"""
        try:
            result = subprocess.run([
                'kubectl', 'logs', f'deployment/{service_name}',
                '--tail', str(lines)
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error getting logs: {result.stderr}"
                
        except Exception as e:
            return f"Error getting Kubernetes logs: {e}"
    
    def stop_service(self, service_name: str) -> bool:
        """Scale Kubernetes deployment to 0"""
        try:
            result = subprocess.run([
                'kubectl', 'scale', 'deployment', service_name, '--replicas=0'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully stopped Kubernetes service: {service_name}")
                return True
            else:
                print(f"Failed to stop Kubernetes service: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error stopping Kubernetes service: {e}")
            return False
    
    def remove_service(self, service_name: str) -> bool:
        """Remove Kubernetes deployment and associated resources"""
        try:
            # Remove deployment
            result = subprocess.run([
                'kubectl', 'delete', 'deployment', service_name
            ], capture_output=True, text=True)
            
            deployment_removed = result.returncode == 0
            
            # Remove service if it exists
            service_result = subprocess.run([
                'kubectl', 'delete', 'service', f'{service_name}-service'
            ], capture_output=True, text=True)
            
            if deployment_removed:
                print(f"Successfully removed Kubernetes deployment: {service_name}")
                if service_result.returncode == 0:
                    print(f"Successfully removed Kubernetes service: {service_name}-service")
                return True
            else:
                print(f"Failed to remove Kubernetes deployment: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error removing Kubernetes service: {e}")
            return False
    
    def restart_service(self, service_name: str) -> bool:
        """Restart Kubernetes deployment"""
        try:
            result = subprocess.run([
                'kubectl', 'rollout', 'restart', 'deployment', service_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully restarted Kubernetes service: {service_name}")
                return True
            else:
                print(f"Failed to restart Kubernetes service: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error restarting Kubernetes service: {e}")
            return False
    
    def get_deploy_command(self, config_file: str) -> str:
        """Get Kubernetes deployment command"""
        return f"kubectl apply -f {config_file}"


class KubernetesTemplateEngine(TemplateEngine):
    """Kubernetes YAML template engine"""
    
    def get_platform_name(self) -> str:
        return "kubernetes"
    
    def get_config_file_extension(self) -> str:
        return "yaml"
    
    def supports_secrets(self) -> bool:
        return True
    
    def supports_networking(self) -> bool:
        return True
    
    def generate_deployment_config(self, context: Dict[str, Any]) -> str:
        """Generate Kubernetes deployment configuration"""
        
        service_name = context['service_name']
        image_name = context['image_name']
        project = context['project']
        environment = context['environment']
        replica_count = context.get('replica_count', 1)
        is_worker = context.get('is_worker', False)
        namespace = f"{project}-{environment}"
        
        # Generate namespace
        namespace_manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "project": project,
                    "environment": environment,
                    "managed-by": "personal-cloud-orchestrator"
                }
            }
        }
        
        # Generate deployment
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "project": project,
                    "environment": environment,
                    "service-type": context.get('service_type', 'web')
                }
            },
            "spec": {
                "replicas": replica_count,
                "selector": {
                    "matchLabels": {
                        "app": service_name
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": service_name,
                            "project": project,
                            "environment": environment
                        }
                    },
                    "spec": {
                        "containers": [{
                            "name": service_name,
                            "image": image_name,
                            "env": self._generate_env_vars(context),
                            "resources": self._generate_resources(context)
                        }],
                        "restartPolicy": "Always"
                    }
                }
            }
        }
        
        # Add command for workers
        if context.get('command'):
            deployment["spec"]["template"]["spec"]["containers"][0]["command"] = [context['command']]
        
        # Add ports and health checks for web services
        if not is_worker and 'SERVICE_PORT' in context:
            port = context['SERVICE_PORT']
            container = deployment["spec"]["template"]["spec"]["containers"][0]
            
            container["ports"] = [{
                "containerPort": port,
                "name": "http"
            }]
            
            # Add health checks
            container["livenessProbe"] = {
                "httpGet": {
                    "path": "/health",
                    "port": port
                },
                "initialDelaySeconds": 30,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 3
            }
            
            container["readinessProbe"] = {
                "httpGet": {
                    "path": "/health", 
                    "port": port
                },
                "initialDelaySeconds": 5,
                "periodSeconds": 5,
                "timeoutSeconds": 3,
                "failureThreshold": 3
            }
        
        config_parts = [namespace_manifest, deployment]
        
        # Add service for web services
        if not is_worker and 'SERVICE_PORT' in context:
            service = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": f"{service_name}-service",
                    "namespace": namespace,
                    "labels": {
                        "app": service_name
                    }
                },
                "spec": {
                    "selector": {
                        "app": service_name
                    },
                    "ports": [{
                        "name": "http",
                        "port": 80,
                        "targetPort": context['SERVICE_PORT'],
                        "protocol": "TCP"
                    }],
                    "type": "ClusterIP"
                }
            }
            config_parts.append(service)
            
            # Add ingress if specified
            if context.get('enable_ingress', False):
                ingress = self._generate_ingress(context, namespace)
                if ingress:
                    config_parts.append(ingress)
        
        # Convert to YAML
        return "\n---\n".join([yaml.dump(part, default_flow_style=False) for part in config_parts])
    
    def _generate_env_vars(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate environment variables for Kubernetes"""
        env_vars = []
        
        # Standard environment variables
        standard_vars = [
            'DB_USER', 'DB_NAME', 'DB_HOST', 'DB_PORT', 
            'REDIS_HOST', 'REDIS_PORT',
            'VAULT_HOST', 'VAULT_PORT', 
            'OPENSEARCH_HOST', 'OPENSEARCH_PORT', 'OPENSEARCH_INDEX',
            'SERVICE_NAME', 'ENVIRONMENT', 'PROJECT', 'RESOURCE_HASH'
        ]
        
        for var in standard_vars:
            if var in context:
                env_vars.append({
                    "name": var,
                    "value": str(context[var])
                })
        
        # Add SERVICE_PORT for web services
        if not context.get('is_worker') and 'SERVICE_PORT' in context:
            env_vars.append({
                "name": "SERVICE_PORT",
                "value": str(context['SERVICE_PORT'])
            })
        
        # Add secrets from Kubernetes secrets
        secrets_config = context.get('secrets_config', {})
        if secrets_config.get('type') == 'kubernetes_secret':
            secret_name = secrets_config.get('secret_name')
            for secret_key in secrets_config.get('secret_keys', []):
                env_vars.append({
                    "name": secret_key.upper(),
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_name,
                            "key": secret_key
                        }
                    }
                })
        
        return env_vars
    
    def _generate_resources(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate resource constraints"""
        resources = context.get('resources', {})
        
        default_resources = {
            "requests": {
                "memory": "128Mi",
                "cpu": "100m"
            },
            "limits": {
                "memory": "512Mi", 
                "cpu": "500m"
            }
        }
        
        # Merge with provided resources
        if resources:
            if 'requests' in resources:
                default_resources['requests'].update(resources['requests'])
            if 'limits' in resources:
                default_resources['limits'].update(resources['limits'])
        
        return default_resources
    
    def _generate_ingress(self, context: Dict[str, Any], namespace: str) -> Dict[str, Any]:
        """Generate ingress configuration"""
        service_name = context['service_name']
        project = context['project']
        environment = context['environment']
        service_type = context.get('service_type', 'web')
        
        # Generate hostname
        hostname = f"{project}-{environment}-{service_type}.yourdomain.com"
        
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{service_name}-ingress",
                "namespace": namespace,
                "annotations": {
                    "nginx.ingress.kubernetes.io/rewrite-target": "/",
                    "nginx.ingress.kubernetes.io/ssl-redirect": "true"
                }
            },
            "spec": {
                "ingressClassName": "nginx",
                "rules": [{
                    "host": hostname,
                    "http": {
                        "paths": [{
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": f"{service_name}-service",
                                    "port": {
                                        "number": 80
                                    }
                                }
                            }
                        }]
                    }
                }],
                "tls": [{
                    "hosts": [hostname],
                    "secretName": f"{service_name}-tls"
                }]
            }
        }
    
    def get_health_check_url(self, service_name: str, host: str, port: int) -> str:
        """Generate health check URL for Kubernetes service"""
        return f"http://{service_name}-service/health"


class KubernetesSecretHandler(SecretHandler):
    """Kubernetes secret handler implementation"""
    
    def __init__(self, secret_manager):
        self.secret_manager = secret_manager
    
    def get_platform_name(self) -> str:
        return "kubernetes"
    
    def create_secrets(self, project: str, environment: str, secrets: Dict[str, str]) -> List[str]:
        """Create Kubernetes secrets"""
        secret_name = f"{project}-{environment}-secrets"
        namespace = f"{project}-{environment}"
        
        if not secrets:
            print(f"No secrets to create for {project}-{environment}")
            return []
        
        # Base64 encode secrets
        secret_data = {}
        for key, value in secrets.items():
            encoded_value = base64.b64encode(value.encode()).decode()
            secret_data[key] = encoded_value
        
        secret_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "labels": {
                    "project": project,
                    "environment": environment,
                    "managed-by": "personal-cloud-orchestrator"
                }
            },
            "type": "Opaque",
            "data": secret_data
        }
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                yaml.dump(secret_manifest, f)
                temp_path = f.name
            
            # Ensure namespace exists
            self._ensure_namespace(namespace, project, environment)
            
            # Apply the secret
            result = subprocess.run([
                'kubectl', 'apply', '-f', temp_path
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Created Kubernetes secret: {secret_name}")
                return [secret_name]
            else:
                print(f"Failed to create Kubernetes secret: {result.stderr}")
                return []
                
        except Exception as e:
            print(f"Error creating Kubernetes secret: {e}")
            return []
        finally:
            if 'temp_path' in locals():
                os.unlink(temp_path)
    
    def _ensure_namespace(self, namespace: str, project: str, environment: str) -> bool:
        """Ensure Kubernetes namespace exists"""
        try:
            check_result = subprocess.run([
                'kubectl', 'get', 'namespace', namespace
            ], capture_output=True)
            
            if check_result.returncode != 0:
                # Create namespace with labels
                namespace_manifest = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {
                            "project": project,
                            "environment": environment,
                            "managed-by": "personal-cloud-orchestrator"
                        }
                    }
                }
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    yaml.dump(namespace_manifest, f)
                    temp_path = f.name
                
                result = subprocess.run([
                    'kubectl', 'apply', '-f', temp_path
                ], capture_output=True, text=True)
                
                os.unlink(temp_path)
                
                if result.returncode == 0:
                    print(f"Created Kubernetes namespace: {namespace}")
                    return True
                else:
                    print(f"Failed to create namespace {namespace}: {result.stderr}")
                    return False
            
            return True
            
        except Exception as e:
            print(f"Error ensuring namespace {namespace}: {e}")
            return False
    
    def remove_secret(self, secret_name: str, namespace: str = None, **kwargs) -> bool:
        """Remove a Kubernetes secret"""
        try:
            cmd = ['kubectl', 'delete', 'secret', secret_name]
            if namespace:
                cmd.extend(['-n', namespace])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Removed Kubernetes secret: {secret_name}")
                return True
            else:
                print(f"Failed to remove Kubernetes secret: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error removing Kubernetes secret: {e}")
            return False
    
    def list_secrets(self, namespace: str = None, **kwargs) -> List[str]:
        """List all Kubernetes secrets"""
        try:
            cmd = ['kubectl', 'get', 'secrets', '--no-headers', '-o', 'custom-columns=:metadata.name']
            if namespace:
                cmd.extend(['-n', namespace])
            else:
                cmd.append('--all-namespaces')
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                secrets = [line.strip() for line in result.stdout.split('\n') if line.strip()]
                # Filter out default Kubernetes secrets
                return [s for s in secrets if not s.startswith('default-token')]
            else:
                print(f"Failed to list Kubernetes secrets: {result.stderr}")
                return []
                
        except Exception as e:
            print(f"Error listing Kubernetes secrets: {e}")
            return []
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all Kubernetes secrets for a project/environment"""
        namespace = f"{project}-{environment}"
        secret_name = f"{project}-{environment}-secrets"
        
        if self.remove_secret(secret_name, namespace=namespace):
            print(f"Cleaned up Kubernetes secrets for {project}-{environment}")
            return 1
        return 0
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all Kubernetes secrets for a project/environment"""
        return [f"{project}-{environment}-secrets"]
    
    def validate_secret_availability(self, secret_name: str, namespace: str = None) -> bool:
        """Validate that a Kubernetes secret exists and is accessible"""
        try:
            cmd = ['kubectl', 'get', 'secret', secret_name]
            if namespace:
                cmd.extend(['-n', namespace])
            
            result = subprocess.run(cmd, capture_output=True)
            return result.returncode == 0
            
        except Exception:
            return False