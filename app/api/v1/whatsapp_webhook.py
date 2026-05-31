"""Meta WhatsApp Cloud API webhook — new Wa* path.

GET  /webhook/whatsapp  — Meta verification handshake
POST /webhook/whatsapp  — signed payload receiver (fast 200 ACK,
                          processing fires off as a background task)

Mounted at root (no /api/v1 prefix) because the URL is configured directly
in the Meta App Dashboard and should be stable across API versioning.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Body, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.wa_credential import WaCredential
from app.services.whatsapp_client_factory import WhatsAppNotConnected
from app.services.whatsapp_service import (
    handle_event,
    send_text_message,
    verify_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    expected = settings.WA_VERIFY_TOKEN
    if hub_mode == "subscribe" and expected and hub_verify_token == expected and hub_challenge:
        return PlainTextResponse(hub_challenge, status_code=200)
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/webhook/whatsapp")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    raw_body = await request.body()

    if not verify_signature(raw_body, x_hub_signature_256):
        logger.warning("WA webhook: signature verification failed")
        return JSONResponse({"detail": "invalid signature"}, status_code=403)

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("WA webhook: failed to decode JSON body")
        # Meta retries on non-2xx; a malformed payload won't get better on retry,
        # so still ACK 200 but skip processing.
        return JSONResponse({"ok": True}, status_code=200)

    # Fast-ACK: hand off to a background task so Meta sees a 200 within
    # milliseconds. handle_event opens its own DB session.
    asyncio.create_task(_safe_handle(payload))

    return JSONResponse({"ok": True}, status_code=200)


# --- TEMPORARY / DEV-ONLY -----------------------------------------------------
# Internal test endpoint to trigger an outbound send without the owner UI.
# No auth, no rate limiting, no business scoping — picks the single seeded
# WaCredential row. REMOVE before any production deploy.
@router.post("/webhook/whatsapp/test-send")
async def test_send(payload: dict = Body(...)):
    to_phone = (payload or {}).get("to_phone")
    body = (payload or {}).get("body")
    if not to_phone or not body:
        raise HTTPException(status_code=400, detail="to_phone and body are required")

    with Session(engine) as s:
        cred = s.exec(select(WaCredential)).first()
    if cred is None:
        raise HTTPException(
            status_code=400,
            detail="No WaCredential row seeded. Insert one before calling test-send.",
        )

    try:
        msg = await send_text_message(
            business_id=cred.business_id,
            to_phone=str(to_phone),
            body=str(body),
        )
    except WhatsAppNotConnected as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        # send_text_message already persisted a failed row before re-raising.
        raise HTTPException(status_code=502, detail=f"send failed: {e}")

    return {
        "ok": True,
        "wamid": msg.wamid,
        "status": msg.status,
        "to": msg.contact_phone,
        "business_id": str(msg.business_id),
    }
# ------------------------------------------------------------------------------


async def _safe_handle(payload: dict) -> None:
    """Run handle_event off the request loop; never let exceptions escape into
    the asyncio task warning."""
    try:
        await asyncio.to_thread(handle_event, payload)
    except Exception:
        logger.exception("WA webhook background task crashed")
