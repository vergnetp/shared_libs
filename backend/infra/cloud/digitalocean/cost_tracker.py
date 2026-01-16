"""
DigitalOcean Cost Tracker

Track spending per project/environment using droplet tags.

Uses AsyncDOClient from shared cloud module.

SAFETY: Only tracks costs for managed droplets (tagged with MANAGED_TAG).
Personal/unmanaged servers are excluded from cost calculations.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from .client import MANAGED_TAG, AsyncDOClient


@dataclass
class DropletCost:
    """Cost info for a single droplet."""
    id: int
    name: str
    size: str
    monthly_cost: float
    hourly_cost: float
    project: Optional[str] = None
    environment: Optional[str] = None
    region: str = ""


@dataclass 
class CostSummary:
    """Aggregated cost summary."""
    total_monthly: float
    total_hourly: float
    by_project: Dict[str, float]
    by_environment: Dict[str, float]
    by_region: Dict[str, float]
    droplets: List[DropletCost]


# DigitalOcean size pricing (monthly USD) - as of 2024
SIZE_PRICING = {
    "s-1vcpu-512mb-10gb": 4.0,
    "s-1vcpu-1gb": 6.0,
    "s-1vcpu-1gb-amd": 7.0,
    "s-1vcpu-1gb-intel": 7.0,
    "s-1vcpu-2gb": 12.0,
    "s-1vcpu-2gb-amd": 14.0,
    "s-1vcpu-2gb-intel": 14.0,
    "s-2vcpu-2gb": 18.0,
    "s-2vcpu-2gb-amd": 21.0,
    "s-2vcpu-2gb-intel": 21.0,
    "s-2vcpu-4gb": 24.0,
    "s-2vcpu-4gb-amd": 28.0,
    "s-2vcpu-4gb-intel": 28.0,
    "s-4vcpu-8gb": 48.0,
    "s-4vcpu-8gb-amd": 56.0,
    "s-4vcpu-8gb-intel": 56.0,
    "s-8vcpu-16gb": 96.0,
    "g-2vcpu-8gb": 63.0,
    "g-4vcpu-16gb": 126.0,
    "gd-2vcpu-8gb": 68.0,
    "gd-4vcpu-16gb": 136.0,
    "m-2vcpu-16gb": 84.0,
    "m-4vcpu-32gb": 168.0,
    "c-2": 42.0,
    "c-4": 84.0,
    "c-8": 168.0,
}


class CostTracker:
    """
    Track DigitalOcean costs by project and environment.
    
    Uses AsyncDOClient from shared cloud module for all API calls.
    """
    
    def __init__(self, do_token: str):
        self.do_token = do_token
        self._client: Optional[AsyncDOClient] = None
    
    def _get_client(self) -> AsyncDOClient:
        """Get or create async DO client."""
        if self._client is None:
            self._client = AsyncDOClient(self.do_token)
        return self._client
    
    async def close(self):
        """Close the client."""
        if self._client:
            await self._client.close()
            self._client = None
    
    def _get_size_cost(self, size_slug: str) -> float:
        """Get monthly cost for a size slug."""
        # Try exact match first
        if size_slug in SIZE_PRICING:
            return SIZE_PRICING[size_slug]
        
        # Try without suffix
        base_size = size_slug.rsplit('-', 1)[0] if '-' in size_slug else size_slug
        if base_size in SIZE_PRICING:
            return SIZE_PRICING[base_size]
        
        # Default estimate based on pattern
        return 6.0  # Minimum droplet cost
    
    def _is_managed(self, tags: List[str]) -> bool:
        """Check if droplet is managed by our system."""
        return MANAGED_TAG in tags
    
    def _parse_tags(self, tags: List[str]) -> Dict[str, Optional[str]]:
        """Parse project/environment from tags."""
        result = {"project": None, "environment": None}
        
        for tag in tags:
            if tag.startswith("project:") or tag.startswith("project-"):
                result["project"] = tag.split(":", 1)[-1].split("-", 1)[-1]
            elif tag.startswith("env:") or tag.startswith("env-"):
                result["environment"] = tag.split(":", 1)[-1].split("-", 1)[-1]
        
        return result
    
    async def get_droplet_costs(self) -> List[DropletCost]:
        """
        Get cost info for managed droplets only.
        
        SAFETY: Excludes personal/unmanaged droplets from cost tracking.
        """
        client = self._get_client()
        
        # Get all droplets (include_unmanaged=True so we can filter ourselves)
        all_droplets = await client.list_droplets(include_unmanaged=True)
        
        droplets = []
        for d in all_droplets:
            tags = d.tags or []
            
            # SAFETY: Skip unmanaged droplets
            if not self._is_managed(tags):
                continue
            
            tags_info = self._parse_tags(tags)
            monthly = self._get_size_cost(d.size or "")
            
            droplets.append(DropletCost(
                id=d.id,
                name=d.name,
                size=d.size or "unknown",
                monthly_cost=monthly,
                hourly_cost=round(monthly / 730, 4),  # ~730 hours/month
                project=tags_info["project"],
                environment=tags_info["environment"],
                region=d.region or "unknown",
            ))
        
        return droplets
    
    async def get_cost_summary(self) -> CostSummary:
        """Get aggregated cost summary."""
        droplets = await self.get_droplet_costs()
        
        by_project: Dict[str, float] = {}
        by_environment: Dict[str, float] = {}
        by_region: Dict[str, float] = {}
        
        total_monthly = 0.0
        
        for d in droplets:
            total_monthly += d.monthly_cost
            
            project = d.project or "untagged"
            by_project[project] = by_project.get(project, 0) + d.monthly_cost
            
            env = d.environment or "untagged"
            by_environment[env] = by_environment.get(env, 0) + d.monthly_cost
            
            by_region[d.region] = by_region.get(d.region, 0) + d.monthly_cost
        
        return CostSummary(
            total_monthly=round(total_monthly, 2),
            total_hourly=round(total_monthly / 730, 4),
            by_project={k: round(v, 2) for k, v in sorted(by_project.items(), key=lambda x: -x[1])},
            by_environment={k: round(v, 2) for k, v in sorted(by_environment.items(), key=lambda x: -x[1])},
            by_region={k: round(v, 2) for k, v in sorted(by_region.items(), key=lambda x: -x[1])},
            droplets=droplets,
        )
    
    async def get_billing_history(self, limit: int = 12) -> List[Dict[str, Any]]:
        """Get recent billing history from DO."""
        client = self._get_client()
        return await client.get_billing_history(limit)
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get current account balance."""
        client = self._get_client()
        return await client.get_balance()
