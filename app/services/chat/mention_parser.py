from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session

from app.models.user import User
from app.services import catalog_item_service, customer_service, invoice_service


@dataclass
class ResolvedMention:
    type: str
    id: UUID
    display_name: str
    data: dict


@dataclass
class ParsedMessage:
    original_message: str
    clean_message: str
    mentions: list[ResolvedMention] = field(default_factory=list)
    mention_context: str = ""


MENTION_PATTERN = re.compile(r"@\[([^\]]+)\]\{([^:}]+):([^}]+)\}")


def parse_mentions(message: str) -> list[tuple[str, str, str]]:
    return [
        (match.group(1), match.group(2), match.group(3))
        for match in MENTION_PATTERN.finditer(message)
    ]


def clean_message_for_llm(message: str) -> str:
    return MENTION_PATTERN.sub(r"@\1", message)


def resolve_mentions(
    session: Session,
    current_user: User,
    message: str,
) -> ParsedMessage:
    raw_mentions = parse_mentions(message)
    resolved: list[ResolvedMention] = []

    for display_name, mention_type, id_str in raw_mentions:
        try:
            entity_id = UUID(id_str)
        except ValueError:
            continue

        try:
            if mention_type == "customer":
                customer = customer_service.get_customer(
                    session=session,
                    current_user=current_user,
                    customer_id=entity_id,
                )
                resolved.append(
                    ResolvedMention(
                        type="customer",
                        id=customer.id,
                        display_name=display_name,
                        data={
                            "customer_id": str(customer.id),
                            "name": customer.name,
                            "phone": customer.phone,
                            "email": customer.email,
                        },
                    )
                )
            elif mention_type == "item":
                item = catalog_item_service.get_catalog_item(
                    session=session,
                    current_user=current_user,
                    item_id=entity_id,
                )
                resolved.append(
                    ResolvedMention(
                        type="item",
                        id=item.id,
                        display_name=display_name,
                        data={
                            "catalog_item_id": str(item.id),
                            "name": item.name,
                            "default_rate": float(item.default_rate),
                            "unit": item.custom_unit or item.unit.value,
                            "gst_percent": float(item.gst_percent),
                            "description": item.description,
                            "sac_code": item.sac_code,
                        },
                    )
                )
            elif mention_type == "invoice":
                invoice = invoice_service.get_invoice(
                    session=session,
                    current_user=current_user,
                    invoice_id=entity_id,
                )
                resolved.append(
                    ResolvedMention(
                        type="invoice",
                        id=invoice.id,
                        display_name=display_name,
                        data={
                            "invoice_id": str(invoice.id),
                            "invoice_number": invoice.invoice_number,
                            "total_amount": float(invoice.total_amount),
                            "status": invoice.status.value,
                            "customer_name": getattr(invoice, "customer_name", None),
                            "lead_id": str(invoice.lead_id) if invoice.lead_id else None,
                            "pdf_url": getattr(invoice, "pdf_url", None),
                        },
                    )
                )
        except HTTPException:
            continue

    return ParsedMessage(
        original_message=message,
        clean_message=clean_message_for_llm(message),
        mentions=resolved,
        mention_context=_build_mention_context(resolved),
    )


def _build_mention_context(mentions: list[ResolvedMention]) -> str:
    if not mentions:
        return ""

    lines = ["MENTIONED ENTITIES:"]
    for mention in mentions:
        if mention.type == "customer":
            lines.append(
                f"- Customer: {mention.data['name']} "
                f"(ID: {mention.data['customer_id']}, "
                f"Phone: {mention.data.get('phone') or 'N/A'}, "
                f"Email: {mention.data.get('email') or 'N/A'})"
            )
        elif mention.type == "item":
            description = mention.data.get("description") or "N/A"
            sac = mention.data.get("sac_code") or "N/A"
            lines.append(
                f"- Catalog Item: {mention.data['name']} "
                f"(ID: {mention.data['catalog_item_id']}, "
                f"Rate: Rs. {mention.data['default_rate']}, "
                f"Unit: {mention.data['unit']}, "
                f"GST: {mention.data['gst_percent']}%, "
                f"SAC: {sac}, "
                f"Description: {description})"
            )
        elif mention.type == "invoice":
            customer_text = (
                f", Customer: {mention.data['customer_name']}"
                if mention.data.get("customer_name")
                else ""
            )
            pdf_text = (
                f", PDF: {mention.data['pdf_url']}"
                if mention.data.get("pdf_url")
                else ""
            )
            lines.append(
                f"- Invoice: {mention.data['invoice_number']} "
                f"(ID: {mention.data['invoice_id']}, "
                f"Amount: Rs. {mention.data['total_amount']:,.0f}, "
                f"Status: {mention.data['status']}"
                f"{customer_text}{pdf_text})"
            )
    return "\n".join(lines)
