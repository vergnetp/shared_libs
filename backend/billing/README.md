# Billing Module

Your DB is the golden source. Stripe is just the payment processor.

## Supports All Product Types

| Type | Use Case | Example |
|------|----------|---------|
| `subscription` | Recurring billing | SaaS plans, memberships |
| `one_time` | Digital purchases | Credits, lifetime access, add-ons |
| `physical` | Physical goods | T-shirts, hardware, books |

## Quick Start

**1. Add billing to your manifest.yaml:**

```yaml
name: my-saas
version: "1.0.0"

database:
  type: sqlite
  path: ./data/app.db

auth:
  jwt_secret: ${JWT_SECRET}

saas:
  enabled: true

billing:
  stripe_secret_key: ${STRIPE_SECRET_KEY}
  stripe_publishable_key: ${STRIPE_PUBLISHABLE_KEY}
  stripe_webhook_secret: ${STRIPE_WEBHOOK_SECRET}
  default_currency: usd
  trial_days: 14
  
  products:
    # Subscription plans
    - slug: free
      name: Free Plan
      type: subscription
      features: [basic_access]
      prices:
        - amount: 0
          interval: month
    
    - slug: pro
      name: Pro Plan
      type: subscription
      description: Everything you need to grow
      features: [basic_access, api_access, priority_support]
      prices:
        - amount: 1999
          interval: month
          nickname: monthly
        - amount: 19900
          interval: year
          nickname: yearly
    
    # One-time digital purchase
    - slug: credit-pack-100
      name: 100 Credits
      type: one_time
      description: Add 100 credits to your account
      prices:
        - amount: 999
    
    # Physical product
    - slug: dev-tshirt
      name: Developer T-Shirt
      type: physical
      shippable: true
      description: Premium cotton tee with our logo
      metadata:
        sizes: [S, M, L, XL]
        colors: [black, white, navy]
      prices:
        - amount: 2499
```

**2. Wire it up in main.py:**

```python
from backend.app_kernel import create_service, ServiceConfig

app = create_service(
    name="my-saas",
    routers=[my_router],
    config=ServiceConfig.from_manifest("manifest.yaml"),
    manifest_path="manifest.yaml",  # ← Enables auto-wiring
)
```

**3. Done!** You get all these routes automatically.

| Endpoint | Description |
|----------|-------------|
| **Products** | |
| `GET /billing/products` | List all products (filter by `?product_type=`) |
| `GET /billing/products/{slug}` | Get specific product |
| **Subscriptions** | |
| `GET /billing/subscription` | Current user's subscription |
| `POST /billing/subscribe` | Create subscription checkout |
| `POST /billing/cancel` | Cancel subscription |
| `POST /billing/reactivate` | Reactivate subscription |
| **One-time / Physical** | |
| `POST /billing/purchase` | Create purchase checkout |
| `GET /billing/orders` | List user's orders |
| `GET /billing/orders/{id}` | Get order details |
| `GET /billing/purchased/{slug}` | Check if user bought product |
| **General** | |
| `POST /billing/portal` | Stripe customer portal URL |
| `GET /billing/invoices` | List invoices |
| `GET /billing/access/{feature}` | Check feature access |
| `POST /billing/webhooks/stripe` | Stripe webhook |

## Test vs Live Mode

Stripe test mode is **auto-detected** from your key prefix:
- `sk_test_*` → Test mode (no real charges)
- `sk_live_*` → Live mode (real money!)

### Option 1: Use test keys (recommended for development)

```bash
# .env
STRIPE_SECRET_KEY=sk_test_51abc...
STRIPE_PUBLISHABLE_KEY=pk_test_51abc...
STRIPE_WEBHOOK_SECRET=whsec_test_...
```

### Option 2: Force test mode

```yaml
# manifest.yaml
billing:
  stripe_secret_key: ${STRIPE_SECRET_KEY}
  test_mode: true  # Force test mode even with live keys
```

### Option 3: Separate test/live keys

```bash
# .env - Use test keys in dev, live keys in prod
STRIPE_SECRET_KEY=sk_live_...        # Production
STRIPE_TEST_SECRET_KEY=sk_test_...   # Development
STRIPE_TEST_PUBLISHABLE_KEY=pk_test_...
STRIPE_TEST_WEBHOOK_SECRET=whsec_test_...
```

```yaml
# manifest.yaml
billing:
  stripe_secret_key: ${STRIPE_SECRET_KEY}
  stripe_test_secret_key: ${STRIPE_TEST_SECRET_KEY}
  test_mode: ${STRIPE_TEST_MODE:-false}  # Set STRIPE_TEST_MODE=true in dev
```

### Stripe Test Cards

