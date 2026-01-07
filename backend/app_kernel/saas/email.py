"""
Email service for SaaS module.

Handles sending invite emails and other SaaS-related notifications.
Uses the emailing module if available, otherwise logs a warning.
"""

from typing import Optional, Callable, Awaitable
import asyncio


# Email sender function type
EmailSenderFn = Callable[[str, str, str, Optional[str]], Awaitable[bool]]

# Global email sender (set by app)
_email_sender: Optional[EmailSenderFn] = None


def set_email_sender(sender: EmailSenderFn) -> None:
    """
    Set the email sender function.
    
    The sender should be an async function with signature:
        async def send(to: str, subject: str, html: str, text: str = None) -> bool
    
    Example using the emailing module:
        from ...emailing import Emailer, EmailConfig
        
        config = EmailConfig(
            provider="smtp",
            from_address="noreply@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
        )
        emailer = Emailer(config)
        
        async def send_email(to, subject, html, text=None):
            try:
                emailer.send_email(
                    subject=subject,
                    recipients=[to],
                    html=html,
                    text=text,
                )
                return True
            except Exception:
                return False
        
        set_email_sender(send_email)
    """
    global _email_sender
    _email_sender = sender


def get_email_sender() -> Optional[EmailSenderFn]:
    """Get the current email sender."""
    return _email_sender


async def send_invite_email(
    to_email: str,
    workspace_name: str,
    inviter_name: str,
    invite_url: str,
    role: str = "member",
) -> bool:
    """
    Send a workspace invite email.
    
    Args:
        to_email: Recipient email
        workspace_name: Name of the workspace
        inviter_name: Name of person who sent invite
        invite_url: Full URL to accept invite
        role: Role being offered (member/admin)
    
    Returns:
        True if sent successfully, False otherwise
    """
    if _email_sender is None:
        # No email sender configured - log and return
        try:
            from ..observability import get_logger
            get_logger().warning(
                f"Email sender not configured - invite email not sent to {to_email}"
            )
        except ImportError:
            pass
        return False
    
    subject = f"You've been invited to join {workspace_name}"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: #4F46E5; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
            .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; border-top: none; }}
            .button {{ display: inline-block; background: #4F46E5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
            .footer {{ font-size: 12px; color: #6b7280; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0;">You're Invited!</h1>
            </div>
            <div class="content">
                <p>Hi there,</p>
                <p><strong>{inviter_name}</strong> has invited you to join <strong>{workspace_name}</strong> as a <strong>{role}</strong>.</p>
                <p>Click the button below to accept the invitation:</p>
                <a href="{invite_url}" class="button">Accept Invitation</a>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all; color: #4F46E5;">{invite_url}</p>
                <p class="footer">
                    This invitation will expire in 7 days.<br>
                    If you didn't expect this invitation, you can safely ignore this email.
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text = f"""
You've been invited to join {workspace_name}

{inviter_name} has invited you to join {workspace_name} as a {role}.

Accept the invitation by visiting:
{invite_url}

This invitation will expire in 7 days.
If you didn't expect this invitation, you can safely ignore this email.
    """
    
    try:
        return await _email_sender(to_email, subject, html, text)
    except Exception:
        return False
