"""
Onboarding service — shared logic for both chat-based and form-based onboarding.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.enums import CatalogItemUnit
from app.repositories.business_repository import get_business_by_id
from app.services.pipeline_service import PIPELINE_TEMPLATES, create_persona_pipeline


VALID_PERSONAS = {"interior_designer", "photographer", "coach", "other"}
VALID_LANGUAGES = {"hinglish", "english", "hindi"}


def set_language(
    session: Session,
    business_id: UUID,
    language: str,
) -> Business:
    """Set the preferred language for AI responses."""
    normalized = language.strip().lower()
    if normalized not in VALID_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid language. Must be one of: {', '.join(sorted(VALID_LANGUAGES))}",
        )

    business = get_business_by_id(session, business_id)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    business.preferred_language = normalized
    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def set_persona(
    session: Session,
    business_id: UUID,
    persona: str,
    label: str | None = None,
) -> Business:
    """Set business type and seed the pipeline."""
    if persona not in VALID_PERSONAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid persona. Must be one of: {', '.join(sorted(VALID_PERSONAS))}",
        )

    business = get_business_by_id(session, business_id)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    business.business_type = persona
    business.business_type_label = label.strip() if label else None
    business.onboarding_status = "persona_selected"
    session.add(business)
    session.commit()
    session.refresh(business)

    create_persona_pipeline(session, business_id, persona)

    return business


def add_first_catalog_item(
    session: Session,
    business_id: UUID,
    name: str,
    price: float,
) -> CatalogItem:
    """Add a catalog item during onboarding."""
    from decimal import Decimal

    item = CatalogItem(
        business_id=business_id,
        name=name.strip(),
        default_rate=Decimal(str(price)),
        unit=CatalogItemUnit.PIECE,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def complete_onboarding(
    session: Session,
    business_id: UUID,
    method: str,
) -> Business:
    """Mark onboarding as completed."""
    business = get_business_by_id(session, business_id)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    business.onboarding_status = "completed"
    business.onboarding_method = method
    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def get_pipeline_preview(persona: str) -> list[dict]:
    """Return the pipeline stages for a persona without creating them."""
    stages = PIPELINE_TEMPLATES.get(persona, PIPELINE_TEMPLATES["other"])
    return [{"name": s["name"], "color": s["color"]} for s in stages if s["name"] != "Lost"]
