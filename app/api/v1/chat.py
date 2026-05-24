"""Chat endpoint — drives the Claude tool-use agent."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import run_agent
from app.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.base import APIResponse
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=APIResponse[ChatResponse])
async def chat(
    data: ChatRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await run_agent(
        session,
        current_user,
        data.message,
        [m.model_dump() for m in data.history],
    )
    return APIResponse(
        data=ChatResponse(
            reply=result.reply,
            citations=result.citations,
            actions=result.actions,
        )
    )
