"""
Cloud Provider Clients - Unified interface for cloud APIs.

Provides sync and async clients for:
- DigitalOcean (server provisioning, snapshots, VPC, registry)
- Cloudflare (DNS management, multi-server setup)
- Stripe (payments, subscriptions, customers)
- LLM APIs (OpenAI, Anthropic, Groq, Together)

All clients include:
- Automatic retries with exponential backoff
- Circuit breaker to prevent cascade failures
- Request/response tracing
- Unified error handling

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
    
    # LLM - OpenAI (sync)
    from cloud.llm import OpenAICompatClient
    
    client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
    response = client.chat([{"role": "user", "content": "Hello"}])
    
    # LLM - Anthropic (async with streaming)
    from cloud.llm import AsyncAnthropicClient
    
    async with AsyncAnthropicClient(api_key="sk-ant-...") as client:
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")

Configuration:
    from cloud import CloudClientConfig, DOClient
    
    config = CloudClientConfig(
        timeout=60.0,
        max_retries=5,
        circuit_breaker_threshold=10,
    )
    
    client = DOClient(api_token="xxx", config=config)
"""

# Configuration
from .base import CloudClientConfig

# Errors
from .errors import (
    CloudError,
    DOError,
    CloudflareError,
    StripeError,
    RateLimitError,
    AuthenticationError,
    NotFoundError,
    # LLM errors
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
    LLMContextLengthError,
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

# LLM - imported as submodule, access via cloud.llm
# from . import llm  # Uncomment if you want `cloud.llm.OpenAICompatClient`


__all__ = [
    # Config
    "CloudClientConfig",
    # Errors
    "CloudError",
    "DOError",
    "DOAPIError",
    "CloudflareError",
    "StripeError",
    "RateLimitError",
    "AuthenticationError",
    "NotFoundError",
    "LLMError",
    "LLMRateLimitError",
    "LLMAuthError",
    "LLMContextLengthError",
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
]
