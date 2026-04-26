"""
Transactional email service.

Behavior:
- If ZEPTOMAIL_TOKEN is set in the environment, we send a real email via
  ZeptoMail's HTTPS REST API at ZEPTOMAIL_API_URL. (We use REST, not SMTP,
  because most cloud hosts — Linode included — block outbound SMTP.)
- If not, we fall back to printing the email contents to stdout so flows
  stay dev-testable without external credentials.

Keep public function signatures stable — callers shouldn't care whether
it's hitting the real API or stdout.
"""

from __future__ import annotations

import base64
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings

logger = logging.getLogger(__name__)


TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "email"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=False,
    lstrip_blocks=False,
)


# Logo embedded as inline attachment (cid:sellnsettle-logo in templates).
# Gmail strips base64 data URIs in <img> tags and blocks remote URLs by
# default, but inline-attached images via Content-ID render reliably.
_LOGO_PATH = TEMPLATE_DIR / "sellnsettle-icon.png"
try:
    _LOGO_B64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
except FileNotFoundError:
    _LOGO_B64 = None
    logger.warning("Welcome email logo not found at %s — emails will render without logo", _LOGO_PATH)


def _welcome_inline_images() -> list[dict[str, str]]:
    """ZeptoMail inline_images payload for the welcome email's logo."""
    if not _LOGO_B64:
        return []
    return [
        {
            "mime_type": "image/png",
            "content": _LOGO_B64,
            "cid": "sellnsettle-logo",
        }
    ]


# ── Persona content ──────────────────────────────────────────────────────────

PERSONA_RECENT_LEADS: dict[str, list[dict[str, str]]] = {
    "interior_designer": [
        {"name": "Rajesh Mehta", "desc": "Kitchen renovation — ₹2.5L", "badge_label": "New", "badge_class": "lb-new"},
        {"name": "Priya Sharma", "desc": "Living room redesign — ₹1.8L", "badge_label": "Quoted", "badge_class": "lb-quoted"},
        {"name": "Amit Patel", "desc": "Office interiors — ₹4.5L", "badge_label": "Won", "badge_class": "lb-won"},
    ],
    "photographer": [
        {"name": "Neha Kapoor", "desc": "Pre-wedding shoot — Goa", "badge_label": "New", "badge_class": "lb-new"},
        {"name": "Anjali Reddy", "desc": "Maternity shoot — ₹35K", "badge_label": "Quoted", "badge_class": "lb-quoted"},
        {"name": "Karan Shah", "desc": "Product shoot — jewellery brand", "badge_label": "Won", "badge_class": "lb-won"},
    ],
    "coach": [
        {"name": "Vikram Singh", "desc": "GMAT prep — 3 month plan", "badge_label": "New", "badge_class": "lb-new"},
        {"name": "Aditi Verma", "desc": "1-on-1 mentorship — ₹15K/mo", "badge_label": "Quoted", "badge_class": "lb-quoted"},
        {"name": "Ravi Iyer", "desc": "Group cohort — Sept batch", "badge_label": "Won", "badge_class": "lb-won"},
    ],
    "other": [
        {"name": "Your first enquiry", "desc": "Add a lead to see it here", "badge_label": "New", "badge_class": "lb-new"},
        {"name": "Sample quote", "desc": "Track stages as deals progress", "badge_label": "Quoted", "badge_class": "lb-quoted"},
        {"name": "Closed deal", "desc": "Mark won leads & invoice them", "badge_label": "Won", "badge_class": "lb-won"},
    ],
}

PERSONA_LABEL: dict[str, str] = {
    "interior_designer": "Interior Designer",
    "photographer": "Photographer",
    "coach": "Coach",
    "other": "Business owner",
}


def _resolve_persona(business_type: str | None) -> str:
    """Map free-text business_type to a known persona key, falling back to 'other'."""
    if business_type and business_type in PERSONA_RECENT_LEADS:
        return business_type
    return "other"


# ── Password reset email (existing) ──────────────────────────────────────────


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


# ── ZeptoMail transport ──────────────────────────────────────────────────────


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


def _send_via_zeptomail(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    to_name: str | None = None,
    inline_images: list[dict[str, str]] | None = None,
) -> None:
    """Send an email via ZeptoMail's HTTPS REST API. Raises on non-2xx.

    `inline_images` is the ZeptoMail payload for CID-referenced inline
    attachments — each entry is `{mime_type, content (base64), cid}` and
    is referenced from the HTML via `<img src="cid:<cid>">`. Use this for
    logos / signatures rather than base64 data URIs (which Gmail strips).
    """
    to_address: dict[str, Any] = {"address": to}
    if to_name:
        to_address["name"] = to_name

    payload: dict[str, Any] = {
        "from": {
            "address": settings.EMAIL_FROM_ADDRESS,
            "name": settings.EMAIL_FROM_NAME,
        },
        "to": [{"email_address": to_address}],
        "subject": subject,
        "htmlbody": html,
        "textbody": text,
    }
    if inline_images:
        payload["inline_images"] = inline_images
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
        body_preview = response.text[:400]
        logger.error(
            "ZeptoMail REST send failed: status=%d body=%s",
            response.status_code,
            body_preview,
        )
        _safe_print(
            f"[email] ERROR sending '{subject}' to {to}: HTTP {response.status_code} - {body_preview}"
        )
        response.raise_for_status()

    logger.info("Email '%s' sent via ZeptoMail to %s", subject, to)
    _safe_print(f"[email] '{subject}' sent via ZeptoMail to {to}")


