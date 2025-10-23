# backend/infra/git_manager.py

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


class GitManager:
    """
    Manages Git repository operations for automated code checkout.
    """
    
    # Base directory for all git checkouts
    GIT_CHECKOUT_BASE = Path("C:/local/git_checkouts") if os.name == 'nt' else Path("/local/git_checkouts")
    
    @staticmethod
    def _ensure_git_available() -> bool:
        """Check if git is installed"""
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                check=True,
                timeout=5
            )
            return True
        except Exception:
            return False
    
    @staticmethod
    def checkout_repo(
        repo_url: str,
        project_name: str,
        service_name: str,
        env: str,
        git_token: Optional[str] = None
    ) -> Optional[str]:
        """
        Clone or update a git repository and return the checkout path.
        
        Supports URLs with refs:
            - https://github.com/user/repo.git
            - https://github.com/user/repo.git@branch-name
            - https://github.com/user/repo.git@v1.2.3
            - https://github.com/user/repo.git@abc123
        
        Authentication:
            - Public repos: No authentication needed
            - Private repos: Pass git_token parameter or set GIT_TOKEN env var
        
        Args:
            repo_url: Git repository URL (with optional @ref)
            project_name: Project name for organizing checkouts
            service_name: Service name for organizing checkouts
            env: Environment name for organizing checkouts
            git_token: Personal access token for private repos (optional)
                
        Returns:
            Path to checked out repository, or None if failed
            
        Example:
            path = GitManager.checkout_repo(
                "https://github.com/user/myapp.git@develop",
                "myapp",
                "api",
                "prod",
                git_token="ghp_xxxxxxxxxxxx"
            )
            # Returns: C:/local/git_checkouts/myapp/prod/api
        """
        if not GitManager._ensure_git_available():
            log("Error: Git is not installed or not in PATH")
            return None
        
        try:
            # Parse URL and ref
            if '@' in repo_url and not repo_url.startswith('git@'):
                # URL with ref: https://github.com/user/repo.git@branch
                url, ref = repo_url.rsplit('@', 1)
            else:
                # Just URL: https://github.com/user/repo.git
                url = repo_url
                ref = None
            
            # Inject token for HTTPS private repos
            original_url = url  # Keep original for logging
            if url.startswith('https://'):
                # Try provided token first, then fallback to environment variable
                token = git_token or os.getenv('GIT_TOKEN')
                
                if token:
                    # Support multiple Git platforms
                    if 'github.com' in url:
                        # GitHub: https://TOKEN@github.com/user/repo.git
                        url = url.replace('https://', f'https://{token}@')
                        log(f"Using Git token for GitHub authentication")
                    elif 'gitlab.com' in url:
                        # GitLab: https://oauth2:TOKEN@gitlab.com/user/repo.git
                        url = url.replace('https://', f'https://oauth2:{token}@')
                        log(f"Using Git token for GitLab authentication")
                    elif 'bitbucket.org' in url:
                        # Bitbucket: https://x-token-auth:TOKEN@bitbucket.org/user/repo.git
                        url = url.replace('https://', f'https://x-token-auth:{token}@')
                        log(f"Using Git token for Bitbucket authentication")
                    else:
                        # Generic: https://TOKEN@git-server.com/user/repo.git
                        url = url.replace('https://', f'https://{token}@')
                        log(f"Using Git token for authentication")
                else:
                    log(f"No Git token provided, attempting public repository access")
            
            # Determine checkout directory (includes env)
            checkout_dir = GitManager.GIT_CHECKOUT_BASE / project_name / env / service_name
            checkout_dir.parent.mkdir(parents=True, exist_ok=True)
            
            # Clone or update
            if checkout_dir.exists():
                log(f"Repository exists, updating: {checkout_dir}")
                
                # Update remote URL with token (for subsequent operations)
                if git_token or os.getenv('GIT_TOKEN'):
                    try:
                        subprocess.run(
                            ["git", "remote", "set-url", "origin", url],
                            cwd=checkout_dir,
                            check=True,
                            capture_output=True
                        )
                    except:
                        pass  # Ignore errors, not critical
                
                # Fetch latest
                subprocess.run(
                    ["git", "fetch", "--all", "--tags"],
                    cwd=checkout_dir,
                    check=True,
                    capture_output=True,
                    timeout=120
                )
                
                # Checkout ref
                if ref:
                    subprocess.run(
                        ["git", "checkout", ref],
                        cwd=checkout_dir,
                        check=True,
                        capture_output=True
                    )
                    subprocess.run(
                        ["git", "pull"],
                        cwd=checkout_dir,
                        capture_output=True  # Ignore errors (might be tag/commit)
                    )
                else:
                    # Default branch
                    result = subprocess.run(
                        ["git", "checkout", "main"],
                        cwd=checkout_dir,
                        capture_output=True
                    )
                    if result.returncode != 0:
                        subprocess.run(
                            ["git", "checkout", "master"],
                            cwd=checkout_dir,
                            check=True,
                            capture_output=True
                        )
                    subprocess.run(
                        ["git", "pull"],
                        cwd=checkout_dir,
                        check=True,
                        capture_output=True
                    )
                
                log(f"✓ Updated repository: {ref or 'default branch'}")
                
            else:
                log(f"Cloning repository from {original_url}")
                
                # Clone
                subprocess.run(
                    ["git", "clone", url, str(checkout_dir)],
                    check=True,
                    capture_output=True,
                    timeout=300
                )
                
                # Checkout specific ref if provided
                if ref:
                    subprocess.run(
                        ["git", "checkout", ref],
                        cwd=checkout_dir,
                        check=True,
                        capture_output=True
                    )
                
                log(f"✓ Cloned repository: {ref or 'default branch'}")
            
            return str(checkout_dir)
            
        except subprocess.TimeoutExpired:
            log(f"Error: Git operation timed out")
            return None
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            log(f"Error: Git command failed: {error_msg}")
            
            # Check for authentication errors
            if any(phrase in error_msg.lower() for phrase in ['authentication failed', 'permission denied', 'invalid credentials', 'could not read', '403']):
                log("Authentication failed. Please check your Git token or ensure the repository is public.")
            
            return None
        except Exception as e:
            log(f"Error checking out repository: {e}")
            return None
    
    @staticmethod
    def cleanup_checkouts(project_name: Optional[str] = None):
        """
        Remove git checkouts.
        
        Args:
            project_name: If specified, only remove checkouts for this project
        """
        try:
            if project_name:
                checkout_dir = GitManager.GIT_CHECKOUT_BASE / project_name
            else:
                checkout_dir = GitManager.GIT_CHECKOUT_BASE
            
            if checkout_dir.exists():                
                shutil.rmtree(checkout_dir)
                log(f"✓ Cleaned up checkouts: {checkout_dir}")
        except Exception as e:
            log(f"Error cleaning up checkouts: {e}")