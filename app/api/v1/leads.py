"""Monetization leads — callback/quote requests (the Services CTA).

Auth-required: a lead is tied to the user so partners can follow up. The same
endpoint backs the agent's `create_lead` tool and the Services "Get quotes" CTA.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.leads import LeadCreate, LeadOut
from app.services import workspace

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
