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

__all__ = [
    # Email
    "setup_kernel_email",
    "get_emailer",
    "is_email_configured",
    "send_email",
    "send_email_batch",
    
   
]
