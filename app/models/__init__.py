"""Model registry — import all models so Alembic + SQLAlchemy see them."""

from app.models.checklist import ChecklistItem
from app.models.deadline import Deadline
from app.models.document import Document
from app.models.event import Event
from app.models.kb_chunk import KBChunk
from app.models.lead import Lead
from app.models.profile import Profile
from app.models.service import ServiceProvider
from app.models.source import Source
from app.models.user import User

__all__ = [
    "User",
    "Profile",
    "ChecklistItem",
    "Deadline",
    "Document",
    "Event",
    "ServiceProvider",
    "Source",
    "KBChunk",
    "Lead",
]
