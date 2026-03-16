"""
Meta WhatsApp webhook: GET for verification, POST for incoming payloads.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.services.whatsapp_webhook_service import process_webhook_payload

router = APIRouter()


@router.get("/whatsapp/webhook", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    """
    Meta verification endpoint. Compare hub.verify_token to config token;
    return hub.challenge on success. TODO: Unify with stored account webhook_verify_token.
    """
    if hub_mode != "subscribe" or not hub_challenge:
        return ""
    expected = settings.whatsapp_webhook_verify_token or ""
    if hub_verify_token != expected:
        return ""
    return hub_challenge


@router.post("/whatsapp/webhook")
async def receive_webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Public webhook receiver; process payload and return 200 quickly."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload:
        try:
            process_webhook_payload(session, payload)
        except Exception:
            pass  # Do not fail the request; log in production
    return {}
