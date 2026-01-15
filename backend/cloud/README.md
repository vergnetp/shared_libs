# Cloud Provider Clients

Unified interface for cloud APIs with built-in resilience (retry, circuit breaker, tracing).

## Installation

```bash
# Requires http_client module
pip install requests aiohttp
```

## Quick Start

### DigitalOcean

```python
# Sync
from cloud import DOClient

client = DOClient(api_token="xxx")
droplets = client.list_droplets()

# Create a droplet
droplet = client.create_droplet(
    name="myapp-api-1",
    region="lon1",
    size="s-1vcpu-1gb",
    project="myproject",
)

# Async
from cloud import AsyncDOClient

async with AsyncDOClient(api_token="xxx") as client:
    droplets = await client.list_droplets()
```

### Cloudflare

```python
# Sync
from cloud import CloudflareClient

cf = CloudflareClient(api_token="...")

# Point domain to server (with Cloudflare proxy)
cf.upsert_a_record("api.example.com", "1.2.3.4", proxied=True)

# Multi-server setup (FREE load balancing)
cf.setup_multi_server("api.example.com", ["1.2.3.4", "5.6.7.8"])

# Async
from cloud import AsyncCloudflareClient

async with AsyncCloudflareClient(api_token="...") as cf:
    await cf.upsert_a_record("api.example.com", "1.2.3.4")
```

### Stripe

```python
# Sync
from cloud import StripeClient

client = StripeClient(api_key="sk_...")

# Create product and price
product = client.create_product(name="Pro Plan", metadata={"local_id": "xxx"})
price = client.create_price(
    product=product["id"],
    unit_amount=1999,  # $19.99
    currency="usd",
    recurring={"interval": "month"},
)

# Create checkout session
session = client.create_checkout_session(
    customer="cus_xxx",
    line_items=[{"price": price["id"], "quantity": 1}],
    mode="subscription",
    success_url="https://example.com/success",
    cancel_url="https://example.com/cancel",
)
# Redirect user to session["url"]

# Async
from cloud import AsyncStripeClient

async with AsyncStripeClient(api_key="sk_...") as client:
    product = await client.create_product(name="Pro Plan")
```

### LLM APIs (OpenAI, Anthropic, Groq)

```python
# OpenAI
from cloud.llm import OpenAICompatClient

client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
response = client.chat([{"role": "user", "content": "Hello!"}])
print(response.content)

# Groq (OpenAI-compatible)
client = OpenAICompatClient(
    api_key="gsk-...",
    base_url="https://api.groq.com/openai/v1",
    model="llama-3.3-70b-versatile"
)

# Anthropic Claude
from cloud.llm import AnthropicClient

client = AnthropicClient(api_key="sk-ant-...")
response = client.chat(
    messages=[{"role": "user", "content": "Hello!"}],
    system="You are helpful.",
)

# Streaming
for chunk in client.chat_stream(messages):
    print(chunk, end="", flush=True)

# Async streaming
from cloud.llm import AsyncAnthropicClient

async with AsyncAnthropicClient(api_key="...") as client:
    async for chunk in client.chat_stream(messages):
        print(chunk, end="")

# Tool calls
response = client.chat(messages, tools=[
    {"name": "get_weather", "description": "...", "parameters": {...}}
])
if response.has_tool_calls:
    for tc in response.tool_calls:
        print(f"{tc.name}({tc.arguments})")
```

See [llm/README.md](llm/README.md) for full documentation.

## Configuration

```python
from cloud import CloudClientConfig, DOClient

config = CloudClientConfig(
    timeout=60.0,                    # Request timeout
    max_retries=5,                   # Max retry attempts
    retry_base_delay=1.0,            # Base delay between retries
    circuit_breaker_threshold=10,    # Failures before circuit opens
    circuit_breaker_timeout=60.0,    # Seconds before circuit resets
)

client = DOClient(api_token="xxx", config=config)
```

## Error Handling

