"""Career insight domain model.

``CareerInsight`` stores the structured output of an AI career analysis run
against a user's resume. Each record is immutable after creation — running a
new analysis produces a new row rather than updating an existing one, which
preserves history and allows the client to display the latest insight.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.resume import Resume
    from backend.models.user import User


class CareerInsight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A persisted AI career analysis for a user's resume.

    Attributes:
        user_id:     FK to the owning user; cascade-deleted with the user.
        resume_id:   FK to the resume the analysis was run against; nulled if
                     the resume is deleted.
        target_role: The optional role the user asked to be analysed against.
        analysis:    Full structured JSON output from the AI career navigator.
    """

    __tablename__ = "career_insights"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    target_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    analysis: Mapped[dict] = mapped_column(nullable=False)  # JSON via type_annotation_map

    user: Mapped[User] = orm_relationship(back_populates="career_insights")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="career_insights")


__all__ = ["CareerInsight"]
