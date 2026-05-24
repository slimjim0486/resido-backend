"""Generic Pydantic base + response envelopes. Harvested as-is."""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class APIResponse(BaseModel, Generic[T]):
    """Standard response wrapper: {success, data, message}."""

    success: bool = True
    data: T
    message: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response with page metadata."""

    success: bool = True
    data: T
    page: int
    per_page: int
    total: int
    message: Optional[str] = None

    @property
    def total_pages(self) -> int:
        return (self.total + self.per_page - 1) // self.per_page if self.per_page else 0

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1
