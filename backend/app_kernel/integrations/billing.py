"""
Billing Integration - Auto-wire billing from manifest.

When billing: section exists in manifest, kernel:
1. Loads BillingConfig
2. Seeds products/prices from manifest
3. Mounts billing routes
4. Registers billing tasks

Usage in manifest.yaml:
    billing:
      stripe_secret_key: ${STRIPE_SECRET_KEY}
      stripe_publishable_key: ${STRIPE_PUBLISHABLE_KEY}
      stripe_webhook_secret: ${STRIPE_WEBHOOK_SECRET}
      trial_days: 14
      
      products:
        - slug: pro
          name: Pro Plan
          features: [api_access]
          prices:
            - amount: 1999
              interval: month
"""

from typing import Dict, Any, Optional, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Global billing config
_billing_config = None
_billing_enabled = False


def is_billing_configured() -> bool:
    """Check if billing is configured."""
    return _billing_enabled and _billing_config is not None


def get_billing_config():
    """Get billing config (raises if not configured)."""
    if not _billing_config:
        raise RuntimeError("Billing not configured")
    return _billing_config


def setup_kernel_billing(
    billing_section: Dict[str, Any],
    get_db_connection,
    require_auth,
) -> Optional[Any]:
    """
    Setup billing from manifest section.
    
    Called by kernel during bootstrap when billing: section exists.
    
    Returns:
        Tuple of (billing router, billing tasks), or (None, {}) if billing not available
    """
    global _billing_config, _billing_enabled
    
    try:
        from ...billing import (
            BillingConfig,
            configure_billing,
            create_billing_router,
            BILLING_TASKS,
        )
    except ImportError:
        logger.warning("Billing module not found - pip install backend-billing or add to shared_libs")
        return None, {}
    
    # Check if Stripe keys are configured
    stripe_key = billing_section.get("stripe_secret_key", "")
    if not stripe_key or stripe_key.startswith("${"):
        # Key is empty or still has uninterpolated ${VAR} placeholder
        logger.info("Billing: Stripe keys not configured, skipping billing setup")
        logger.info("Billing: Set STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET to enable")
        return None, {}
    
    # Create config from manifest section
    try:
        _billing_config = BillingConfig.from_manifest(billing_section)
    except ValueError as e:
        logger.warning(f"Billing: configuration incomplete - {e}")
        logger.info("Billing: Set Stripe environment variables to enable billing")
        return None, {}
    
    _billing_enabled = True
    
    # Configure for background jobs
    configure_billing(_billing_config)
    
    # Create router
    router = create_billing_router(
        _billing_config,
        get_db_connection,
        require_auth,
    )
    
    mode = "TEST" if _billing_config.is_test_mode else "LIVE"
    logger.info(f"Billing configured from manifest ({mode} mode)")
    
    return router, BILLING_TASKS


async def seed_billing_catalog(manifest_path: str, get_db_connection) -> Dict[str, Any]:
    """
    Seed products/prices from manifest at startup.
    
    Called by kernel during startup if billing is enabled.
    """
    if not _billing_enabled:
        return {"status": "skipped", "reason": "billing not enabled"}
    
    try:
        from ...billing import setup_billing_from_manifest
        
        result = await setup_billing_from_manifest(manifest_path, get_db_connection)
        
        if result.get("products_created"):
            logger.info(f"Billing: created {len(result['products_created'])} products")
        if result.get("prices_created"):
            logger.info(f"Billing: created {len(result['prices_created'])} prices")
        if result.get("errors"):
            for err in result["errors"]:
                logger.error(f"Billing seed error: {err}")
        
        return result
    except ImportError:
        return {"status": "skipped", "reason": "billing module not available"}
    except Exception as e:
        logger.error(f"Billing seed failed: {e}")
        return {"status": "error", "error": str(e)}
