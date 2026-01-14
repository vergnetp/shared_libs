"""
app_kernel.integrations - Optional integrations with external modules.

These are auto-configured by the kernel based on ServiceConfig/manifest settings.
"""

from .email import (
    setup_kernel_email,
    get_emailer,
    is_email_configured,
    send_email,
    send_email_batch,
)

from .billing import (
    setup_kernel_billing,
    seed_billing_catalog,
    is_billing_configured,
    get_billing_config,
)

__all__ = [
    # Email
    "setup_kernel_email",
    "get_emailer",
    "is_email_configured",
    "send_email",
    "send_email_batch",
    
    # Billing
    "setup_kernel_billing",
    "seed_billing_catalog",
    "is_billing_configured",
    "get_billing_config",
]
