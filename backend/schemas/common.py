"""Shared Pydantic schemas used across feature modules."""

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base schema that can be populated directly from ORM instances."""

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse[T](BaseModel):
    """Uniform pagination envelope for list endpoints."""

    items: list[T]
    total: int
    offset: int
    limit: int
