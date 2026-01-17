"""
DNS module - DNS management and load balancing.

Usage (Sync):
    from infra.dns import DnsCleanupService, CloudflareLBService
    
    # Cleanup orphaned records
    result = DnsCleanupService(do_token, cf_token).cleanup_orphaned(zone_name)
    
    # Setup DNS load balancer
    result = CloudflareLBService(cf_token).setup_lb("api.example.com", ["1.2.3.4"])

Usage (Async):
    from infra.dns import AsyncDnsCleanupService, AsyncCloudflareLBService
    
    result = await AsyncDnsCleanupService(do_token, cf_token).cleanup_orphaned(zone_name)
    result = await AsyncCloudflareLBService(cf_token).setup_lb("api.example.com", ["1.2.3.4"])
"""

from .service import DnsCleanupService, AsyncDnsCleanupService, DnsCleanupResult
from .lb_service import CloudflareLBService, AsyncCloudflareLBService, LBSetupResult

__all__ = [
    # Cleanup
    "DnsCleanupService",
    "AsyncDnsCleanupService",
    "DnsCleanupResult",
    # Load Balancer
    "CloudflareLBService",
    "AsyncCloudflareLBService",
    "LBSetupResult",
]
