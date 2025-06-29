import subprocess
import json
from datetime import datetime
from typing import List, Dict, Optional


class ContainerManager:
    """
    A hybrid container management class with static methods for local operations 
    and instance methods for registry operations.
    
    Static Methods (No Authentication Required):
    - build_image(): Build Docker images locally
    - list_local_images(): List local Docker images
    - cleanup_local_images(): Clean up old local images
    
    Instance Methods (Require Authentication):
    - authenticate(): Authenticate to a registry
    - push_image(): Push images to authenticated registry
    - cleanup_registry_images(): Clean up old registry images
    
    Key Features:
    - Clean separation between local and remote operations
    - Static methods for stateless local Docker operations
    - Instance methods for stateful registry authentication
    - Automatic image cleanup after successful operations
    - Support for AWS ECR, Docker Hub, and private registries
    
    Example Usage:
        ```python
        # Static local operations (no authentication needed)
        dockerfile_content = "FROM python:3.11\\nWORKDIR /app\\n..."
        ContainerManager.build_image(dockerfile_content, "myapp", "v1.0.1", keep_count=5)
        
        # Instance registry operations (authentication required)
        cm = ContainerManager()
        cm.authenticate("myregistry.com", "user", "pass")
        cm.push_image("myapp", "v1.0.1", keep_count=5)
        ```
    
    Design Benefits:
    - Static builds can be called without instantiation
    - Registry authentication state is properly managed
    - Clear separation of local vs remote concerns
    - No unnecessary object creation for local operations
    """
    
    def __init__(self):
        """
        Initialize a new ContainerManager instance for registry operations.
        
        Sets up initial state for authentication and registry configuration.
        No authentication is performed until authenticate() is called.
        """
        self.authenticated = False
        self.registry_url = None
        self.auth_info = {}
    
    # ========================
    # STATIC METHODS (Local Operations - No Authentication)
    # ========================
    
    @staticmethod
    def build_image(container_content: str, image_name: str, tag: str = "latest", 
                   build_context: str = '.', keep_count: int = 5) -> bool:
        """
        Build a container image from Dockerfile content with automatic cleanup.
        
        Builds the image locally using Docker daemon and automatically cleans up
        old local images after successful build to maintain storage hygiene.
        
        Args:
            container_content: Dockerfile content as string
            image_name: Base image name (e.g., "myapp", "backend")
            tag: Image tag (e.g., "latest", "v1.0.1", "dev")
            build_context: Build context directory (default: current directory)
            keep_count: Number of local images to retain after cleanup (default: 5)
            
        Returns:
            bool: True if build and cleanup successful, False otherwise
            
        Examples:
            ```python
            dockerfile = '''
            FROM python:3.11-slim
            WORKDIR /app
            COPY . .
            CMD ["python", "app.py"]
            '''
            
            # Build with default settings
            ContainerManager.build_image(dockerfile, "myapp", "v1.0.1")
            
            # Build with custom cleanup policy
            ContainerManager.build_image(dockerfile, "myapp", "dev", keep_count=10)
            ```
            
        Notes:
            - No authentication required (local operation only)
            - Cleanup only runs after successful build
            - Cleanup preserves the most recently created images
            - Build timeout is set to 10 minutes
        """
        full_image_name = f"{image_name}:{tag}"
        
        # Build locally
        if ContainerManager._build_image_internal(container_content, full_image_name, build_context):
            # Auto-cleanup after successful build
            print(f"Cleaning up old local images (keeping last {keep_count})...")
            ContainerManager.cleanup_local_images(image_name, keep_count)
            return True
        return False
    
    @staticmethod
    def _build_image_internal(container_content: str, full_image_name: str, build_context: str) -> bool:
        """Internal method to build container image locally"""
        try:
            # Build with local tag only (no registry tag for static method)
            tags = ['-t', full_image_name]
            
            # FIXED: Added encoding parameter to handle Windows Unicode issues
            process = subprocess.run([
                'docker', 'build',
                '-f', '-',  # Read from stdin
                *tags,
                build_context
            ], 
            input=container_content, 
            text=True, 
            capture_output=True, 
            timeout=600,
            encoding='utf-8',  # FIXED: Explicit UTF-8 encoding
            errors='replace'   # FIXED: Replace invalid characters instead of failing
            )
            
            if process.returncode == 0:
                print(f"✓ Successfully built image: {full_image_name}")
                return True
            else:
                print(f"✗ Build failed: {process.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"✗ Build timed out for: {full_image_name}")
            return False
        except Exception as e:
            print(f"✗ Error building image: {e}")
            return False
    
    @staticmethod
    def list_local_images(image_base_name: str) -> List[Dict]:
        """
        List local images matching base name, sorted by creation date then by tag.
        
        Args:
            image_base_name: Base name to filter images (e.g., "myapp")
            
        Returns:
            List of image dictionaries sorted by version priority and creation date
            
        Examples:
            ```python
            images = ContainerManager.list_local_images("myapp")
            for img in images:
                print(f"{img['Repository']}:{img['Tag']} - {img['CreatedAt']}")
            ```
        """
        try:
            # Use JSON format for more reliable parsing
            # FIXED: Added encoding parameter to handle Windows Unicode issues
            process = subprocess.run([
                'docker', 'images', 
                '--format', '{{json .}}',
                '--filter', f'reference={image_base_name}*'
            ], 
            capture_output=True, 
            text=True,
            encoding='utf-8',  # FIXED: Explicit UTF-8 encoding
            errors='replace'   # FIXED: Replace invalid characters instead of failing
            )
            
            if process.returncode != 0:
                print(f"    ✗ Failed to list images: {process.stderr}")
                return []
            
            images = []
            lines = process.stdout.strip().split('\n')
            
            for line in lines:
                if line.strip():
                    try:
                        img_data = json.loads(line)
                        # Only include images that match our pattern
                        if img_data.get('Repository', '').startswith(image_base_name):
                            images.append(img_data)
                    except json.JSONDecodeError:
                        continue
            
            # Smart sorting: prioritize by semantic version, then by timestamp
            def sort_key(img):
                tag = img.get('Tag', '')
                created_at = img.get('CreatedAt', '')
                
                # Parse semantic versions for better ordering
                version_priority = 0
                try:
                    if tag == 'latest':
                        version_priority = 9999999  # Latest should be HIGHEST priority
                    elif tag.startswith('v') and '.' in tag:
                        # v1.0.1 -> (1, 0, 1)
                        parts = tag[1:].split('.')
                        version_priority = sum(int(p) * (1000 ** (len(parts) - i - 1)) 
                                             for i, p in enumerate(parts))
                    elif '.' in tag and all(p.isdigit() for p in tag.split('.')):
                        # 1.0.1 -> (1, 0, 1)  
                        parts = tag.split('.')
                        version_priority = sum(int(p) * (1000 ** (len(parts) - i - 1)) 
                                             for i, p in enumerate(parts))
                    else:
                        # Try to extract numbers from tag
                        import re
                        numbers = re.findall(r'\d+', tag)
                        if numbers:
                            version_priority = int(numbers[-1])  # Use last number
                except:
                    version_priority = 0
                
                # Return tuple: (version_priority, timestamp) for sorting
                # Higher version priority = newer, later timestamp = newer
                return (version_priority, created_at)
            
            # Sort by version priority first, then timestamp (both descending = newest first)
            images.sort(key=sort_key, reverse=True)
            
            print(f"    Found {len(images)} local images matching '{image_base_name}'")
            for i, img in enumerate(images[:5]):  # Show first 5 for debugging
                repo = img.get('Repository', 'unknown')
                tag = img.get('Tag', 'unknown')
                created = img.get('CreatedAt', 'unknown')
                
                # Calculate version priority for debugging
                version_info = ""
                try:
                    priority, _ = sort_key(img)
                    version_info = f"priority:{priority}"
                except:
                    version_info = "priority:unknown"
                
                print(f"      #{i+1}: {repo}:{tag} ({version_info}, created: {created})")
            
            return images
            
        except Exception as e:
            print(f"    ✗ Error listing local images: {e}")
            return []
    
    @staticmethod
    def cleanup_local_images(image_base_name: str, keep_count: int) -> bool:
        """
        Remove old local images, keeping the most recent ones.
        
        Args:
            image_base_name: Base name to filter images for cleanup
            keep_count: Number of most recent images to keep
            
        Returns:
            bool: True if cleanup successful, False otherwise
            
        Examples:
            ```python
            # Keep only the 3 most recent myapp images
            ContainerManager.cleanup_local_images("myapp", 3)
            ```
        """
        images = ContainerManager.list_local_images(image_base_name)
        
        if len(images) <= keep_count:
            print(f"  Only {len(images)} local images found, keeping all (want to keep {keep_count})")
            return True
        
        # Keep the first 'keep_count' images (most recent)
        to_keep = images[:keep_count]
        to_delete = images[keep_count:]
        
        print(f"  Keeping {len(to_keep)} most recent images:")
        for img in to_keep:
            repo = img.get('Repository', 'unknown')
            tag = img.get('Tag', 'unknown')
            print(f"    ✓ Keep: {repo}:{tag}")
        
        print(f"  Cleaning up {len(to_delete)} old local images:")
        
        success = True
        for img in to_delete:
            try:
                repo = img.get('Repository', 'unknown')
                tag = img.get('Tag', 'unknown')
                img_id = img.get('ID', '')
                
                # Use image ID to avoid tag conflicts
                result = subprocess.run(['docker', 'rmi', img_id], 
                                      capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    print(f"    ✓ Deleted: {repo}:{tag}")
                else:
                    print(f"    ✗ Failed to delete {repo}:{tag}: {result.stderr}")
                    success = False
            except Exception as e:
                print(f"    ✗ Error deleting {repo}:{tag}: {e}")
                success = False
        
        return success
    
    @staticmethod
    def debug_list_all_images():
        """Debug method to list all local images"""
        try:
            process = subprocess.run([
                'docker', 'images', '--format', '{{json .}}'
            ], capture_output=True, text=True)
            
            if process.returncode == 0:
                print("🔍 All local Docker images:")
                lines = process.stdout.strip().split('\n')
                for line in lines:
                    if line.strip():
                        try:
                            img = json.loads(line)
                            repo = img.get('Repository', 'unknown')
                            tag = img.get('Tag', 'unknown')
                            print(f"  {repo}:{tag}")
                        except:
                            continue
            else:
                print(f"Failed to list images: {process.stderr}")
        except Exception as e:
            print(f"Error: {e}")
    
    # ========================
    # INSTANCE METHODS (Registry Operations - Authentication Required)
    # ========================
    
    def authenticate(self, registry_url: str = "localhost:5000", username: str = None, 
                    password: str = None, region: str = None) -> bool:
        """
        Authenticate to a container registry.
        
        Supports multiple registry types with appropriate authentication methods:
        - AWS ECR: Uses AWS CLI to get temporary tokens (no username/password needed)
        - Docker Hub/Private: Uses standard username/password authentication
        
        Args:
            registry_url: Registry URL (e.g., "docker.io", "myregistry.com:5000")
            username: Username for non-AWS registries (required for Docker Hub/private)
            password: Password for non-AWS registries (required for Docker Hub/private)  
            region: AWS region for ECR authentication (required for ECR)
            
        Returns:
            bool: True if authentication successful, False otherwise
            
        Examples:
            ```python
            cm = ContainerManager()
            
            # AWS ECR
            cm.authenticate("123456789.dkr.ecr.us-east-1.amazonaws.com", region="us-east-1")
            
            # Docker Hub
            cm.authenticate("docker.io", "myusername", "mypassword")
            
            # Private registry
            cm.authenticate("myregistry.com:5000", "admin", "secret")
            
            # Local registry (no auth)
            cm.authenticate("localhost:5000")
            ```
            
        Raises:
            No exceptions raised directly, but prints error messages for:
            - Missing AWS CLI for ECR
            - Missing Docker CLI
            - Invalid credentials
            - Network timeouts
        """
        if self.authenticated and self.registry_url == registry_url:
            return True
        
        # Parse registry type
        is_aws = "ecr" in registry_url and "amazonaws.com" in registry_url
        
        try:
            if is_aws:
                # AWS ECR Authentication
                if not region:
                    print("✗ AWS ECR requires region parameter")
                    return False
                
                print(f"Authenticating to AWS ECR in region: {region}")
                
                # Step 1: Get ECR login token
                token_process = subprocess.run([
                    "aws", "ecr", "get-login-password", "--region", region
                ], capture_output=True, text=True, timeout=300)
                
                if token_process.returncode != 0:
                    print(f"✗ AWS ECR get-login-password failed: {token_process.stderr}")
                    print("  Make sure AWS CLI is configured with proper credentials")
                    return False
                
                # Step 2: Use token to login to Docker
                login_process = subprocess.run([
                    "docker", "login", "--username", "AWS", "--password-stdin", registry_url
                ], input=token_process.stdout, text=True, capture_output=True, timeout=300)
                
                if login_process.returncode != 0:
                    print(f"✗ Docker login to ECR failed: {login_process.stderr}")
                    return False
                
            else:
                # Regular registry authentication (Docker Hub, private registries, etc.)
                if not username or not password:
                    print("✗ Registry requires username and password")
                    return False
                
                print(f"Authenticating to registry: {registry_url}")
                
                process = subprocess.run([
                    "docker", "login", 
                    "-u", username,
                    "-p", password,
                    registry_url
                ], capture_output=True, text=True, timeout=300)
                
                if process.returncode != 0:
                    print(f"✗ Registry authentication failed: {process.stderr}")
                    return False
            
            print(f"✓ Successfully authenticated to Registry: {registry_url}")
            self.authenticated = True
            self.registry_url = registry_url
            self.auth_info = {"username": username, "password": password, "region": region}
            return True
            
        except subprocess.TimeoutExpired:
            print("✗ Authentication timed out")
            return False
        except FileNotFoundError as e:
            if "aws" in str(e):
                print("✗ AWS CLI not found. Please install AWS CLI for ECR authentication")
            elif "docker" in str(e):
                print("✗ Docker not found. Please install Docker")
            else:
                print(f"✗ Command not found: {e}")
            return False
        except Exception as e:
            print(f"✗ Error during Registry authentication: {e}")
            return False
    

    
    def push_image(self, image_name: str, tag: str = "latest", keep_count: int = 5) -> bool:
        """
        Push a container image to the authenticated registry with automatic cleanup.
        
        Pushes the specified image to the configured registry and automatically
        cleans up old registry images to manage storage costs and maintain organization.
        
        Args:
            image_name: Base image name (must exist locally)
            tag: Image tag to push (must exist locally)
            keep_count: Number of registry images to retain after cleanup (default: 5)
            
        Returns:
            bool: True if push and cleanup successful, False otherwise
            
        Examples:
            ```python
            cm = ContainerManager()
            cm.authenticate("myregistry.com", "user", "pass")
            
            # Push with default cleanup
            cm.push_image("myapp", "v1.0.1")
            
            # Push with custom retention policy
            cm.push_image("myapp", "prod", keep_count=10)
            ```
            
        Prerequisites:
            - Must call authenticate() first
            - Image must exist locally (built with ContainerManager.build_image())
            - Registry must be accessible and writable
            
        Notes:
            - Automatically tags local image for registry before pushing
            - Only works with authenticated registries
            - Cleanup only runs after successful push
            - AWS ECR cleanup uses AWS CLI commands
            - Other registries may not support automatic cleanup
            - Push timeout is set to 10 minutes
        """
        if not self.registry_url:
            print("✗ No registry configured")
            return False
            
        if not self.authenticated:
            print("✗ Not authenticated to registry")
            return False
        
        full_image_name = f"{image_name}:{tag}"
        
        if self._push_image_internal(full_image_name):
            # Auto-cleanup registry after successful push
            print(f"Cleaning up old registry images (keeping last {keep_count})...")
            self._cleanup_registry_images(image_name, keep_count)
            return True
        return False
    
    def _push_image_internal(self, full_image_name: str) -> bool:
        """Internal method to push image to registry"""
        # Tag the local image for registry if not already tagged
        local_image = full_image_name
        registry_image_name = f"{self.registry_url}/{full_image_name}"
        
        try:
            # First, tag the local image for the registry
            tag_process = subprocess.run([
                'docker', 'tag', local_image, registry_image_name
            ], capture_output=True, text=True, timeout=60)
            
            if tag_process.returncode != 0:
                print(f"✗ Failed to tag image for registry: {tag_process.stderr}")
                return False
            
            # Then push the registry-tagged image
            process = subprocess.run([
                'docker', 'push', registry_image_name
            ], capture_output=True, text=True, timeout=600)
            
            if process.returncode == 0:
                print(f"✓ Image pushed: {registry_image_name}")
                return True
            else:
                print(f"✗ Push failed: {process.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"✗ Push timed out for: {registry_image_name}")
            return False
        except Exception as e:
            print(f"✗ Push error: {e}")
            return False
    
    def _cleanup_registry_images(self, image_base_name: str, keep_count: int) -> bool:
        """Remove old registry images (implementation depends on registry type)"""
        if not self.registry_url:
            print("  No registry configured for cleanup")
            return False
        
        # AWS ECR cleanup
        if "ecr" in self.registry_url and "amazonaws.com" in self.registry_url:
            return self._cleanup_ecr_images(image_base_name, keep_count)
        
        # Docker Hub - cannot delete via API
        elif "docker.io" in self.registry_url or self.registry_url == "docker.io":
            print("  ⚠️  Docker Hub does not support automated image deletion")
            print("  💡 To clean up Docker Hub images:")
            print("     1. Go to https://hub.docker.com/")
            print("     2. Navigate to your repository")
            print("     3. Delete old tags manually via web interface")
            return True  # Don't fail the operation
        
        # Local registry cleanup (could be implemented)
        elif "localhost" in self.registry_url or "127.0.0.1" in self.registry_url:
            print("  ⚠️  Local registry cleanup not implemented")
            print("  💡 You can manually delete from registry storage or restart registry")
            return True  # Don't fail the operation
            
        # Generic/unknown registry
        else:
            print("  ⚠️  Registry cleanup not implemented for this registry type")
            print(f"  💡 Manual cleanup may be needed for: {self.registry_url}")
            return True  # Don't fail the operation
    
    def _cleanup_ecr_images(self, image_base_name: str, keep_count: int) -> bool:
        """AWS ECR specific cleanup"""
        try:
            # Extract repository name (remove tag if present)
            repo_name = image_base_name.split(':')[0]
            region = self.auth_info.get('region', 'us-east-1')
            
            # List images in ECR repository
            list_process = subprocess.run([
                'aws', 'ecr', 'describe-images',
                '--repository-name', repo_name,
                '--region', region,
                '--query', 'sort_by(imageDetails,&imagePushedAt)[*].[imageDigest,imageTags[0],imagePushedAt]',
                '--output', 'json'
            ], capture_output=True, text=True, timeout=300)
            
            if list_process.returncode != 0:
                print(f"    ✗ Failed to list ECR images: {list_process.stderr}")
                return False
            
            images = json.loads(list_process.stdout)
            
            if len(images) <= keep_count:
                print(f"    Only {len(images)} images in ECR repository, nothing to cleanup")
                return True
            
            # Delete old images (keep the most recent ones)
            to_delete = images[:-keep_count]  # All except last N
            print(f"    Cleaning up {len(to_delete)} old ECR images...")
            
            success = True
            for digest, tag, pushed_at in to_delete:
                try:
                    delete_process = subprocess.run([
                        'aws', 'ecr', 'batch-delete-image',
                        '--repository-name', repo_name,
                        '--region', region,
                        '--image-ids', f'imageDigest={digest}'
                    ], capture_output=True, text=True, timeout=300)
                    
                    if delete_process.returncode == 0:
                        print(f"      ✓ Deleted ECR image: {repo_name}:{tag or 'untagged'}")
                    else:
                        print(f"      ✗ Failed to delete ECR image {tag}: {delete_process.stderr}")
                        success = False
                except Exception as e:
                    print(f"      ✗ Error deleting ECR image {tag}: {e}")
                    success = False
            
            return success
            
        except Exception as e:
            print(f"    ✗ Error cleaning up ECR images: {e}")
            return False


# Test/Demo functions
def demo_hybrid_container_manager():
    """Demonstrate hybrid ContainerManager usage with verbose output"""
    print("🐳 Hybrid ContainerManager Demo")
    print("=" * 50)
    
    # Sample Dockerfile content
    dockerfile_content = """FROM python:3.11-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Create a simple app
RUN echo 'import time; print("Hello from demo container!"); time.sleep(10)' > app.py

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD echo "Health check passed"

EXPOSE 8080
CMD ["python", "app.py"]
"""

    print("📄 Generated Dockerfile content:")
    print("-" * 30)
    print(dockerfile_content[:200] + "...")
    print("-" * 30)

    # Test static local builds
    print("\n🏗️  Testing STATIC local image builds (no authentication needed)...")
    
    test_images = [
        ("demo-app", "v1.0.0"),
        ("demo-app", "v1.0.1"), 
        ("demo-app", "v1.0.2"),
        ("demo-app", "latest")
    ]
    
    for image_name, tag in test_images:
        print(f"\nBuilding {image_name}:{tag} (static method)...")
        success = ContainerManager.build_image(dockerfile_content, image_name, tag, keep_count=2)
        status = "✓ Success" if success else "✗ Failed"
        print(f"Build result: {status}")
    
    # Show current images with debug info
    print("\n📋 Debugging image listing (static method)...")
    ContainerManager.debug_list_all_images()
    
    print("\n🧹 Testing cleanup specifically (static method)...")
    ContainerManager.cleanup_local_images("demo-app", 3)
    
    # Demonstrate instance registry operations
    print("\n🔐 Testing INSTANCE registry operations...")
    print("   (Authentication required for these operations)")
    cm = ContainerManager()
    print("✓ ContainerManager instance created")
    
    # Show usage patterns
    print("\n📖 Usage Patterns:")
    print("   STATIC (No Authentication):")
    print("   └── ContainerManager.build_image(dockerfile, 'myapp', 'v1.0.1')")
    print("   └── ContainerManager.list_local_images('myapp')")
    print("   └── ContainerManager.cleanup_local_images('myapp', 5)")
    print()
    print("   INSTANCE (Authentication Required):")
    print("   └── cm = ContainerManager()")
    print("   └── cm.authenticate('myregistry.com', 'user', 'pass')")
    print("   └── cm.push_image('myapp', 'v1.0.1')")
    
    print("\n🚀 Complete Hybrid Workflow Example:")
    print("   # Static local build")
    print("   1. ContainerManager.build_image(dockerfile_content, 'myapp', 'v1.0.1')")
    print("   ")
    print("   # Instance registry operations")
    print("   2. cm = ContainerManager()")
    print("   3. cm.authenticate('myregistry.com', 'user', 'pass')")
    print("   4. cm.push_image('myapp', 'v1.0.1')  # Auto-tags for registry")
    
    print("\n" + "=" * 50)
    print("✅ Hybrid Demo completed!")
    print("\n💡 Benefits of Hybrid Design:")
    print("   ✓ Static methods for stateless local operations")
    print("   ✓ Instance methods for stateful registry operations")
    print("   ✓ No unnecessary object creation for local builds")
    print("   ✓ Proper authentication state management")
    print("   ✓ Clean separation of concerns")


def test_hybrid_workflow():
    """Test the hybrid workflow with local registry"""
    print("🧪 Testing Hybrid Workflow")
    print("=" * 40)
    print("Prerequisites: docker run -d -p 5000:5000 --name registry registry:2")
    print()
    
    dockerfile_content = """FROM alpine:latest
RUN echo 'echo "Hello from hybrid test container"' > /app.sh
RUN chmod +x /app.sh
CMD ["/app.sh"]
"""
    
    # Step 1: Static local build
    print("1. STATIC: Building image locally...")
    success = ContainerManager.build_image(dockerfile_content, "hybrid-test", "v1.0.0", keep_count=3)
    if success:
        print("✓ Static build successful")
        
        # Step 2: Instance registry operations
        print("\n2. INSTANCE: Setting up registry operations...")
        cm = ContainerManager()
        
        if cm.authenticate("localhost:5000"):
            print("✓ Registry authentication successful")
            
            # Step 3: Push the locally built image
            print("\n3. INSTANCE: Pushing to registry...")
            if cm.push_image("hybrid-test", "v1.0.0", keep_count=3):
                print("✓ Push successful")
                print("✅ Full hybrid workflow completed!")
                
                # Step 4: Test another version
                print("\n4. TESTING: Building and pushing another version...")
                if ContainerManager.build_image(dockerfile_content.replace("v1.0.0", "v2.0.0"), "hybrid-test", "v2.0.0"):
                    print("✓ Static build v2.0.0 successful")
                    if cm.push_image("hybrid-test", "v2.0.0", keep_count=3):
                        print("✓ Push v2.0.0 successful")
                        print("✅ Multi-version workflow completed!")
            else:
                print("✗ Push failed")
        else:
            print("✗ Registry authentication failed")
    else:
        print("✗ Static build failed")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test-hybrid":
            test_hybrid_workflow()
        else:
            print("Usage:")
            print("  python container_manager.py                    # Demo")
            print("  python container_manager.py --test-hybrid      # Test hybrid workflow")
    else:
        demo_hybrid_container_manager()