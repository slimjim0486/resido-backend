"""Chat request/response schemas."""

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)


class Citation(BaseModel):
    title: str | None = None
    url: str
    category: str | None = None
    fetched_at: str | None = None


class AgentAction(BaseModel):
    type: str
    summary: str
    # Populated only for a quote-draft lead so the chat UI can offer a one-tap
    # send (WhatsApp/SMS) the user fires from their own phone. Contact is loaded
    # server-side, never model-supplied.
    channel: str | None = None  # "whatsapp" | "sms" | "none"
    to_number: str | None = None
    message: str | None = None
    provider_name: str | None = None


class ChatResponse(BaseModel):
    reply: str
    citations: list[Citation] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list)
