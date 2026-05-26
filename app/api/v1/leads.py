"""Monetization leads — callback/quote requests (the Services CTA).

Auth-required: a lead is tied to the user so partners can follow up. The same
endpoint backs the agent's `create_lead` tool and the Services "Get quotes" CTA.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.leads import LeadCreate, LeadOut, QuoteDraftOut, QuoteDraftRequest
from app.services import providers, quotes, workspace

router = APIRouter(prefix="/leads", tags=["leads"])

CurrentUser = Annotated[User, Depends(get_current_active_user)]
Session = Annotated[AsyncSession, Depends(get_db)]


@router.post("", response_model=APIResponse[LeadOut])
async def create_lead(data: LeadCreate, current_user: CurrentUser, session: Session):
    lead = await workspace.create_lead(
        session, current_user.id, vertical=data.vertical, payload=data.payload
    )
    return APIResponse(
        data=LeadOut.model_validate(lead),
        message="We'll connect you with a vetted provider shortly.",
    )


@router.post("/draft", response_model=APIResponse[QuoteDraftOut])
async def draft_quote(data: QuoteDraftRequest, current_user: CurrentUser, session: Session):
    """Draft an AI quote-request the user sends from their own WhatsApp, and
    record the lead. The provider is loaded server-side from ``provider_id``."""
    provider = await providers.get_provider(session, data.provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    result = await quotes.draft_quote(
        session,
        current_user.id,
        provider=provider,
        need=data.need,
        when_pref=data.when_pref,
        budget_aed=data.budget_aed,
        customer_name=data.customer_name,
    )
    return APIResponse(
        data=QuoteDraftOut(**result),
        message="Here's your message — tap send to reach the provider.",
    )
