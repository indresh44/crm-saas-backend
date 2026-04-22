"""
Transactional email service.

Behavior:
- If ZEPTOMAIL_SMTP_PASSWORD is set in the environment, we send a real email
  via ZeptoMail's SMTP relay (smtp.zeptomail.in:587, STARTTLS).
- If not, we fall back to printing the email contents to stdout so the
  password-reset flow stays dev-testable without external credentials.

Keep this function's signature stable — callers shouldn't care whether it's
hitting real SMTP or stdout.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _render_reset_html(reset_url: str) -> str:
    """Minimal branded HTML body. Plain enough to clear spam filters."""
    return f"""\
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color:#1f2937; line-height:1.5; padding:24px 12px; max-width:560px; margin:0 auto;">
  <h2 style="color:#E8862E; margin-top:0;">SellNSettle</h2>
  <p>Hi,</p>
  <p>We got a request to reset your SellNSettle password. Click the button below to choose a new one. The link is valid for 30 minutes.</p>
  <p style="margin:24px 0;">
    <a href="{reset_url}" style="background:#E8862E; color:#ffffff; padding:10px 20px; text-decoration:none; border-radius:6px; display:inline-block; font-weight:600;">Reset password</a>
  </p>
  <p>If the button doesn't work, copy this link into your browser:</p>
  <p style="word-break:break-all; color:#4b5563;"><a href="{reset_url}">{reset_url}</a></p>
  <p style="color:#6b7280; font-size:13px; margin-top:24px;">
    Didn't request this? You can safely ignore this email — your password won't change.
  </p>
</body>
</html>"""


def _render_reset_text(reset_url: str) -> str:
    """Plain-text alternative. Required for clients that don't render HTML."""
    return (
        "Hi,\n\n"
        "We got a request to reset your SellNSettle password. Use the link "
        "below to choose a new one. The link is valid for 30 minutes.\n\n"
        f"{reset_url}\n\n"
        "Didn't request this? You can safely ignore this email — your password "
        "won't change.\n\n"
        "— SellNSettle"
    )


def _send_via_stdout(to: str, reset_url: str) -> None:
    """Dev fallback — prints the email instead of sending. Used when no SMTP password is configured."""
    banner = "========== PASSWORD RESET EMAIL (STDOUT STUB) =========="
    print(banner, flush=True)
    print(f"To: {to}", flush=True)
    print("Subject: Reset your SellNSettle password", flush=True)
    print(f"Link (valid for 30 minutes): {reset_url}", flush=True)
    print("=" * len(banner), flush=True)
    logger.info("Password reset email (stdout stub) for %s — link %s", to, reset_url)


def _send_via_smtp(to: str, reset_url: str) -> None:
    """Send the password-reset email via ZeptoMail SMTP relay."""
    msg = EmailMessage()
    msg["Subject"] = "Reset your SellNSettle password"
    msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
    msg["To"] = to
    msg.set_content(_render_reset_text(reset_url))
    msg.add_alternative(_render_reset_html(reset_url), subtype="html")

    with smtplib.SMTP(settings.ZEPTOMAIL_SMTP_HOST, settings.ZEPTOMAIL_SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(settings.ZEPTOMAIL_SMTP_USERNAME, settings.ZEPTOMAIL_SMTP_PASSWORD)
        server.send_message(msg)
    logger.info("Password reset email sent via ZeptoMail to %s", to)
    print(f"[email] password-reset email sent via ZeptoMail to {to}", flush=True)


def send_password_reset_email(to: str, reset_url: str) -> None:
    """
    Send a password-reset email. Falls back to stdout if ZeptoMail is not
    configured. Raises on unexpected SMTP errors so misconfiguration is
    visible during setup; the caller in auth_service intentionally lets that
    propagate as a 500 in dev — wrap in try/except upstream once production
    flow is stable if we want to swallow failures for enumeration safety.
    """
    if not settings.ZEPTOMAIL_SMTP_PASSWORD:
        _send_via_stdout(to, reset_url)
        return

    try:
        _send_via_smtp(to, reset_url)
    except smtplib.SMTPException as exc:
        logger.error("ZeptoMail SMTP send failed for %s: %s", to, exc)
        print(f"[email] ERROR sending password-reset email to {to}: {exc}", flush=True)
        raise
