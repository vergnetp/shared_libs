"""
Image Builder - Docker image building and management.

Handles:
- Building from Dockerfile
- Building from git repos
- Multi-stage builds
- Registry push
- Image tagging and versioning
"""

from __future__ import annotations
import os
import shutil
import tempfile
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..core.service import Service

from ..core.result import Result, BuildResult
from ..docker.client import DockerClient


@dataclass
class BuildConfig:
    """Configuration for an image build."""
    name: str
    tag: str = "latest"
    dockerfile: str = "Dockerfile"
    context: str = "."
    build_args: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    target: Optional[str] = None  # Multi-stage target
    no_cache: bool = False
    pull: bool = False  # Pull base image before build
    
    @property
    def full_tag(self) -> str:
        return f"{self.name}:{self.tag}"


class ImageBuilder:
    """
    Docker image builder.
    
    Usage:
        builder = ImageBuilder(ctx)
        
        # Build from Dockerfile
        result = builder.build(
            name="myapp/api",
            dockerfile="./Dockerfile",
            context=".",
        )
        
        # Build service
        result = builder.build_service(service)
        
        # Build and push
        result = builder.build_and_push(service, registry="docker.io")
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        docker: Optional[DockerClient] = None,
    ):
        self.ctx = ctx
        self.docker = docker or DockerClient(ctx)
        self._temp_dirs: List[str] = []
    
    def __del__(self):
        """Cleanup temp directories."""
        self.cleanup()
    
    def cleanup(self):
        """Remove temporary build directories."""
        for temp_dir in self._temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception:
                pass
        self._temp_dirs.clear()
    
    # =========================================================================
    # Building
    # =========================================================================
    
    def build(
        self,
        name: str,
        tag: str = "latest",
        dockerfile: str = "Dockerfile",
        context: str = ".",
        build_args: Optional[Dict[str, str]] = None,
        labels: Optional[Dict[str, str]] = None,
        no_cache: bool = False,
    ) -> BuildResult:
        """
        Build Docker image.
        
        Args:
            name: Image name
            tag: Image tag
            dockerfile: Path to Dockerfile
            context: Build context directory
            build_args: Build arguments
            labels: Image labels
            no_cache: Disable build cache
            
        Returns:
            BuildResult
        """
        start = datetime.utcnow()
        full_tag = f"{name}:{tag}"
        
        self.ctx.log_info(f"Building {full_tag}", context=context)
        
        # Add standard labels
        all_labels = {
            "project": self.ctx.project_name,
            "env": self.ctx.env,
            "user": self.ctx.user_id,
            "built_at": start.isoformat(),
        }
        if labels:
            all_labels.update(labels)
        
        # Build
        result = self.docker.build(
            tag=full_tag,
            dockerfile=dockerfile,
            context=context,
            build_args=build_args,
            no_cache=no_cache,
        )
        
        elapsed = (datetime.utcnow() - start).total_seconds()
        
        if result.success:
            self.ctx.log_info(f"Built {full_tag}", duration=f"{elapsed:.1f}s")
            return BuildResult(
                success=True,
                message=f"Built {full_tag}",
                image_name=name,
                image_tag=tag,
                build_time_seconds=elapsed,
            )
        else:
            self.ctx.log_error(f"Build failed: {result.error}")
            return BuildResult(
                success=False,
                error=result.error,
                image_name=name,
                image_tag=tag,
                build_time_seconds=elapsed,
            )
    
    def build_service(
        self,
        service: 'Service',
        tag: Optional[str] = None,
        no_cache: bool = False,
    ) -> BuildResult:
        """
        Build image for a service.
        
        Handles:
        - Custom Dockerfile content
        - Git repo sources
        - Auto-generated Dockerfiles
        
        Args:
            service: Service definition
            tag: Override tag (default: latest or from context)
            no_cache: Disable build cache
            
        Returns:
            BuildResult
        """
        tag = tag or "latest"
        image_name = self.ctx.image_name(service.name, tag="").rstrip(":")
        
        # Determine build strategy
        if service.image and not service.needs_build:
            # Pre-built image, no build needed
            return BuildResult(
                success=True,
                message=f"Using pre-built image: {service.image}",
                image_name=service.image.split(":")[0],
                image_tag=service.image.split(":")[-1] if ":" in service.image else "latest",
            )
        
        # Prepare build context
        context_path, dockerfile_path = self._prepare_build_context(service)
        
        if not context_path or not dockerfile_path:
            return BuildResult(
                success=False,
                error="Failed to prepare build context",
                image_name=image_name,
            )
        
        return self.build(
            name=image_name,
            tag=tag,
            dockerfile=dockerfile_path,
            context=context_path,
            build_args=service.environment,  # Pass env as build args
            labels={
                "service": service.name,
                "type": service.type.value,
            },
            no_cache=no_cache,
        )
    
    def _prepare_build_context(
        self, 
        service: 'Service',
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Prepare build context for a service.
        
        Returns:
            Tuple of (context_path, dockerfile_path)
        """
        # If git repo specified, clone it
        if service.git_repo:
            context_path = self._clone_repo(
                service.git_repo,
                service.git_branch,
            )
            if not context_path:
                return None, None
            
            dockerfile_path = os.path.join(context_path, "Dockerfile")
            
            # If no Dockerfile in repo, generate one
            if not os.path.exists(dockerfile_path):
                content = self._generate_dockerfile(service)
                if content:
                    with open(dockerfile_path, "w") as f:
                        f.write(content)
            
            return context_path, dockerfile_path
        
        # If dockerfile content provided, write it
        if service.dockerfile:
            if os.path.isfile(service.dockerfile):
                # It's a path
                context_path = service.build_context or os.path.dirname(service.dockerfile)
                return context_path, service.dockerfile
            else:
                # It's content
                temp_dir = tempfile.mkdtemp(prefix=f"build_{service.name}_")
                self._temp_dirs.append(temp_dir)
                
                dockerfile_path = os.path.join(temp_dir, "Dockerfile")
                with open(dockerfile_path, "w") as f:
                    f.write(service.dockerfile)
                
                return service.build_context or temp_dir, dockerfile_path
        
        # Auto-generate Dockerfile
        content = self._generate_dockerfile(service)
        if content:
            temp_dir = tempfile.mkdtemp(prefix=f"build_{service.name}_")
            self._temp_dirs.append(temp_dir)
            
            dockerfile_path = os.path.join(temp_dir, "Dockerfile")
            with open(dockerfile_path, "w") as f:
                f.write(content)
            
            return service.build_context or temp_dir, dockerfile_path
        
        return None, None
    
    def _clone_repo(
        self,
        repo_url: str,
        branch: Optional[str] = None,
    ) -> Optional[str]:
        """Clone git repository to temp directory."""
        import subprocess
        
        # Parse repo URL (might include @branch)
        if "@" in repo_url and not repo_url.startswith("git@"):
            url, ref = repo_url.rsplit("@", 1)
        else:
            url = repo_url
            ref = branch or "main"
        
        temp_dir = tempfile.mkdtemp(prefix="git_clone_")
        self._temp_dirs.append(temp_dir)
        
        try:
            # Clone
            cmd = ["git", "clone", "--depth", "1", "--branch", ref, url, temp_dir]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                self.ctx.log_error(f"Git clone failed: {result.stderr}")
                return None
            
            return temp_dir
            
        except Exception as e:
            self.ctx.log_error(f"Git clone error: {e}")
            return None
    
    def _generate_dockerfile(self, service: 'Service') -> Optional[str]:
        """Generate Dockerfile based on service type."""
        from ..core.service import ServiceType
        
        if service.type == ServiceType.PYTHON:
            return f"""FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run
EXPOSE {service.main_port or 8000}
CMD {service.command or '["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]'}
"""
        
        elif service.type == ServiceType.NODE:
            return f"""FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci --only=production

# Copy application
COPY . .

# Run
EXPOSE {service.main_port or 3000}
CMD {service.command or '["node", "index.js"]'}
"""
        
        elif service.type == ServiceType.REACT:
            return """FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production image
FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf 2>/dev/null || true

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""
        
        return None
    
    # =========================================================================
    # Push
    # =========================================================================
    
    def push(
        self,
        name: str,
        tag: str = "latest",
        registry: Optional[str] = None,
    ) -> Result:
        """
        Push image to registry.
        
        Args:
            name: Image name
            tag: Image tag
            registry: Registry URL (default: Docker Hub)
            
        Returns:
            Result
        """
        full_tag = f"{name}:{tag}"
        
        # Tag for registry if needed
        if registry:
            registry_tag = f"{registry}/{full_tag}"
            tag_result = self.docker.tag(full_tag, registry_tag)
            if not tag_result.success:
                return tag_result
            full_tag = registry_tag
        
        self.ctx.log_info(f"Pushing {full_tag}")
        return self.docker.push(full_tag)
    
    def build_and_push(
        self,
        service: 'Service',
        tag: Optional[str] = None,
        registry: Optional[str] = None,
        no_cache: bool = False,
    ) -> BuildResult:
        """
        Build and push service image.
        
        Args:
            service: Service definition
            tag: Override tag
            registry: Registry URL
            no_cache: Disable build cache
            
        Returns:
            BuildResult with pushed=True on success
        """
        # Build
        result = self.build_service(service, tag=tag, no_cache=no_cache)
        
        if not result.success:
            return result
        
        # Push
        push_result = self.push(
            result.image_name,
            result.image_tag,
            registry=registry,
        )
        
        if push_result.success:
            result.pushed = True
            result.message = f"Built and pushed {result.image_name}:{result.image_tag}"
        else:
            result.success = False
            result.error = f"Push failed: {push_result.error}"
        
        return result
    
    # =========================================================================
    # Versioning
    # =========================================================================
    
    def generate_tag(
        self,
        service: 'Service',
        strategy: str = "git",
    ) -> str:
        """
        Generate image tag.
        
        Strategies:
        - "latest": Always use "latest"
        - "git": Use git commit hash
        - "timestamp": Use timestamp
        - "semver": Use semantic version from service config
        
        Args:
            service: Service definition
            strategy: Tagging strategy
            
        Returns:
            Tag string
        """
        if strategy == "latest":
            return "latest"
        
        elif strategy == "git":
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
            return "latest"
        
        elif strategy == "timestamp":
            return datetime.utcnow().strftime("%Y%m%d%H%M%S")
        
        elif strategy == "semver":
            # Look for version in service config
            return service.labels.get("version", "1.0.0")
        
        else:
            return "latest"
    
    def content_hash(self, context_path: str) -> str:
        """
        Generate hash of build context for cache-busting.
        
        Returns:
            Short hash string
        """
        hasher = hashlib.md5()
        
        for root, dirs, files in os.walk(context_path):
            # Skip hidden and common cache dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and d != "node_modules"]
            
            for filename in sorted(files):
                if filename.startswith("."):
                    continue
                    
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "rb") as f:
                        hasher.update(f.read())
                except Exception:
                    pass
        
        return hasher.hexdigest()[:12]
