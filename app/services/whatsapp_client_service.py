"""
Thin adapter for Meta WhatsApp Cloud API.
Stub implementation: returns placeholder response. Replace with real httpx/PyWa later.
"""

from typing import Any

from app.models.whatsapp_account import WhatsAppAccount


def send_text(
    account: WhatsAppAccount,
    to_phone: str,
    text: str,
) -> dict[str, Any]:
    """
    Send a text message via WhatsApp Cloud API.
    Returns dict with 'messages' key containing list of { 'id': whatsapp_message_id }.
    """
    # TODO: Replace with real POST to
    # https://graph.facebook.com/v18.0/{phone_number_id}/messages
    # using account.access_token. For now return stub so callers get a message id.
    _ = account, to_phone, text
    return {"messages": [{"id": f"stub-{id(account)}-{hash(text) % 10**8}"}]}


def send_document(
    account: WhatsAppAccount,
    to_phone: str,
    document_url: str,
    file_name: str,
    caption: str | None = None,
) -> dict[str, Any]:
    """
    Send a document message via WhatsApp Cloud API.
    Returns dict with 'messages' key containing list of { 'id': whatsapp_message_id }.
    """
    # TODO: Replace with real API call. Use PyWa or httpx when integrating.
    _ = account, to_phone, document_url, file_name, caption
    return {"messages": [{"id": f"stub-doc-{id(account)}-{hash(document_url) % 10**8}"}]}
