"""
WhatsApp Cloud API integration using PyWA library.
Sends text and document messages via Meta's WhatsApp Cloud API.
"""

from typing import Any

from pywa import WhatsApp
from pywa.types import Document

from app.models.whatsapp_account import WhatsAppAccount


def _create_client(account: WhatsAppAccount) -> WhatsApp:
    """
    Create a PyWA WhatsApp client from account credentials.
    """
    return WhatsApp(
        phone_id=account.phone_number_id,
        token=account.access_token,
    )


def send_text(
    account: WhatsAppAccount,
    to_phone: str,
    text: str,
) -> dict[str, Any]:
    """
    Send a text message via WhatsApp Cloud API using PyWA.
    
    Args:
        account: WhatsAppAccount with credentials and phone_number_id
        to_phone: Recipient phone number (11 digits, no +)
        text: Message text content
    
    Returns:
        dict with 'messages' key containing list of { 'id': whatsapp_message_id }
    
    Raises:
        Exception: If API call fails
    """
    client = _create_client(account)
    
    response = client.send_text(
        to=to_phone,
        text=text,
    )
    
    # PyWA returns a Message object with id attribute
    # Convert to Meta API response format
    return {
        "messages": [
            {
                "id": response.id
            }
        ]
    }


def send_document(
    account: WhatsAppAccount,
    to_phone: str,
    document_url: str,
    file_name: str,
    caption: str | None = None,
) -> dict[str, Any]:
    """
    Send a document message via WhatsApp Cloud API using PyWA.
    
    Args:
        account: WhatsAppAccount with credentials and phone_number_id
        to_phone: Recipient phone number (11 digits, no +)
        document_url: URL to the document file
        file_name: Filename to display in WhatsApp
        caption: Optional caption for the document
    
    Returns:
        dict with 'messages' key containing list of { 'id': whatsapp_message_id }
    
    Raises:
        Exception: If API call fails
    """
    client = _create_client(account)
    
    response = client.send_document(
        to=to_phone,
        document=Document(
            link=document_url,
            filename=file_name,
        ),
        caption=caption,
    )
    
    # PyWA returns a Message object with id attribute
    # Convert to Meta API response format
    return {
        "messages": [
            {
                "id": response.id
            }
        ]
    }
