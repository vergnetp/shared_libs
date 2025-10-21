import os
import subprocess
from pathlib import Path
from typing import Optional
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
        env: str
    ) -> Optional[str]:
        """
        Clone or update a git repository and return the checkout path.
        
        Supports URLs with refs:
            - https://github.com/user/repo.git
            - https://github.com/user/repo.git@branch-name
            - https://github.com/user/repo.git@v1.2.3
            - https://github.com/user/repo.git@abc123
            - git@github.com:user/repo.git@main
        
        Args:
            repo_url: Git repository URL (with optional @ref)
            project_name: Project name for organizing checkouts
            service_name: Service name for organizing checkouts
            env: Environment name for organizing checkouts
            
        Returns:
            Path to checked out repository, or None if failed
            
        Example:
            path = GitManager.checkout_repo(
                "https://github.com/user/myapp.git@develop",
                "myapp",
                "api",
                "prod"
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
                # Just URL: https://github.com/user/repo.git or git@github.com:user/repo.git
                url = repo_url
                ref = None
            
            # Determine checkout directory (includes env)
            checkout_dir = GitManager.GIT_CHECKOUT_BASE / project_name / env / service_name
            checkout_dir.parent.mkdir(parents=True, exist_ok=True)
            
            # Clone or update
            if checkout_dir.exists():
                log(f"Repository exists, updating: {checkout_dir}")
                
                # Fetch latest
                subprocess.run(
                    ["git", "fetch", "--all", "--tags"],
                    cwd=checkout_dir,
                    check=True,
                    capture_output=True
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
                    subprocess.run(
                        ["git", "checkout", "main"],
                        cwd=checkout_dir,
                        capture_output=True
                    )
                    if subprocess.run(["git", "branch", "--show-current"], cwd=checkout_dir, capture_output=True).returncode != 0:
                        subprocess.run(["git", "checkout", "master"], cwd=checkout_dir, check=True, capture_output=True)
                    subprocess.run(["git", "pull"], cwd=checkout_dir, check=True, capture_output=True)
                
                log(f"✓ Updated repository: {ref or 'default branch'}")
                
            else:
                log(f"Cloning repository: {url}")
                
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
            log(f"Error: Git operation timed out for {repo_url}")
            return None
        except subprocess.CalledProcessError as e:
            log(f"Error: Git command failed: {e.stderr.decode() if e.stderr else str(e)}")
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
                import shutil
                shutil.rmtree(checkout_dir)
                log(f"✓ Cleaned up checkouts: {checkout_dir}")
        except Exception as e:
            log(f"Error cleaning up checkouts: {e}")