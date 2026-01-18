"""
Cloud Provider Clients - Unified interface for cloud APIs.

Provides sync and async clients for:
- DigitalOcean (server provisioning, snapshots, VPC, registry)
- Cloudflare (DNS management, multi-server setup)
- Stripe (payments, subscriptions)
- LLM APIs (OpenAI, Anthropic, Groq)

All clients include:
- Automatic retries with exponential backoff
- Request/response tracing
- Connection pooling (pooled by base_url)
- Unified error handling

Connection Pooling:
    HTTP clients are pooled by base_url automatically. All AsyncDOClient 
    instances share the same TCP connection pool to api.digitalocean.com.
    Auth is passed per-request (multi-tenant safe).
    
    First request:  TCP + TLS handshake (~200-300ms)
    Subsequent:     Reuse connection (~20-50ms)
    
    Call close_all_cloud_clients() on app shutdown to clean up.
    
    For high concurrency, configure pool limits:
        from http_client import configure_pool_limits, PoolLimits
        configure_pool_limits(PoolLimits.high_concurrency())

Quick Start:
    # DigitalOcean (sync)
    from cloud import DOClient
    
    client = DOClient(api_token="xxx")
    droplets = client.list_droplets()
    
    # DigitalOcean (async)
    from cloud import AsyncDOClient
    
    async with AsyncDOClient(api_token="xxx") as client:
        droplets = await client.list_droplets()
    
    # Cloudflare (sync)
    from cloud import CloudflareClient
    
    cf = CloudflareClient(api_token="...")
    cf.upsert_a_record("api.example.com", "1.2.3.4")
    
    # Cloudflare (async)
    from cloud import AsyncCloudflareClient
    
    async with AsyncCloudflareClient(api_token="...") as cf:
        await cf.upsert_a_record("api.example.com", "1.2.3.4")
    
    # LLM - OpenAI
    from cloud.llm import AsyncOpenAICompatClient
    
    async with AsyncOpenAICompatClient(api_key="sk-...") as client:
        response = await client.chat([{"role": "user", "content": "Hello!"}])
        print(response.content)
    
    # LLM - Anthropic Claude
    from cloud.llm import AsyncAnthropicClient
    
    async with AsyncAnthropicClient(api_key="sk-ant-...") as client:
        response = await client.chat(
            messages=[{"role": "user", "content": "Hello!"}],
            system="You are helpful.",
        )

Configuration:
    from cloud import CloudClientConfig, DOClient
    
    config = CloudClientConfig(
        timeout=60.0,
        max_retries=5,
    )
    
    client = DOClient(api_token="xxx", config=config)

Shutdown:
    from cloud import close_all_cloud_clients
    
    # In FastAPI shutdown handler
    await close_all_cloud_clients()
"""

# Configuration
from .base import (
    CloudClientConfig,
    close_all_cloud_clients,
)

# Errors
from .errors import (
    CloudError,
    DOError,
    CloudflareError,
    StripeError,
    RateLimitError,
    AuthenticationError,
    NotFoundError,
)

# DigitalOcean
from .digitalocean import (
    DOClient,
    AsyncDOClient,
    Droplet,
    DropletSize,
    Region,
    Result,
    MANAGED_TAG,
    DOAPIError,  # Backwards compatibility
)

# Cloudflare
from .cloudflare import (
    CloudflareClient,
    AsyncCloudflareClient,
    DNSRecord,
)

# Stripe
from .stripe import (
    StripeClient,
    AsyncStripeClient,
)

# LLM - re-export from submodule for convenience
from .llm import (
    # Types
    ChatResponse,
    ChatMessage,
    ToolCall,
    # Errors
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
    LLMContextLengthError,
    LLMTimeoutError,
    LLMConnectionError,
    # Clients
    OpenAICompatClient,
    AsyncOpenAICompatClient,
    AnthropicClient,
    AsyncAnthropicClient,
)


__all__ = [
    # Config
    "CloudClientConfig",
    # Connection management
    "close_all_cloud_clients",
    # Errors
    "CloudError",
    "DOError",
    "DOAPIError",
    "CloudflareError",
    "StripeError",
    "RateLimitError",
    "AuthenticationError",
    "NotFoundError",
    # DigitalOcean
    "DOClient",
    "AsyncDOClient",
    "Droplet",
    "DropletSize",
    "Region",
    "Result",
    "MANAGED_TAG",
    # Cloudflare
    "CloudflareClient",
    "AsyncCloudflareClient",
    "DNSRecord",
    # Stripe
    "StripeClient",
    "AsyncStripeClient",
    # LLM Types
    "ChatResponse",
    "ChatMessage",
    "ToolCall",
    # LLM Errors
    "LLMError",
    "LLMRateLimitError",
    "LLMAuthError",
    "LLMContextLengthError",
    "LLMTimeoutError",
    "LLMConnectionError",
    # LLM Clients
    "OpenAICompatClient",
    "AsyncOpenAICompatClient",
    "AnthropicClient",
    "AsyncAnthropicClient",
]
