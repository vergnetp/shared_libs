"""
Billing Catalog - Auto-seed products and prices from manifest.

Reads the billing.products section from manifest.yaml and creates/syncs
products and prices at startup.

Usage:
    from .catalog import seed_catalog_from_manifest
    
    async def on_startup():
        configure_billing(billing_config)
        await seed_catalog_from_manifest("manifest.yaml")
"""

import yaml
import re
import os
from typing import Dict, Any, List, Optional
from pathlib import Path

from .config import BillingConfig
from .services import BillingService
from .sync import StripeSync


async def seed_catalog_from_manifest(
    manifest_path: str,
    billing_config: BillingConfig,
    db_connection,
    *,
    sync_to_stripe: bool = True,
    update_existing: bool = False,
) -> Dict[str, Any]:
    """
    Seed products and prices from manifest.yaml.
    
    Args:
        manifest_path: Path to manifest.yaml
        billing_config: BillingConfig instance
        db_connection: Database connection from kernel
        sync_to_stripe: Whether to sync to Stripe after creating
        update_existing: Whether to update existing products (by slug)
    
    Returns:
        Summary of created/updated products and prices
        
    Example manifest.yaml:
        billing:
          products:
            - slug: pro
              name: Pro Plan
              description: Full access to all features
              features: [api_access, priority_support]
              prices:
                - amount: 1999
                  interval: month
                  nickname: monthly
                - amount: 19900
                  interval: year
                  nickname: yearly
    """
    manifest = _load_manifest(manifest_path)
    billing_section = manifest.get("billing", {})
    products_config = billing_section.get("products", [])
    
    if not products_config:
        return {"status": "skipped", "reason": "no products defined"}
    
    billing = BillingService(billing_config)
    sync = StripeSync(billing_config) if sync_to_stripe else None
    
    results = {
        "products_created": [],
        "products_updated": [],
        "products_skipped": [],
        "prices_created": [],
        "errors": [],
    }
    
    for product_def in products_config:
        try:
            product_result = await _ensure_product(
                db_connection, billing, sync,
                product_def, update_existing
            )
            
            if product_result["action"] == "created":
                results["products_created"].append(product_result["product"]["slug"])
            elif product_result["action"] == "updated":
                results["products_updated"].append(product_result["product"]["slug"])
            else:
                results["products_skipped"].append(product_result["product"]["slug"])
            
            # Create prices for this product
            for price_def in product_def.get("prices", []):
                try:
                    price_result = await _ensure_price(
                        db_connection, billing, sync,
                        product_result["product"], price_def
                    )
                    if price_result["action"] == "created":
                        results["prices_created"].append({
                            "product": product_result["product"]["slug"],
                            "amount": price_def["amount"],
                            "interval": price_def.get("interval", "month"),
                        })
                except Exception as e:
                    results["errors"].append({
                        "product": product_def["slug"],
                        "price": price_def,
                        "error": str(e),
                    })
                    
        except Exception as e:
            results["errors"].append({
                "product": product_def.get("slug", "unknown"),
                "error": str(e),
            })
    
    return results


async def _ensure_product(
    conn,
    billing: BillingService,
    sync: Optional[StripeSync],
    product_def: Dict[str, Any],
    update_existing: bool,
) -> Dict[str, Any]:
    """Create or update a product."""
    slug = product_def["slug"]
    
    # Check if exists
    existing = await billing.get_product_by_slug(conn, slug)
    
    if existing:
        if update_existing:
            # Update existing product
            updated = await billing.update_product(
                conn,
                existing["id"],
                name=product_def.get("name", existing["name"]),
                description=product_def.get("description", existing.get("description")),
                features=product_def.get("features", existing.get("features", [])),
                active=product_def.get("active", True),
            )
            if sync:
                await sync.sync_product(conn, billing, product=updated)
            return {"action": "updated", "product": updated}
        else:
            return {"action": "skipped", "product": existing}
    
    # Determine product type
    product_type = product_def.get("type", "subscription")
    shippable = product_def.get("shippable", product_type == "physical")
    
    # Create new product
    product = await billing.create_product(
        conn,
        name=product_def["name"],
        slug=slug,
        description=product_def.get("description"),
        features=product_def.get("features", []),
        metadata=product_def.get("metadata", {}),
        active=product_def.get("active", True),
        product_type=product_type,
        shippable=shippable,
    )
    
    if sync:
        product = await sync.sync_product(conn, billing, product=product)
    
    return {"action": "created", "product": product}


async def _ensure_price(
    conn,
    billing: BillingService,
    sync: Optional[StripeSync],
    product: Dict[str, Any],
    price_def: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a price if it doesn't exist."""
    amount = price_def["amount"]
    interval = price_def.get("interval")  # None for one-time prices
    currency = price_def.get("currency", "usd")
    
    # Check if similar price exists
    existing_prices = await billing.list_prices(conn, product_id=product["id"])
    
    for existing in existing_prices:
        if (existing["amount_cents"] == amount and 
            existing.get("interval") == interval and
            existing["currency"] == currency):
            return {"action": "skipped", "price": existing}
    
    # Create new price
    price = await billing.create_price(
        conn,
        product_id=product["id"],
        amount_cents=amount,
        currency=currency,
        interval=interval,
        interval_count=price_def.get("interval_count", 1),
        nickname=price_def.get("nickname"),
        metadata=price_def.get("metadata", {}),
        skip_product_validation=True,  # We already have the product
    )
    
    if sync:
        price = await sync.sync_price(conn, billing, price=price, product=product)
    
    return {"action": "created", "price": price}


def _load_manifest(manifest_path: str) -> Dict[str, Any]:
    """Load and interpolate manifest.yaml."""
    with open(manifest_path) as f:
        content = f.read()
    
    # Interpolate ${VAR} and ${VAR:-default}
    def replacer(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")
    
    content = re.sub(r'\$\{([^}:]+)(?::-([^}]*))?\}', replacer, content)
    
    return yaml.safe_load(content)


# Convenience function for startup
async def setup_billing_from_manifest(
    manifest_path: str,
    get_db_connection_func,
) -> Dict[str, Any]:
    """
    One-liner billing setup from manifest.
    
    Usage:
        from .catalog import setup_billing_from_manifest
        from ..app_kernel import get_db_connection
        
        async def on_startup():
            result = await setup_billing_from_manifest(
                "manifest.yaml",
                get_db_connection,
            )
            print(f"Created {len(result['products_created'])} products")
    """
    from .jobs import _billing_config, configure_billing
    
    # Load billing config from manifest if not already configured
    manifest = _load_manifest(manifest_path)
    billing_section = manifest.get("billing", {})
    
    if not _billing_config:
        config = BillingConfig.from_manifest(billing_section)
        configure_billing(config)
    else:
        config = _billing_config
    
    async with get_db_connection_func() as conn:
        return await seed_catalog_from_manifest(
            manifest_path,
            config,
            conn,
            sync_to_stripe=True,
        )
