# backend/infra/do_state_manager.py
"""
DigitalOcean State Manager - Use DO tags as distributed database

Stores service deployment information as droplet tags, eliminating the need
for a centralized state manager. This provides:
- High availability (relies on DO's infrastructure)
- No single point of failure
- Survives total infrastructure loss
- Simple implementation
"""

import time
from typing import List, Dict, Any, Optional
from datetime import datetime

try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .do_manager import DOManager
except ImportError:
    from do_manager import DOManager


def log(msg):
    Logger.log(msg)


class DOStateManager:
    """
    Manage service deployment state using DigitalOcean droplet tags.
    
    Tag Format:
        Service tags: "svc:{user}:{project}:{env}:{service}"
        Status tags: "status:active" (already exists)
        
    Examples:
        "svc:u1:myapp:prod:api"
        "svc:u1:myapp:prod:postgres"
        "svc:u2:shop:dev:worker"
    
    This allows querying service state even when servers are completely down,
    since the data lives in DigitalOcean's cloud, not on the servers.
    """
    
    # Cache to reduce API calls (respects rate limits)
    _cache = {}
    CACHE_TTL = 60  # Cache for 60 seconds
    
    # Service tag prefix
    TAG_PREFIX = "svc"
    
    # ========================================
    # CORE OPERATIONS
    # ========================================
    
    @staticmethod
    def add_service_to_server(
        server_ip: str,
        user: str,
        project: str,
        env: str,
        service: str,
        credentials: Dict = None
    ) -> bool:
        """
        Add service tag to droplet when service is deployed.
        
        Call this after successful container deployment.
        
        Args:
            server_ip: Server IP address
            user: User ID (e.g., "u1")
            project: Project name
            env: Environment name
            service: Service name
            credentials: Optional credentials dict
            
        Returns:
            True if successful
        """
        try:
            # Get droplet
            droplet = DOStateManager._get_droplet_by_ip(server_ip, credentials)
            if not droplet:
                log(f"Could not find droplet for {server_ip}")
                return False
            
            # Create service tag
            service_tag = DOStateManager._make_service_tag(user, project, env, service)
            
            # Add tag
            DOManager.update_droplet_tags(
                droplet['droplet_id'],
                add_tags=[service_tag],
                credentials=credentials
            )
            
            log(f"Added service tag '{service_tag}' to {server_ip}")
            
            # Invalidate cache
            DOStateManager._invalidate_cache(server_ip)
            
            return True
            
        except Exception as e:
            log(f"Failed to add service tag to {server_ip}: {e}")
            return False
    
    @staticmethod
    def remove_service_from_server(
        server_ip: str,
        user: str,
        project: str,
        env: str,
        service: str,
        credentials: Dict = None
    ) -> bool:
        """
        Remove service tag from droplet when service is destroyed.
        
        Call this after container removal.
        
        Args:
            server_ip: Server IP address
            user: User ID
            project: Project name
            env: Environment name
            service: Service name
            credentials: Optional credentials dict
            
        Returns:
            True if successful
        """
        try:
            # Get droplet
            droplet = DOStateManager._get_droplet_by_ip(server_ip, credentials)
            if not droplet:
                log(f"Could not find droplet for {server_ip}")
                return False
            
            # Create service tag
            service_tag = DOStateManager._make_service_tag(user, project, env, service)
            
            # Remove tag
            DOManager.update_droplet_tags(
                droplet['droplet_id'],
                remove_tags=[service_tag],
                credentials=credentials
            )
            
            log(f"Removed service tag '{service_tag}' from {server_ip}")
            
            # Invalidate cache
            DOStateManager._invalidate_cache(server_ip)
            
            return True
            
        except Exception as e:
            log(f"Failed to remove service tag from {server_ip}: {e}")
            return False
    
    @staticmethod
    def get_services_on_server(
        server_ip: str,
        credentials: Dict = None,
        use_cache: bool = True
    ) -> List[Dict[str, str]]:
        """
        Get all services deployed on a server.
        
        CRITICAL: Works even if server is completely down!
        Queries DigitalOcean API for droplet tags.
        
        Args:
            server_ip: Server IP address
            credentials: Optional credentials dict
            use_cache: If True, use cached data (respects rate limits)
            
        Returns:
            List of service dicts: [
                {"user": "u1", "project": "myapp", "env": "prod", "service": "api"},
                {"user": "u2", "project": "shop", "env": "dev", "service": "worker"}
            ]
        """
        # Check cache first
        if use_cache:
            cached = DOStateManager._get_from_cache(server_ip)
            if cached is not None:
                return cached
        
        try:
            # Get droplet
            droplet = DOStateManager._get_droplet_by_ip(server_ip, credentials)
            if not droplet:
                log(f"Could not find droplet for {server_ip}")
                return []
            
            # Parse service tags
            services = []
            tags = droplet.get('tags', [])
            
            for tag in tags:
                if tag.startswith(f"{DOStateManager.TAG_PREFIX}:"):
                    parsed = DOStateManager._parse_service_tag(tag)
                    if parsed:
                        services.append(parsed)
            
            # Cache result
            DOStateManager._put_in_cache(server_ip, services)
            
            return services
            
        except Exception as e:
            log(f"Failed to get services from {server_ip}: {e}")
            return []
    
    @staticmethod
    def get_users_on_server(
        server_ip: str,
        credentials: Dict = None,
        use_cache: bool = True
    ) -> List[str]:
        """
        Get all unique users who have services on a server.
        
        CRITICAL: Works even if server is completely down!
        
        Args:
            server_ip: Server IP address
            credentials: Optional credentials dict
            use_cache: If True, use cached data
            
        Returns:
            List of unique user IDs: ["u1", "u2"]
        """
        services = DOStateManager.get_services_on_server(server_ip, credentials, use_cache)
        users = list(set([s['user'] for s in services]))
        return users
    
    @staticmethod
    def sync_server_tags(
        server_ip: str,
        expected_services: List[Dict[str, str]],
        credentials: Dict = None
    ) -> bool:
        """
        Sync droplet tags to match expected service state.
        
        Useful for fixing tag drift or recovering from missed updates.
        
        Args:
            server_ip: Server IP address
            expected_services: List of service dicts that SHOULD be on server
            credentials: Optional credentials dict
            
        Returns:
            True if successful
        """
        try:
            # Get current tags
            current = DOStateManager.get_services_on_server(server_ip, credentials, use_cache=False)
            
            # Convert to sets for comparison
            current_tags = set([
                DOStateManager._make_service_tag(s['user'], s['project'], s['env'], s['service'])
                for s in current
            ])
            
            expected_tags = set([
                DOStateManager._make_service_tag(s['user'], s['project'], s['env'], s['service'])
                for s in expected_services
            ])
            
            # Calculate diff
            to_add = expected_tags - current_tags
            to_remove = current_tags - expected_tags
            
            if not to_add and not to_remove:
                log(f"Tags already synced for {server_ip}")
                return True
            
            # Get droplet
            droplet = DOStateManager._get_droplet_by_ip(server_ip, credentials)
            if not droplet:
                return False
            
            # Apply changes
            if to_add or to_remove:
                DOManager.update_droplet_tags(
                    droplet['droplet_id'],
                    add_tags=list(to_add),
                    remove_tags=list(to_remove),
                    credentials=credentials
                )
                
                log(f"Synced tags for {server_ip}: +{len(to_add)}, -{len(to_remove)}")
            
            # Invalidate cache
            DOStateManager._invalidate_cache(server_ip)
            
            return True
            
        except Exception as e:
            log(f"Failed to sync tags for {server_ip}: {e}")
            return False
    
    # ========================================
    # HELPER METHODS
    # ========================================
    
    @staticmethod
    def _make_service_tag(user: str, project: str, env: str, service: str) -> str:
        """Create service tag from components"""
        return f"{DOStateManager.TAG_PREFIX}:{user}:{project}:{env}:{service}"
    
    @staticmethod
    def _parse_service_tag(tag: str) -> Optional[Dict[str, str]]:
        """
        Parse service tag into components.
        
        Args:
            tag: Tag string (e.g., "svc:u1:myapp:prod:api")
            
        Returns:
            Dict with user, project, env, service or None if invalid
        """
        try:
            parts = tag.split(':', 4)  # Split into max 5 parts
            
            if len(parts) != 5:
                return None
            
            prefix, user, project, env, service = parts
            
            if prefix != DOStateManager.TAG_PREFIX:
                return None
            
            return {
                'user': user,
                'project': project,
                'env': env,
                'service': service
            }
            
        except Exception:
            return None
    
    @staticmethod
    def _get_droplet_by_ip(server_ip: str, credentials: Dict = None) -> Optional[Dict[str, Any]]:
        """
        Find droplet by IP address.
        
        Args:
            server_ip: Server IP address
            credentials: Optional credentials dict
            
        Returns:
            Droplet info dict or None if not found
        """
        try:
            droplets = DOManager.list_droplets(tags=["Infra"], credentials=credentials)
            
            for droplet in droplets:
                if droplet.get('ip') == server_ip:
                    return droplet
            
            log(f"No droplet found with IP {server_ip}")
            return None
            
        except Exception as e:
            log(f"Error finding droplet by IP {server_ip}: {e}")
            return None
    
    # ========================================
    # CACHING (Rate Limit Protection)
    # ========================================
    
    @staticmethod
    def _get_from_cache(server_ip: str) -> Optional[List[Dict[str, str]]]:
        """Get cached services for server if not expired"""
        if server_ip not in DOStateManager._cache:
            return None
        
        timestamp, data = DOStateManager._cache[server_ip]
        age = time.time() - timestamp
        
        if age > DOStateManager.CACHE_TTL:
            # Expired
            del DOStateManager._cache[server_ip]
            return None
        
        return data
    
    @staticmethod
    def _put_in_cache(server_ip: str, services: List[Dict[str, str]]):
        """Cache services for server"""
        DOStateManager._cache[server_ip] = (time.time(), services)
    
    @staticmethod
    def _invalidate_cache(server_ip: str):
        """Invalidate cache for server after tag update"""
        if server_ip in DOStateManager._cache:
            del DOStateManager._cache[server_ip]
    
    @staticmethod
    def clear_cache():
        """Clear entire cache (useful for testing)"""
        DOStateManager._cache.clear()
    
    # ========================================
    # DIAGNOSTICS
    # ========================================
    
    @staticmethod
    def get_all_service_tags(credentials: Dict = None) -> Dict[str, List[Dict[str, str]]]:
        """
        Get service tags for all servers in infrastructure.
        
        Useful for debugging and auditing.
        
        Returns:
            Dict mapping server_ip -> list of services
        """
        try:
            droplets = DOManager.list_droplets(tags=["Infra"], credentials=credentials)
            
            result = {}
            for droplet in droplets:
                ip = droplet.get('ip')
                if ip:
                    services = []
                    for tag in droplet.get('tags', []):
                        if tag.startswith(f"{DOStateManager.TAG_PREFIX}:"):
                            parsed = DOStateManager._parse_service_tag(tag)
                            if parsed:
                                services.append(parsed)
                    
                    result[ip] = services
            
            return result
            
        except Exception as e:
            log(f"Failed to get all service tags: {e}")
            return {}
    
    @staticmethod
    def verify_tags_match_reality(
        server_ip: str,
        actual_services: List[Dict[str, str]],
        credentials: Dict = None
    ) -> Dict[str, List[str]]:
        """
        Compare DO tags with actual running services.
        
        Useful for detecting tag drift.
        
        Args:
            server_ip: Server IP
            actual_services: List of services actually running (from docker ps)
            credentials: Optional credentials
            
        Returns:
            Dict with 'missing_tags' and 'extra_tags' lists
        """
        try:
            # Get tags from DO
            tagged_services = DOStateManager.get_services_on_server(
                server_ip, credentials, use_cache=False
            )
            
            # Convert to sets
            tagged_set = set([
                DOStateManager._make_service_tag(s['user'], s['project'], s['env'], s['service'])
                for s in tagged_services
            ])
            
            actual_set = set([
                DOStateManager._make_service_tag(s['user'], s['project'], s['env'], s['service'])
                for s in actual_services
            ])
            
            # Find differences
            missing_tags = list(actual_set - tagged_set)  # Should be tagged but aren't
            extra_tags = list(tagged_set - actual_set)    # Tagged but not running
            
            return {
                'missing_tags': missing_tags,
                'extra_tags': extra_tags
            }
            
        except Exception as e:
            log(f"Failed to verify tags: {e}")
            return {'missing_tags': [], 'extra_tags': []}