| Card Number | Result |
|-------------|--------|
| 4242 4242 4242 4242 | Success |
| 4000 0000 0000 0002 | Decline |
| 4000 0000 0000 3220 | 3D Secure |

Use any future expiry, any 3-digit CVC, any postal code.

## Frontend Integration

### Show All Products (Plans + One-time + Physical)

```javascript
// All products
const products = await fetch('/api/v1/billing/products').then(r => r.json());

// Filter by type
const plans = await fetch('/api/v1/billing/products?product_type=subscription').then(r => r.json());
const addons = await fetch('/api/v1/billing/products?product_type=one_time').then(r => r.json());
const merch = await fetch('/api/v1/billing/products?product_type=physical').then(r => r.json());
```

### Subscribe to a Plan

```javascript
// Get checkout URL
const { checkout_url } = await fetch('/api/v1/billing/subscribe', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}` },
  body: JSON.stringify({
    price_id: 'price-uuid-here',
    success_url: 'https://app.example.com/success',
    cancel_url: 'https://app.example.com/pricing',
  }),
}).then(r => r.json());

// Redirect to Stripe Checkout
window.location.href = checkout_url;
```

### Check Current Subscription

```javascript
const subscription = await fetch('/api/v1/billing/subscription', {
  headers: { 'Authorization': `Bearer ${token}` },
}).then(r => r.json());

if (subscription) {
  console.log(`Plan: ${subscription.plan.name}`);
  console.log(`Status: ${subscription.status}`);
  console.log(`Renews: ${subscription.current_period_end}`);
}
```

### Check Feature Access

```javascript
const { has_access } = await fetch('/api/v1/billing/access/api_access', {
  headers: { 'Authorization': `Bearer ${token}` },
}).then(r => r.json());

if (has_access) {
  // Show API features
}
```

### Manage Subscription (Portal)

```javascript
const { portal_url } = await fetch('/api/v1/billing/portal', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}` },
  body: JSON.stringify({
    return_url: 'https://app.example.com/settings',
  }),
}).then(r => r.json());

window.location.href = portal_url;
```

## Stripe Webhook Setup

Configure your Stripe webhook to point to:
```
https://your-app.com/api/v1/billing/webhooks/stripe
```

Events to subscribe to:
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `checkout.session.completed`

## Feature Gating in Routes

```python
from backend.billing import BillingService, BillingConfig

billing_config = BillingConfig.from_env()
billing = BillingService(billing_config)

@router.get("/api-endpoint")
async def api_endpoint(user = Depends(require_auth)):
    async with get_db_connection() as conn:
        if not await billing.user_has_feature(conn, user.id, "api_access"):
            raise HTTPException(403, "Upgrade to Pro for API access")
        
        return {"data": "..."}
```

## Background Jobs

The module includes these background tasks (registered via `BILLING_TASKS`):

| Task | Description |
|------|-------------|
| `billing.sync_product` | Sync product to Stripe |
| `billing.sync_price` | Sync price to Stripe |
| `billing.sync_customer` | Sync customer to Stripe |
| `billing.sync_subscription` | Sync subscription to Stripe |
| `billing.cancel_subscription` | Cancel in Stripe |
| `billing.reconcile_subscription` | Reconcile with Stripe |
| `billing.reconcile_all_subscriptions` | Reconcile all subscriptions |
| `billing.process_webhook` | Async webhook processing |
| `billing.expire_trials` | Daily trial expiration check |
| `billing.cleanup_incomplete` | Clean stale subscriptions |

Queue a job:
```python
from backend.app_kernel import get_job_queue

queue = get_job_queue()
await queue.enqueue("billing.sync_subscription", {"subscription_id": "..."})
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Your Application                        │
├─────────────────────────────────────────────────────────────┤
│  manifest.yaml                                               │
│  ├── billing.products[] ─────► Auto-created at startup       │
│  └── billing.stripe_* ───────► Stripe API credentials        │
├─────────────────────────────────────────────────────────────┤
│  /billing/plans         ◄───── BillingService.list_products  │
│  /billing/subscribe     ◄───── StripeSync.create_checkout    │
│  /billing/webhooks/stripe ───► WebhookHandler                │
├─────────────────────────────────────────────────────────────┤
│  Your Database (golden source)                               │
│  ├── billing_product                                         │
│  ├── billing_price                                           │
│  ├── billing_customer                                        │
│  ├── billing_subscription                                    │
│  └── billing_invoice                                         │
├─────────────────────────────────────────────────────────────┤
│  Stripe (payment processor)                                  │
│  └── IDs stored back in your DB after sync                   │
└─────────────────────────────────────────────────────────────┘
```

## Environment Variables

```bash
# Required
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Optional
BILLING_DEFAULT_CURRENCY=usd
BILLING_TRIAL_DAYS=14
```