```python
from cloud import (
    DOClient,
    DOError,
    CloudflareError,
    StripeError,
    RateLimitError,
    AuthenticationError,
    NotFoundError,
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
)

# DigitalOcean
try:
    droplet = client.get_droplet(123)
except NotFoundError:
    print("Droplet not found")
except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except AuthenticationError:
    print("Invalid API token")
except DOError as e:
    print(f"DO API error: {e.message} (status: {e.status_code})")

# Stripe
try:
    sub = stripe_client.create_subscription(...)
except StripeError as e:
    print(f"Stripe error: {e.message}")
    print(f"  Type: {e.error_type}")   # e.g., "card_error"
    print(f"  Code: {e.error_code}")   # e.g., "card_declined"
    print(f"  Param: {e.param}")       # e.g., "payment_method"
```

## Safety Features

### Managed Droplet Protection

All droplets created through this client are tagged with `deployed-via-api`.

```python
# Only lists managed droplets (safe default)
droplets = client.list_droplets()

# Include unmanaged droplets (use with caution)
all_droplets = client.list_droplets(include_unmanaged=True)

# Delete refuses unmanaged droplets unless forced
result = client.delete_droplet(123)  # Fails if not managed
result = client.delete_droplet(123, force=True)  # Deletes anyway
```

## Features

### DigitalOcean

| Feature | Methods |
|---------|---------|
| Droplets | `create_droplet`, `get_droplet`, `list_droplets`, `delete_droplet` |
| SSH Keys | `list_ssh_keys`, `add_ssh_key`, `ensure_deployer_key` |
| VPCs | `list_vpcs`, `create_vpc`, `ensure_vpc` |
| Snapshots | `list_snapshots`, `create_snapshot_from_droplet`, `transfer_snapshot` |
| Regions | `list_regions`, `list_sizes` |
| Firewalls | `create_firewall` |
| Registry | `get_registry`, `create_registry`, `get_registry_credentials` |

### Cloudflare

| Feature | Methods |
|---------|---------|
| Zones | `get_zone_id`, `list_zones` |
| Records | `list_records`, `get_record`, `create_record`, `update_record`, `delete_record` |
| A Records | `upsert_a_record` |
| Multi-Server | `setup_multi_server`, `add_server`, `remove_server`, `list_servers` |
| Cleanup | `cleanup_orphaned_records` |

### Stripe

| Feature | Methods |
|---------|---------|
| Products | `create_product`, `modify_product`, `retrieve_product`, `list_products` |
| Prices | `create_price`, `modify_price`, `retrieve_price` |
| Customers | `create_customer`, `modify_customer`, `retrieve_customer`, `delete_customer` |
| Subscriptions | `create_subscription`, `modify_subscription`, `retrieve_subscription`, `cancel_subscription` |
| Payment Methods | `attach_payment_method`, `detach_payment_method`, `retrieve_payment_method`, `list_payment_methods` |
| Checkout | `create_checkout_session`, `retrieve_checkout_session` |
| Portal | `create_portal_session` |
| Invoices | `retrieve_invoice`, `pay_invoice`, `list_invoices` |

## Migration Guide

### From old `infra/cloud/digitalocean/client.py`

```python
# Before
from infra.cloud.digitalocean.client import DOClient, Droplet, DOAPIError

# After
from cloud import DOClient, Droplet, DOAPIError
```

### From old `infra/cloud/cloudflare.py`

```python
# Before
from infra.cloud.cloudflare import CloudflareClient, DNSRecord, CloudflareError

# After
from cloud import CloudflareClient, DNSRecord, CloudflareError
```

### From Stripe SDK in `stripe_sync.py`

```python
# Before
import stripe
stripe.api_key = config.stripe.secret_key
stripe.Product.create(name="Pro", ...)
stripe.Subscription.retrieve(sub_id)

# After
from cloud import StripeClient
client = StripeClient(api_key=config.stripe.secret_key)
client.create_product(name="Pro", ...)
client.retrieve_subscription(sub_id)
```

**Note:** Webhook signature verification should still use the SDK:
```python
import stripe
event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
```

**API is 100% backwards compatible** - just change the import.

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `CloudClientConfig`

