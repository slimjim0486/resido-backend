"""Public Services directory — ranked local providers (the monetization surface).

No auth: like the events feed, this is content, not personal data, so the
listing loads without provisioning a session. Default sort is the Bayesian trust
score (handled in the service layer).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.base import APIResponse
from app.schemas.services import ServiceProviderOut
from app.services import providers as providers_service

router = APIRouter(prefix="/services", tags=["services"])

Session = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=APIResponse[list[ServiceProviderOut]])
async def list_services(
    session: Session,
    category: str | None = Query(default=None),
    area: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
):
    items = await providers_service.list_providers(
        session, category=category, area=area, limit=limit
    )
    return APIResponse(data=[ServiceProviderOut.model_validate(p) for p in items])
