"""
Kernel infrastructure schema using @entity decorators.

All kernel-owned tables defined here. AutoMigrator creates/updates at startup.

Usage:
    from app_kernel.db.schema import init_all_schemas
    
    async with db as conn:
        await init_all_schemas(conn, saas_enabled=True)
"""

from dataclasses import dataclass
from typing import Optional

from ...databases import entity, entity_field


# =============================================================================
# Core Infrastructure
# =============================================================================

@entity(table="kernel_jobs")
@dataclass
class Job:
    """Background job tracking."""
    task: str
    payload: Optional[str] = None
    context: Optional[str] = None
    status: str = entity_field(default="queued", index=True)
    result: Optional[str] = None
    error: Optional[str] = None
    attempts: int = entity_field(default=0)
    max_attempts: int = entity_field(default=3)
    priority: str = entity_field(default="normal")
    user_id: Optional[str] = entity_field(default=None, index=True)
    idempotency_key: Optional[str] = entity_field(default=None, index=True)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@entity(table="kernel_rate_limits", history=False)
@dataclass
class RateLimit:
    """Sliding window rate limiting."""
    user_id: Optional[str] = entity_field(default=None, index=True)
    ip_address: Optional[str] = entity_field(default=None, index=True)
    endpoint: Optional[str] = entity_field(default=None, index=True)
    window_start: Optional[str] = None
    request_count: int = entity_field(default=0)


@entity(table="kernel_idempotency_keys", history=False)
@dataclass
class IdempotencyKey:
    """Request deduplication."""
    user_id: Optional[str] = None
    endpoint: Optional[str] = None
    request_hash: Optional[str] = None
    response: Optional[str] = None
    status_code: Optional[int] = None
    expires_at: Optional[str] = entity_field(default=None, index=True)


# =============================================================================
# Authentication
# =============================================================================

@entity(table="kernel_users")
@dataclass
class User:
    """Legacy users table."""
    email: str = entity_field(default="", unique=True)
    password_hash: Optional[str] = None
    name: Optional[str] = None
    role: str = entity_field(default="user")
    is_active: int = entity_field(default=1)
    metadata: str = entity_field(default="{}")


@entity(table="kernel_auth_users")
@dataclass
class AuthUser:
    """Authentication users."""
    email: str = entity_field(default="", unique=True, index=True)
    password_hash: Optional[str] = None
    name: Optional[str] = None
    role: str = entity_field(default="user", index=True)
    is_active: int = entity_field(default=1)
    metadata: str = entity_field(default="{}")


@entity(table="kernel_auth_roles")
@dataclass
class AuthRole:
    """Role definitions with permissions."""
    name: str = entity_field(default="", unique=True, index=True)
    permissions: Optional[str] = None
    description: Optional[str] = None


@entity(table="kernel_auth_role_assignments")
@dataclass
class AuthRoleAssignment:
    """User-role mappings."""
    user_id: str = entity_field(default="", index=True)
    role_id: str = entity_field(default="", index=True)
    resource_type: str = entity_field(default="", index=True)
    resource_id: Optional[str] = entity_field(default=None, index=True)
    granted_by: Optional[str] = None
    expires_at: Optional[str] = None


@entity(table="kernel_auth_sessions", history=False)
@dataclass
class AuthSession:
    """Sessions for token revocation."""
    user_id: str = entity_field(default="", index=True)
    token_hash: str = entity_field(default="", index=True)
    expires_at: str = ""
    metadata: Optional[str] = None


# =============================================================================
# API Keys
# =============================================================================

@entity(table="kernel_api_keys")
@dataclass
class ApiKey:
    """API key management."""
    user_id: str = entity_field(default="", index=True)
    key_hash: str = entity_field(default="", unique=True, index=True)
    workspace_id: Optional[str] = entity_field(default=None, index=True)
    name: str = ""
    key_prefix: Optional[str] = None
    scopes: Optional[str] = None
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None


# =============================================================================
# Feature Flags
# =============================================================================

@entity(table="kernel_feature_flags")
@dataclass
class FeatureFlag:
    """Feature flag management."""
    name: str = entity_field(default="", unique=True, index=True)
    description: Optional[str] = None
    enabled: int = entity_field(default=0)
    rollout_percent: int = entity_field(default=100)
    allowed_workspaces: Optional[str] = None
    allowed_users: Optional[str] = None
    metadata: Optional[str] = None


# =============================================================================
# Webhooks
# =============================================================================

@entity(table="kernel_webhooks")
@dataclass
class Webhook:
    """Webhook subscriptions."""
    workspace_id: str = entity_field(default="", index=True)
    url: str = ""
    secret: Optional[str] = None
    description: Optional[str] = None
    enabled: int = entity_field(default=1)


@entity(table="kernel_webhook_deliveries", history=False)
@dataclass
class WebhookDelivery:
    """Webhook delivery logs."""
    webhook_id: str = entity_field(default="", index=True)
    event: str = ""
    payload: Optional[str] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    duration_ms: Optional[int] = None
    success: int = entity_field(default=0)
    error: Optional[str] = None


# =============================================================================
# OAuth
# =============================================================================

@entity(table="kernel_oauth_accounts")
@dataclass
class OAuthAccount:
    """OAuth account links."""
    user_id: str = entity_field(default="", index=True)
    provider: str = entity_field(default="", index=True)
    provider_user_id: str = entity_field(default="", index=True)
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[str] = None
    raw_data: Optional[str] = None