Configuration for cloud clients.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `timeout` | | `float` | Config | Request timeout in seconds. Default: 30.0 |
| | `max_retries` | | `int` | Config | Maximum retry attempts. Default: 3 |
| | `retry_base_delay` | | `float` | Config | Base delay between retries. Default: 1.0 |
| | `circuit_breaker_threshold` | | `int` | Config | Failures before circuit opens. Default: 5 |
| | `circuit_breaker_timeout` | | `float` | Config | Seconds before circuit resets. Default: 60.0 |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DOClient`

DigitalOcean API client (sync).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `get_account` | | `Dict[str, Any]` | Account | Get account info. Useful for verifying token. |
| | `create_droplet` | `name: str`, `region: str="lon1"`, `size: str="s-1vcpu-1gb"`, `image: str`, `ssh_keys: List[str]`, `tags: List[str]`, `user_data: str`, `vpc_uuid: str`, `auto_vpc: bool=True`, `project: str`, `environment: str="prod"`, `wait: bool=True`, `wait_timeout: int=300`, `node_agent_api_key: str` | `Droplet` | Droplets | Create a new droplet. |
| | `get_droplet` | `droplet_id: int` | `Optional[Droplet]` | Droplets | Get droplet by ID. |
| | `list_droplets` | `tag: str`, `project: str`, `environment: str`, `service: str`, `include_unmanaged: bool=False`, `page: int=1`, `per_page: int=100` | `List[Droplet]` | Droplets | List droplets (managed only by default). |
| | `tag_droplet` | `droplet_id: int`, `tag: str` | `Result` | Droplets | Add a tag to a droplet. |
| | `delete_droplet` | `droplet_id: int`, `force: bool=False` | `Result` | Droplets | Delete a droplet (refuses unmanaged unless force=True). |
| | `list_ssh_keys` | | `List[Dict]` | SSH Keys | List all SSH keys. |
| | `add_ssh_key` | `name: str`, `public_key: str` | `Dict[str, Any]` | SSH Keys | Add SSH key to account. |
| | `ensure_deployer_key` | | `str` | SSH Keys | Ensure deployer SSH key exists locally and on DO. Returns key ID. |
| | `list_vpcs` | | `List[Dict]` | VPC | List all VPCs. |
| | `get_vpc` | `vpc_id: str` | `Optional[Dict]` | VPC | Get VPC by ID. |
| | `create_vpc` | `name: str`, `region: str`, `ip_range: str="10.120.0.0/20"`, `description: str=""` | `Dict[str, Any]` | VPC | Create a new VPC. |
| | `delete_vpc` | `vpc_id: str` | `bool` | VPC | Delete a VPC. |
| | `ensure_vpc` | `region: str`, `name_prefix: str="deploy-api"`, `ip_range: str` | `str` | VPC | Ensure VPC exists for region. Returns VPC UUID. |
| | `list_regions` | | `List[Dict]` | Regions | List available regions. |
| | `list_sizes` | | `List[Dict]` | Regions | List available sizes. |
| | `create_firewall` | `name: str`, `droplet_ids: List[int]`, `tags: List[str]`, `inbound_rules: List[Dict]`, `outbound_rules: List[Dict]` | `Dict[str, Any]` | Firewall | Create firewall with default SSH/HTTP/HTTPS rules. |
| | `list_snapshots` | `resource_type: str="droplet"`, `page: int=1`, `per_page: int=100` | `List[Dict]` | Snapshots | List snapshots. |
| | `get_snapshot` | `snapshot_id: str` | `Optional[Dict]` | Snapshots | Get snapshot by ID. |
| | `get_snapshot_by_name` | `name: str` | `Optional[Dict]` | Snapshots | Get snapshot by name. |
| | `create_snapshot_from_droplet` | `droplet_id: int`, `name: str`, `wait: bool=True`, `wait_timeout: int=600` | `Dict[str, Any]` | Snapshots | Create snapshot from a droplet. |
| | `delete_snapshot` | `snapshot_id: str` | `Result` | Snapshots | Delete a snapshot. |
| | `transfer_snapshot` | `snapshot_id: str`, `region: str`, `wait: bool=True`, `wait_timeout: int=600` | `Dict[str, Any]` | Snapshots | Transfer snapshot to another region. |
| | `get_action` | `action_id: int` | `Dict[str, Any]` | Actions | Get action status. |
| | `get_registry` | | `Optional[Dict]` | Registry | Get container registry info. |
| | `create_registry` | `name: str`, `region: str="fra1"`, `subscription_tier: str="starter"` | `Dict[str, Any]` | Registry | Create container registry. |
| | `get_registry_credentials` | `read_write: bool=True`, `expiry_seconds: int=3600` | `Dict` | Registry | Get Docker credentials for registry login. |
| | `get_registry_endpoint` | | `Optional[str]` | Registry | Get the registry endpoint URL. |
| | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AsyncDOClient`

