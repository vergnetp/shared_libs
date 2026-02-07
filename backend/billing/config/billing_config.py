"""
Billing configuration.

Stripe config only - database connection comes from app_kernel.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import os


@dataclass
class StripeConfig:
    """Stripe API configuration with test/live mode support."""
    secret_key: str
    publishable_key: str
    webhook_secret: str
    api_version: str = "2024-06-20"
    
    # Test mode - can be explicit or auto-detected from key prefix
    test_mode: Optional[bool] = None
    
    # Optional separate test keys (if you want both in config)
    test_secret_key: Optional[str] = None
    test_publishable_key: Optional[str] = None
    test_webhook_secret: Optional[str] = None
    
    def __post_init__(self):
        # Auto-detect test mode from key prefix if not explicitly set
        if self.test_mode is None:
            self.test_mode = self.secret_key.startswith("sk_test_")
        
        # If test_mode is True and we have test keys, use them
        if self.test_mode and self.test_secret_key:
            self.secret_key = self.test_secret_key
            self.publishable_key = self.test_publishable_key or self.publishable_key
            self.webhook_secret = self.test_webhook_secret or self.webhook_secret
        
        if not self.secret_key:
            raise ValueError("Stripe secret_key is required")
        if not self.webhook_secret:
            raise ValueError("Stripe webhook_secret is required")
        
        # Warn if test_mode doesn't match key prefix
        key_is_test = self.secret_key.startswith("sk_test_")
        if self.test_mode and not key_is_test:
            import logging
            logging.getLogger(__name__).warning(
                "Stripe test_mode=True but using live key - this may be unintentional"
            )
        elif not self.test_mode and key_is_test:
            import logging
            logging.getLogger(__name__).warning(
                "Stripe test_mode=False but using test key - switching to test mode"
            )
            self.test_mode = True
    
    @property
    def is_test(self) -> bool:
        """Check if using test mode."""
        return self.test_mode or self.secret_key.startswith("sk_test_")
    
    @property
    def mode_str(self) -> str:
        """Get mode as string for logging."""
        return "test" if self.is_test else "live"
    
    @classmethod
    def from_env(cls, prefix: str = "STRIPE_") -> "StripeConfig":
        """
        Load from environment variables.
        
        Supports:
            STRIPE_SECRET_KEY - Main secret key
            STRIPE_PUBLISHABLE_KEY - Main publishable key
            STRIPE_WEBHOOK_SECRET - Webhook signing secret
            STRIPE_TEST_MODE - Force test mode (true/false)
            
            # Optional separate test keys:
            STRIPE_TEST_SECRET_KEY
            STRIPE_TEST_PUBLISHABLE_KEY
            STRIPE_TEST_WEBHOOK_SECRET
        """
        test_mode_str = os.environ.get(f"{prefix}TEST_MODE", "").lower()
        test_mode = None
        if test_mode_str in ("true", "1", "yes"):
            test_mode = True
        elif test_mode_str in ("false", "0", "no"):
            test_mode = False
        
        return cls(
            secret_key=os.environ.get(f"{prefix}SECRET_KEY", ""),
            publishable_key=os.environ.get(f"{prefix}PUBLISHABLE_KEY", ""),
            webhook_secret=os.environ.get(f"{prefix}WEBHOOK_SECRET", ""),
            api_version=os.environ.get(f"{prefix}API_VERSION", "2024-06-20"),
            test_mode=test_mode,
            test_secret_key=os.environ.get(f"{prefix}TEST_SECRET_KEY"),
            test_publishable_key=os.environ.get(f"{prefix}TEST_PUBLISHABLE_KEY"),
            test_webhook_secret=os.environ.get(f"{prefix}TEST_WEBHOOK_SECRET"),
        )


@dataclass
class BillingConfig:
    """
    Configuration for the billing system.
    
    NOTE: Database connection comes from app_kernel.
    This config is only for Stripe and billing behavior.
    
    Args:
        stripe: Stripe API configuration
        default_currency: Default currency for prices (ISO 4217)
        trial_days: Default trial period in days
        webhook_tolerance: Seconds of tolerance for webhook timestamp
    """
    stripe: StripeConfig
    default_currency: str = "usd"
    trial_days: int = 0
    webhook_tolerance: int = 300
    
    @property
    def is_test_mode(self) -> bool:
        """Check if billing is in test mode."""
        return self.stripe.is_test
    
    @classmethod
    def from_env(cls) -> "BillingConfig":
        """Create config from environment variables."""
        return cls(
            stripe=StripeConfig.from_env(),
            default_currency=os.environ.get("BILLING_DEFAULT_CURRENCY", "usd"),
            trial_days=int(os.environ.get("BILLING_TRIAL_DAYS", "0")),
            webhook_tolerance=int(os.environ.get("BILLING_WEBHOOK_TOLERANCE", "300")),
        )
    
    @classmethod
    def from_manifest(cls, billing_section: Dict[str, Any]) -> "BillingConfig":
        """
        Create config from manifest billing section.
        
        manifest.yaml:
            billing:
              stripe_secret_key: ${STRIPE_SECRET_KEY}
              stripe_publishable_key: ${STRIPE_PUBLISHABLE_KEY}
              stripe_webhook_secret: ${STRIPE_WEBHOOK_SECRET}
              
              # Optional: force test mode
              test_mode: true
              
              # Or use separate test keys:
              stripe_test_secret_key: ${STRIPE_TEST_SECRET_KEY}
              stripe_test_publishable_key: ${STRIPE_TEST_PUBLISHABLE_KEY}
              stripe_test_webhook_secret: ${STRIPE_TEST_WEBHOOK_SECRET}
              
              default_currency: usd
              trial_days: 14
        """
        # Parse test_mode - can be bool, string, or missing
        test_mode_raw = billing_section.get("test_mode")
        test_mode = None
        if test_mode_raw is not None:
            if isinstance(test_mode_raw, bool):
                test_mode = test_mode_raw
            elif str(test_mode_raw).lower() in ("true", "1", "yes"):
                test_mode = True
            elif str(test_mode_raw).lower() in ("false", "0", "no"):
                test_mode = False
        
        stripe_config = StripeConfig(
            secret_key=billing_section.get("stripe_secret_key", ""),
            publishable_key=billing_section.get("stripe_publishable_key", ""),
            webhook_secret=billing_section.get("stripe_webhook_secret", ""),
            test_mode=test_mode,
            test_secret_key=billing_section.get("stripe_test_secret_key"),
            test_publishable_key=billing_section.get("stripe_test_publishable_key"),
            test_webhook_secret=billing_section.get("stripe_test_webhook_secret"),
        )
        
        return cls(
            stripe=stripe_config,
            default_currency=billing_section.get("default_currency", "usd"),
            trial_days=billing_section.get("trial_days", 0),
            webhook_tolerance=billing_section.get("webhook_tolerance", 300),
        )