# =============================================================================
# Audit
# =============================================================================

@entity(table="kernel_audit_logs", history=False)
@dataclass
class AuditLog:
    """Audit log entries."""
    action: str = entity_field(default="", index=True)
    workspace_id: Optional[str] = entity_field(default=None, index=True)
    user_id: Optional[str] = entity_field(default=None, index=True)
    entity: Optional[str] = entity_field(default=None, index=True)
    entity_id: Optional[str] = entity_field(default=None, index=True)
    changes: Optional[str] = None
    metadata: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: Optional[str] = entity_field(default=None, index=True)


# =============================================================================
# Metering
# =============================================================================

@entity(table="kernel_usage_requests", history=False)
@dataclass
class UsageRequest:
    """Individual request logs."""
    user_id: Optional[str] = entity_field(default=None, index=True)
    workspace_id: Optional[str] = entity_field(default=None, index=True)
    endpoint: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None
    timestamp: Optional[str] = entity_field(default=None, index=True)


@entity(table="kernel_usage_summary", history=False)
@dataclass
class UsageSummary:
    """Aggregated usage for billing."""
    workspace_id: Optional[str] = entity_field(default=None, index=True)
    user_id: Optional[str] = entity_field(default=None, index=True)
    period: Optional[str] = entity_field(default=None, index=True)
    metric: Optional[str] = entity_field(default=None, index=True)
    value: int = entity_field(default=0)


# =============================================================================
# SaaS (Optional)
# =============================================================================

@entity(table="kernel_workspaces")
@dataclass
class Workspace:
    """Teams/organizations."""
    owner_id: str = entity_field(default="", index=True)
    name: str = ""
    slug: Optional[str] = entity_field(default=None, unique=True, index=True)
    is_personal: int = entity_field(default=0)
    settings_json: Optional[str] = None


@entity(table="kernel_workspace_members")
@dataclass
class WorkspaceMember:
    """Workspace membership."""
    workspace_id: str = entity_field(default="", index=True)
    user_id: str = entity_field(default="", index=True)
    role: str = entity_field(default="member")
    invited_by: Optional[str] = None
    joined_at: Optional[str] = None


@entity(table="kernel_workspace_invites")
@dataclass
class WorkspaceInvite:
    """Workspace invitations."""
    workspace_id: str = entity_field(default="", index=True)
    email: str = entity_field(default="", index=True)
    token: str = entity_field(default="", unique=True, index=True)
    role: str = entity_field(default="member")
    invited_by: str = ""
    status: str = entity_field(default="pending")
    expires_at: Optional[str] = None
    accepted_at: Optional[str] = None


@entity(table="kernel_projects")
@dataclass
class KernelProject:
    """Deployment groupings within workspaces (kernel-managed)."""
    workspace_id: str = entity_field(default="", index=True)
    slug: str = entity_field(default="", index=True)
    name: str = ""
    description: Optional[str] = None
    settings_json: Optional[str] = None
    created_by: str = ""


# =============================================================================
# Observability (Optional)
# =============================================================================

@entity(table="kernel_request_metrics", history=False)
@dataclass
class RequestMetric:
    """Request metrics for observability."""
    path: str = entity_field(default="", index=True)
    status_code: int = entity_field(default=0, index=True)
    timestamp: str = entity_field(default="", index=True)
    year: int = entity_field(default=0, index=True)
    month: int = entity_field(default=0, index=True)
    day: int = entity_field(default=0, index=True)
    request_id: str = ""
    method: str = ""
    query_params: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    server_latency_ms: float = 0.0
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    user_id: Optional[str] = entity_field(default=None, index=True)
    workspace_id: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    continent: Optional[str] = None
    hour: int = 0
    metadata: Optional[str] = None


# =============================================================================
# Schema Initialization
# =============================================================================

async def init_all_schemas(conn, saas_enabled: bool = False, request_metrics_enabled: bool = False) -> None:
    """
    Initialize all kernel schemas using AutoMigrator.
    
    Args:
        conn: Database connection
        saas_enabled: Include SaaS tables
        request_metrics_enabled: Include request_metrics table
    """
    from ...databases.migrations import AutoMigrator
    
    # AutoMigrator reads from ENTITY_SCHEMAS registry (populated by @entity decorators)
    migrator = AutoMigrator(conn)
    await migrator.auto_migrate()


# =============================================================================
# Cleanup Functions
# =============================================================================

async def cleanup_expired_idempotency_keys(conn) -> int:
    """Remove expired idempotency keys. Returns count removed."""
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat()
    
    results = await conn.find_entities(
        "kernel_idempotency_keys",
        where_clause="[expires_at] < ?",
        params=(now,)
    )
    count = len(results)
    
    for row in results:
        await conn.delete_entity("kernel_idempotency_keys", row["id"], permanent=True)
    
    return count


async def cleanup_old_rate_limits(conn, older_than_hours: int = 24) -> int:
    """Remove old rate limit entries. Returns count removed."""
    from datetime import datetime, timezone, timedelta
    
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
    
    results = await conn.find_entities(
        "kernel_rate_limits",
        where_clause="[window_start] < ?",
        params=(cutoff,)
    )
    count = len(results)
    
    for row in results:
        await conn.delete_entity("kernel_rate_limits", row["id"], permanent=True)
    
    return count