DigitalOcean API client (async). Same methods as DOClient but async.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `CloudflareClient`

Cloudflare API client for DNS management (sync).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `get_zone_id` | `domain: str` | `str` | Zones | Get zone ID for a domain. |
| | `list_zones` | | `List[Dict]` | Zones | List all zones in account. |
| | `list_records` | `zone: str`, `zone_id: str`, `record_type: str`, `name: str` | `List[DNSRecord]` | Records | List DNS records. |
| | `get_record` | `domain: str`, `record_type: str="A"` | `Optional[DNSRecord]` | Records | Get a specific DNS record. |
| | `create_record` | `domain: str`, `record_type: str`, `content: str`, `proxied: bool=True`, `ttl: int=1` | `DNSRecord` | Records | Create a DNS record. |
| | `update_record` | `record: DNSRecord`, `record_id: str`, `zone_id: str`, `domain: str`, `record_type: str`, `content: str`, `proxied: bool`, `ttl: int` | `DNSRecord` | Records | Update a DNS record. |
| | `delete_record` | `record: DNSRecord`, `domain: str`, `record_type: str="A"` | `bool` | Records | Delete a DNS record. |
| | `upsert_a_record` | `domain: str`, `ip: str`, `proxied: bool=True`, `ttl: int=1` | `DNSRecord` | Records | Create or update an A record. |
| | `setup_domain` | `domain: str`, `server_ip: str`, `proxied: bool=True` | `DNSRecord` | Convenience | Set up a domain to point to a server. |
| | `remove_domain` | `domain: str` | `bool` | Convenience | Remove a domain's DNS record. |
| | `setup_multi_server` | `domain: str`, `server_ips: List[str]`, `proxied: bool=True` | `List[DNSRecord]` | Multi-Server | Set up domain with multiple servers (FREE load balancing). |
| | `add_server` | `domain: str`, `server_ip: str`, `proxied: bool=True` | `DNSRecord` | Multi-Server | Add a server to existing domain. |
| | `remove_server` | `domain: str`, `server_ip: str` | `bool` | Multi-Server | Remove a server from domain. |
| | `list_servers` | `domain: str` | `List[str]` | Multi-Server | List all server IPs for a domain. |
| | `cleanup_orphaned_records` | `zone: str`, `active_ips: set`, `log_fn: callable` | `Dict[str, Any]` | Cleanup | Remove DNS records pointing to dead IPs. |
| | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AsyncCloudflareClient`

Cloudflare API client (async). Same methods as CloudflareClient but async.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `Droplet`

Droplet (server) info dataclass.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `is_active` | | `bool` | Status | Check if droplet status is "active". |
| `@property` | `is_managed` | | `bool` | Status | Check if droplet has MANAGED_TAG. |
| `@property` | `project` | | `Optional[str]` | Tags | Extract project name from tags (project:xxx). |
| `@property` | `environment` | | `Optional[str]` | Tags | Extract environment from tags (env:xxx). |
| `@property` | `service` | | `Optional[str]` | Tags | Extract service name from tags (service:xxx). |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert to dictionary. |
| `@classmethod` | `from_api` | `data: Dict[str, Any]` | `Droplet` | Factory | Create from DO API response. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DNSRecord`