def _send_reset_via_stdout(to: str, reset_url: str) -> None:
    """Dev fallback — prints the email instead of sending."""
    banner = "========== PASSWORD RESET EMAIL (STDOUT STUB) =========="
    print(banner, flush=True)
    print(f"To: {to}", flush=True)
    print("Subject: Reset your SellNSettle password", flush=True)
    print(f"Link (valid for 30 minutes): {reset_url}", flush=True)
    print("=" * len(banner), flush=True)
    logger.info("Password reset email (stdout stub) for %s — link %s", to, reset_url)


def send_password_reset_email(to: str, reset_url: str) -> None:
    """
    Send a password-reset email. Falls back to stdout when ZEPTOMAIL_TOKEN
    is not set. Raises on non-2xx responses so misconfiguration is visible
    during setup.
    """
    if not settings.ZEPTOMAIL_TOKEN:
        _send_reset_via_stdout(to, reset_url)
        return

    _send_via_zeptomail(
        to=to,
        subject="Reset your SellNSettle password",
        html=_render_reset_html(reset_url),
        text=_render_reset_text(reset_url),
    )


# ── Welcome email ────────────────────────────────────────────────────────────


def _build_welcome_context(
    *,
    user_first_name: str,
    business_type: str | None,
    app_url: str,
) -> dict[str, Any]:
    persona = _resolve_persona(business_type)
    return {
        "user_first_name": user_first_name or "there",
        "persona_label": PERSONA_LABEL[persona],
        "recent_leads": PERSONA_RECENT_LEADS[persona],
        "app_url": app_url.rstrip("/"),
        "year": datetime.now(timezone.utc).year,
    }


def _render_welcome(context: dict[str, Any]) -> tuple[str, str]:
    html = _env.get_template("welcome.html").render(**context)
    text = _env.get_template("welcome.txt").render(**context)
    return html, text


def _safe_print(value: str) -> None:
    """Print, replacing characters the local console can't encode.
    Windows defaults to cp1252 which chokes on emoji; the actual SMTP/REST
    payload is utf-8 and unaffected — this only matters for the dev stub."""
    try:
        print(value, flush=True)
    except UnicodeEncodeError:
        encoding = (sys.stdout.encoding or "ascii")
        print(value.encode(encoding, errors="replace").decode(encoding), flush=True)


def _send_welcome_via_stdout(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    banner = "========== WELCOME EMAIL (STDOUT STUB) =========="
    _safe_print(banner)
    _safe_print(f"To: {to}")
    _safe_print(f"Subject: {subject}")
    _safe_print("--- HTML preview (first 500 chars) ---")
    _safe_print(html[:500])
    _safe_print("--- TEXT body ---")
    _safe_print(text)
    _safe_print("=" * len(banner))
    logger.info("Welcome email (stdout stub) for %s", to)


def send_welcome_email(
    to_email: str,
    user_first_name: str,
    business_name: str,
    business_type: str | None,
    app_url: str | None = None,
) -> None:
    """
    Send the persona-aware welcome email. Falls back to stdout when
    ZEPTOMAIL_TOKEN is missing. Swallows delivery errors and logs WARNING
    so a transient outage doesn't bubble up into the user's onboarding flow.
    """
    try:
        resolved_app_url = app_url or "https://sellnsettle.com"
        context = _build_welcome_context(
            user_first_name=user_first_name,
            business_type=business_type,
            app_url=resolved_app_url,
        )
        html, text = _render_welcome(context)
        subject = f"Welcome to SellNSettle, {context['user_first_name']} \U0001F389"

        if not settings.ZEPTOMAIL_TOKEN:
            _send_welcome_via_stdout(to=to_email, subject=subject, html=html, text=text)
            return

        _send_via_zeptomail(
            to=to_email,
            subject=subject,
            html=html,
            text=text,
            to_name=business_name,
            inline_images=_welcome_inline_images(),
        )
    except Exception as exc:  # noqa: BLE001 — email failures must not break onboarding
        logger.warning(
            "Welcome email send failed for %s (business_type=%s): %s",
            to_email,
            business_type,
            exc,
        )
