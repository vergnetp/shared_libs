"""
KernelSettings - Configuration for app_kernel initialization.

This is the single configuration object passed to init_app_kernel().
All settings are optional with sensible defaults.

IMPORTANT: All settings are FROZEN (immutable) after creation.
No per-request or runtime mutation of kernel config is allowed.
"""
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple, Literal, List, Callable, Awaitable


@dataclass(frozen=True)
class RedisSettings:
    """Redis connection settings for jobs and streaming."""
    url: str = "redis://localhost:6379"
    key_prefix: str = "app:"
    
    # Connection pool settings
    max_connections: int = 10
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0


@dataclass(frozen=True)
class StreamingSettings:
    """Streaming lifecycle settings."""
    max_concurrent_per_user: int = 5
    lease_ttl_seconds: int = 180
    lease_key_namespace: str = "stream_leases"


@dataclass(frozen=True)
class JobSettings:
    """
    Job queue settings.
    
    NOTE: retry_delays and max_attempts are defaults only.
    Registry metadata is advisory; the kernel does not schedule.
    """
    worker_count: int = 4
    thread_pool_size: int = 8
    work_timeout: float = 300.0
    
    # Retry defaults (advisory, not enforced by kernel)
    max_attempts: int = 3
    retry_delays: Tuple[float, ...] = (1.0, 5.0, 30.0)


@dataclass(frozen=True)
class AuthSettings:
    """Auth configuration."""
    token_secret: str = ""
    access_token_expires_minutes: int = 15
    refresh_token_expires_days: int = 30
    
    # If True, auth dependencies will be enabled
    enabled: bool = True


@dataclass(frozen=True)
class ObservabilitySettings:
    """Logging and metrics settings."""
    service_name: str = "app"
    log_level: str = "INFO"
    log_dir: Optional[str] = None
    
    # If True, adds caller info to logs
    add_caller_info: bool = True


@dataclass(frozen=True) 
class ReliabilitySettings:
    """Rate limiting and idempotency settings."""
    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60
    
    # Idempotency
    idempotency_enabled: bool = True
    idempotency_ttl_seconds: int = 86400  # 24 hours


@dataclass(frozen=True)
class FeatureSettings:
    """
    Feature flags for auto-mounted kernel routers.
    
    Defaults are sensible for internal/dev but safe for production.
    """
    # Health endpoints (always safe, no auth needed)
    enable_health_routes: bool = True
    health_path: str = "/healthz"
    ready_path: str = "/readyz"
    
    # Metrics endpoint
    enable_metrics: bool = True
    metrics_path: str = "/metrics"
    # Protection: "admin" (require admin user), "internal" (TODO: IP allowlist), "none"
    protect_metrics: Literal["admin", "internal", "none"] = "admin"
    
    # Auth routes (login, register, me)
    enable_auth_routes: bool = True
    # Mode: "local" (database auth), "apikey" (header key), "external" (skip kernel auth)
    auth_mode: Literal["local", "apikey", "external"] = "local"
    # IMPORTANT: disabled by default for security
    allow_self_signup: bool = False
    auth_prefix: str = "/auth"
    
    # Audit log query endpoint (admin only)
    enable_audit_routes: bool = False
    audit_path: str = "/audit"
    
    # Router prefix for all kernel routes (empty = mount at root)
    kernel_prefix: str = ""
    
    @classmethod
    def from_env(cls) -> "FeatureSettings":
        """
        Create settings from environment variables.
        
        Env vars (all optional, defaults used if not set):
            KERNEL_ENABLE_HEALTH=true
            KERNEL_ENABLE_METRICS=true
            KERNEL_PROTECT_METRICS=admin
            KERNEL_ENABLE_AUTH=true
            KERNEL_AUTH_MODE=local
            KERNEL_ALLOW_SIGNUP=false
            KERNEL_ENABLE_AUDIT=false
        """
        def env_bool(key: str, default: bool) -> bool:
            val = os.environ.get(key, "").lower()
            if val in ("true", "1", "yes"):
                return True
            elif val in ("false", "0", "no"):
                return False
            return default
        
        return cls(
            enable_health_routes=env_bool("KERNEL_ENABLE_HEALTH", True),
            enable_metrics=env_bool("KERNEL_ENABLE_METRICS", True),
            protect_metrics=os.environ.get("KERNEL_PROTECT_METRICS", "admin"),  # type: ignore
            enable_auth_routes=env_bool("KERNEL_ENABLE_AUTH", True),
            auth_mode=os.environ.get("KERNEL_AUTH_MODE", "local"),  # type: ignore
            allow_self_signup=env_bool("KERNEL_ALLOW_SIGNUP", False),
            enable_audit_routes=env_bool("KERNEL_ENABLE_AUDIT", False),
        )


# Type alias for health check functions
HealthCheckFn = Callable[[], Awaitable[Tuple[bool, str]]]


@dataclass(frozen=True)
class KernelSettings:
    """
    Complete configuration for app_kernel.
    
    FROZEN: Cannot be modified after creation.
    
    Usage:
        settings = KernelSettings(
            redis=RedisSettings(url="redis://localhost:6379"),
            auth=AuthSettings(token_secret=os.environ["JWT_SECRET"]),
            observability=ObservabilitySettings(service_name="my-api"),
        )
        
        init_app_kernel(app, settings, job_registry)
    """
    redis: RedisSettings = field(default_factory=RedisSettings)
    streaming: StreamingSettings = field(default_factory=StreamingSettings)
    jobs: JobSettings = field(default_factory=JobSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    reliability: ReliabilitySettings = field(default_factory=ReliabilitySettings)
    features: FeatureSettings = field(default_factory=FeatureSettings)
    
    # Database URL for auth stores (if using database-backed auth)
    database_url: Optional[str] = None
    
    # Health check functions (called by /readyz)
    # Each function returns (healthy: bool, message: str)
    # Example: [check_db, check_redis, check_opensearch]
    health_checks: Tuple[HealthCheckFn, ...] = field(default_factory=tuple)
    
    def __post_init__(self):
        """Validate settings after initialization."""
        # Use object.__setattr__ since frozen
        if self.auth.enabled and not self.auth.token_secret:
            raise ValueError("auth.token_secret is required when auth is enabled")
