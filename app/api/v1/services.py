"""Public Services directory — ranked local providers (the monetization surface).

Optional auth: like the events feed, this is content, not personal data, so it
loads without a session. Default sort is the Bayesian trust score (handled in the
service layer); a signed-in caller's results are then re-ranked by a *bounded*
taste boost (trust still dominates — see backend/PERSONALIZATION.md).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_optional_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.services import ServiceProviderOut
from app.services import preferences as preferences_service
from app.services import providers as providers_service

router = APIRouter(prefix="/services", tags=["services"])

Session = Annotated[AsyncSession, Depends(get_db)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


@router.get("", response_model=APIResponse[list[ServiceProviderOut]])
async def list_services(
    session: Session,
    user: OptionalUser,
    category: str | None = Query(default=None),
    area: str | None = Query(default=None),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    q: str | None = Query(default=None, description="Keyword search over the provider name"),
    max_price: float | None = Query(default=None, ge=0, description="AED budget ceiling"),
    limit: int = Query(default=20, ge=1, le=50),
):
    """Ranked providers. The optional filters mirror the agent's `find_services`
    so the Services screen and the co-pilot return the same set. A signed-in
    caller's results get a bounded taste re-rank (no-op when anonymous)."""
    personalize = (
        await preferences_service.get_preference_profile(session, user.id) if user else None
    )
    items = await providers_service.list_providers(
        session,
        category=category,
        area=area,
        min_rating=min_rating,
        query=q,
        max_price=max_price,
        limit=limit,
        personalize=personalize,
    )
    out = []
    for prov in items:
        po = ServiceProviderOut.model_validate(prov)
        po.reason = preferences_service.explain_provider(personalize, prov)
        out.append(po)
    return APIResponse(data=out)
