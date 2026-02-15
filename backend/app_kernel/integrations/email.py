"""
Email integration for app_kernel.

Wraps the backend.emailing module and auto-configures it from ServiceConfig.
Provides email capabilities to kernel features like SaaS invites.

SMTP calls are offloaded to a thread executor to avoid blocking the event loop.
"""

import asyncio
from typing import Optional, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from ..bootstrap import ServiceConfig

# Module-level logger
_logger = logging.getLogger(__name__)

# Global emailer instance (set by setup_kernel_email)
_emailer = None
_config = None


def setup_kernel_email(cfg: "ServiceConfig") -> bool:
    """
    Initialize kernel email from ServiceConfig.
    
    Called automatically by bootstrap when email_enabled=True.
    
    Args:
        cfg: ServiceConfig with email settings
        
    Returns:
        True if email was configured successfully
    """
    global _emailer, _config
    
    if not cfg.email_enabled:
        return False
    
    if not cfg.smtp_host:
        _logger.warning("Email enabled but SMTP_HOST not set - email disabled")
        return False
    
    try:
        from ...emailing import Emailer, EmailConfig
        
        email_config = EmailConfig(
            provider=cfg.email_provider,
            from_address=cfg.email_from,
            reply_to=cfg.email_reply_to,
            # SMTP settings
            smtp_host=cfg.smtp_host,
            smtp_port=cfg.smtp_port,
            smtp_user=cfg.smtp_user,
            smtp_password=cfg.smtp_password,
            smtp_use_tls=cfg.smtp_use_tls,
        )
        
        _emailer = Emailer(email_config)
        _config = cfg
        
        # Wire up SaaS email sender
        _setup_saas_email()
        
        _logger.info(f"Email configured: {cfg.smtp_host}:{cfg.smtp_port}")
        return True
        
    except ImportError:
        _logger.warning("backend.emailing not available - email disabled")
        return False
    except Exception as e:
        _logger.error(f"Failed to configure email: {e}")
        return False


def _setup_saas_email():
    """Wire up email sender for SaaS module (invites, etc.)."""
    if _emailer is None:
        return
    
    try:
        from ..saas.email import set_email_sender
        
        async def send_email(to: str, subject: str, html: str, text: str = None) -> bool:
            """Send email using configured emailer (offloaded to thread)."""
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: _emailer.send_email(
                        subject=subject,
                        recipients=[to],
                        html=html,
                        text=text,
                    ),
                )
                return True
            except Exception as e:
                _logger.error(f"Failed to send email to {to}: {e}")
                return False
        
        set_email_sender(send_email)
        _logger.debug("SaaS email sender configured")
        
    except ImportError:
        # SaaS module not available
        pass


def get_emailer():
    """Get the configured emailer instance."""
    return _emailer


def is_email_configured() -> bool:
    """Check if email is configured."""
    return _emailer is not None


async def send_email(
    to: str,
    subject: str,
    html: str = None,
    text: str = None,
    from_address: str = None,
    reply_to: str = None,
) -> bool:
    """
    Send an email using the kernel's configured emailer.
    
    Args:
        to: Recipient email address
        subject: Email subject
        html: HTML body (optional)
        text: Plain text body (optional)
        from_address: Override from address (optional)
        reply_to: Override reply-to (optional)
        
    Returns:
        True if sent successfully
    """
    if _emailer is None:
        _logger.warning(f"Email not configured - cannot send to {to}")
        return False
    
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: _emailer.send_email(
                subject=subject,
                recipients=[to],
                html=html,
                text=text,
                from_address=from_address,
                reply_to=reply_to,
            ),
        )
        return True
    except Exception as e:
        _logger.error(f"Failed to send email to {to}: {e}")
        return False


async def send_email_batch(
    recipients: list,
    subject: str,
    html: str = None,
    text: str = None,
) -> dict:
    """
    Send email to multiple recipients.
    
    Args:
        recipients: List of email addresses
        subject: Email subject
        html: HTML body
        text: Plain text body
        
    Returns:
        Dict with 'sent' and 'failed' counts
    """
    if _emailer is None:
        return {"sent": 0, "failed": len(recipients), "error": "Email not configured"}
    
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: _emailer.send_email(
                subject=subject,
                recipients=recipients,
                html=html,
                text=text,
            ),
        )
        return {"sent": len(recipients), "failed": 0}
    except Exception as e:
        return {"sent": 0, "failed": len(recipients), "error": str(e)}
