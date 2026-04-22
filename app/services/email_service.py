"""
Transactional email service.

Behavior:
- If ZEPTOMAIL_TOKEN is set in the environment, we send a real email via
  ZeptoMail's HTTPS REST API at ZEPTOMAIL_API_URL. (We use REST, not SMTP,
  because most cloud hosts — Linode included — block outbound SMTP.)
- If not, we fall back to printing the email contents to stdout so the
  password-reset flow stays dev-testable without external credentials.

Keep this function's signature stable — callers shouldn't care whether it's
hitting the real API or stdout.
"""

from __future__ import annotations

import logging

import httpx

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
    """Plain-text alternative. Included so clients that strip HTML still get a readable message."""
    return (
        "Hi,\n\n"
        "We got a request to reset your SellNSettle password. Use the link "
        "below to choose a new one. The link is valid for 30 minutes.\n\n"
        f"{reset_url}\n\n"
        "Didn't request this? You can safely ignore this email — your password "
        "won't change.\n\n"
        "— SellNSettle"
    )


def _build_authorization_header(token: str) -> str:
    """
    ZeptoMail requires 'Authorization: Zoho-enczapikey <token>'.
    If the user pasted the header-formatted value (the UI shows it with the
    prefix), use as-is; otherwise prepend the prefix. Tolerant either way.
    """
    token = token.strip()
    if token.startswith("Zoho-enczapikey "):
        return token
    return f"Zoho-enczapikey {token}"


def _send_via_stdout(to: str, reset_url: str) -> None:
    """Dev fallback — prints the email instead of sending. Used when no token is configured."""
    banner = "========== PASSWORD RESET EMAIL (STDOUT STUB) =========="
    print(banner, flush=True)
    print(f"To: {to}", flush=True)
    print("Subject: Reset your SellNSettle password", flush=True)
    print(f"Link (valid for 30 minutes): {reset_url}", flush=True)
    print("=" * len(banner), flush=True)
    logger.info("Password reset email (stdout stub) for %s — link %s", to, reset_url)


def _send_via_rest(to: str, reset_url: str) -> None:
    """Send the password-reset email via ZeptoMail's HTTPS REST API."""
    payload = {
        "from": {
            "address": settings.EMAIL_FROM_ADDRESS,
            "name": settings.EMAIL_FROM_NAME,
        },
        "to": [{"email_address": {"address": to}}],
        "subject": "Reset your SellNSettle password",
        "htmlbody": _render_reset_html(reset_url),
        "textbody": _render_reset_text(reset_url),
    }
    headers = {
        "Authorization": _build_authorization_header(settings.ZEPTOMAIL_TOKEN),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    response = httpx.post(
        settings.ZEPTOMAIL_API_URL,
        headers=headers,
        json=payload,
        timeout=15.0,
    )

    if response.status_code >= 400:
        # ZeptoMail returns JSON with `error.details` or `message` on failure —
        # surface what we can so the server log is useful.
        body_preview = response.text[:400]
        logger.error(
            "ZeptoMail REST send failed: status=%d body=%s",
            response.status_code,
            body_preview,
        )
        print(
            f"[email] ERROR sending to {to}: HTTP {response.status_code} — {body_preview}",
            flush=True,
        )
        response.raise_for_status()

    logger.info("Password reset email sent via ZeptoMail to %s", to)
    print(f"[email] password-reset email sent via ZeptoMail to {to}", flush=True)


def send_password_reset_email(to: str, reset_url: str) -> None:
    """
    Send a password-reset email. Falls back to stdout when ZEPTOMAIL_TOKEN
    is not set. Raises on non-2xx responses so misconfiguration is visible
    during setup.
    """
    if not settings.ZEPTOMAIL_TOKEN:
        _send_via_stdout(to, reset_url)
        return

    _send_via_rest(to, reset_url)