Cloudflare DNS record dataclass.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod` | `from_api` | `data: Dict[str, Any]`, `zone_id: str` | `DNSRecord` | Factory | Create from Cloudflare API response. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `StripeClient`

Stripe API client (sync).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `create_product` | `name: str`, `description: str`, `active: bool=True`, `metadata: Dict` | `Dict[str, Any]` | Products | Create a product. |
| | `modify_product` | `product_id: str`, `name: str`, `description: str`, `active: bool`, `metadata: Dict` | `Dict[str, Any]` | Products | Update a product. |
| | `retrieve_product` | `product_id: str` | `Dict[str, Any]` | Products | Get a product. |
| | `list_products` | `active: bool`, `limit: int=10` | `Dict[str, Any]` | Products | List products. |
| | `create_price` | `product: str`, `unit_amount: int`, `currency: str="usd"`, `recurring: Dict`, `nickname: str`, `active: bool=True`, `metadata: Dict` | `Dict[str, Any]` | Prices | Create a price. |
| | `modify_price` | `price_id: str`, `active: bool`, `nickname: str`, `metadata: Dict` | `Dict[str, Any]` | Prices | Update a price. |
| | `retrieve_price` | `price_id: str` | `Dict[str, Any]` | Prices | Get a price. |
| | `create_customer` | `email: str`, `name: str`, `metadata: Dict`, `payment_method: str`, `invoice_settings: Dict` | `Dict[str, Any]` | Customers | Create a customer. |
| | `modify_customer` | `customer_id: str`, `email: str`, `name: str`, `metadata: Dict`, `invoice_settings: Dict` | `Dict[str, Any]` | Customers | Update a customer. |
| | `retrieve_customer` | `customer_id: str` | `Dict[str, Any]` | Customers | Get a customer. |
| | `delete_customer` | `customer_id: str` | `Dict[str, Any]` | Customers | Delete a customer. |
| | `create_subscription` | `customer: str`, `items: List[Dict]`, `default_payment_method: str`, `trial_end: int`, `metadata: Dict`, `cancel_at_period_end: bool=False` | `Dict[str, Any]` | Subscriptions | Create a subscription. |
| | `modify_subscription` | `subscription_id: str`, `items: List[Dict]`, `cancel_at_period_end: bool`, `proration_behavior: str`, `metadata: Dict` | `Dict[str, Any]` | Subscriptions | Update a subscription. |
| | `retrieve_subscription` | `subscription_id: str`, `expand: List[str]` | `Dict[str, Any]` | Subscriptions | Get a subscription. |
| | `cancel_subscription` | `subscription_id: str`, `immediately: bool=False` | `Dict[str, Any]` | Subscriptions | Cancel a subscription. |
| | `attach_payment_method` | `payment_method_id: str`, `customer: str` | `Dict[str, Any]` | Payment Methods | Attach payment method to customer. |
| | `detach_payment_method` | `payment_method_id: str` | `Dict[str, Any]` | Payment Methods | Detach payment method. |
| | `retrieve_payment_method` | `payment_method_id: str` | `Dict[str, Any]` | Payment Methods | Get a payment method. |
| | `list_payment_methods` | `customer: str`, `type: str="card"` | `Dict[str, Any]` | Payment Methods | List customer's payment methods. |
| | `create_checkout_session` | `customer: str`, `line_items: List[Dict]`, `mode: str`, `success_url: str`, `cancel_url: str`, `metadata: Dict`, `shipping_address_collection: Dict` | `Dict[str, Any]` | Checkout | Create a Checkout Session. |
| | `retrieve_checkout_session` | `session_id: str`, `expand: List[str]` | `Dict[str, Any]` | Checkout | Get a Checkout Session. |
| | `create_portal_session` | `customer: str`, `return_url: str` | `Dict[str, Any]` | Portal | Create Customer Portal session. |
| | `retrieve_invoice` | `invoice_id: str` | `Dict[str, Any]` | Invoices | Get an invoice. |
| | `pay_invoice` | `invoice_id: str`, `payment_method: str` | `Dict[str, Any]` | Invoices | Pay an invoice. |
| | `list_invoices` | `customer: str`, `subscription: str`, `status: str`, `limit: int=10` | `Dict[str, Any]` | Invoices | List invoices. |
| | `close` | | `None` | Lifecycle | Close the underlying HTTP client. |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AsyncStripeClient`

Stripe API client (async). Same methods as StripeClient but async.

</div>
