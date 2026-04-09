"""
Onboarding API — health check + form-based onboarding endpoints.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.config.llm_config import llm_settings
from app.core.config import settings
from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.services import onboarding_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# --- Schemas ---


class HealthCheckResponse(BaseModel):
    ai_available: bool


class SetLanguageRequest(BaseModel):
    language: str


class SetLanguageResponse(BaseModel):
    preferred_language: str


class SetPersonaRequest(BaseModel):
    persona: str
    label: str | None = None


class SetPersonaResponse(BaseModel):
    business_type: str
    onboarding_status: str
    pipeline_stages: list[dict]


class AddCatalogItemRequest(BaseModel):
    name: str
    price: float


class AddCatalogItemResponse(BaseModel):
    item_id: str
    name: str
    price: float


class CompleteOnboardingRequest(BaseModel):
    method: str = "form"


class CompleteOnboardingResponse(BaseModel):
    onboarding_status: str


class PipelinePreviewResponse(BaseModel):
    stages: list[dict]


# --- Routes ---


@router.get("/health", response_model=HealthCheckResponse)
async def check_ai_health(
    current_user: User = Depends(get_current_user),
) -> HealthCheckResponse:
    """Check if AI is available for chat-based onboarding."""
    if settings.FORCE_FORM_ONBOARDING:
        return HealthCheckResponse(ai_available=False)

    try:
        from app.services.llm_service import LLMService

        llm = LLMService()
        response = await asyncio.wait_for(
            llm.chat(
                system_prompt="Reply with exactly: OK",
                messages=[{"role": "user", "content": "health check"}],
                model=llm_settings.primary_model,
            ),
            timeout=4.0,
        )
        return HealthCheckResponse(ai_available=bool(response and response.content))
    except Exception as exc:
        logger.warning("AI health check failed: %s", exc)
        return HealthCheckResponse(ai_available=False)


@router.post("/language", response_model=SetLanguageResponse)
def set_language(
    data: SetLanguageRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SetLanguageResponse:
    """Set preferred language during onboarding."""
    business = onboarding_service.set_language(
        session=session,
        business_id=current_user.business_id,
        language=data.language,
    )
    return SetLanguageResponse(preferred_language=business.preferred_language)


@router.post("/persona", response_model=SetPersonaResponse)
def set_persona(
    data: SetPersonaRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SetPersonaResponse:
    """Set business persona and seed pipeline (form-based onboarding)."""
    business = onboarding_service.set_persona(
        session=session,
        business_id=current_user.business_id,
        persona=data.persona,
        label=data.label,
    )
    stages = onboarding_service.get_pipeline_preview(data.persona)
    return SetPersonaResponse(
        business_type=business.business_type,
        onboarding_status=business.onboarding_status,
        pipeline_stages=stages,
    )


@router.post("/catalog-item", response_model=AddCatalogItemResponse)
def add_catalog_item(
    data: AddCatalogItemRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AddCatalogItemResponse:
    """Add first catalog item during onboarding."""
    item = onboarding_service.add_first_catalog_item(
        session=session,
        business_id=current_user.business_id,
        name=data.name,
        price=data.price,
    )
    return AddCatalogItemResponse(
        item_id=str(item.id),
        name=item.name,
        price=float(item.default_rate),
    )


@router.post("/complete", response_model=CompleteOnboardingResponse)
def complete_onboarding(
    data: CompleteOnboardingRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CompleteOnboardingResponse:
    """Mark onboarding as completed."""
    business = onboarding_service.complete_onboarding(
        session=session,
        business_id=current_user.business_id,
        method=data.method,
    )
    return CompleteOnboardingResponse(onboarding_status=business.onboarding_status)


@router.get("/pipeline-preview/{persona}", response_model=PipelinePreviewResponse)
def get_pipeline_preview(
    persona: str,
    current_user: User = Depends(get_current_user),
) -> PipelinePreviewResponse:
    """Preview pipeline stages for a persona without creating them."""
    stages = onboarding_service.get_pipeline_preview(persona)
    return PipelinePreviewResponse(stages=stages)
