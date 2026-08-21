"""Pydantic schema for the Interview Preparation endpoint.

Maps directly to the output of
:class:`~backend.ai.interview_coach.InterviewCoachService.generate_interview_plan`.
"""

from __future__ import annotations

import uuid
from pydantic import BaseModel


class InterviewQuestion(BaseModel):
    """One interview question with guidance."""
    question: str
    category: str
    difficulty: str
    ideal_answer_points: list[str]


class InterviewPrepResponse(BaseModel):
    """Full interview preparation plan returned to the client."""
    application_id: uuid.UUID
    job_id: uuid.UUID
    target_role: str
    readiness_score: int
    interview_questions: list[InterviewQuestion]
    strength_areas: list[str]
    improvement_areas: list[str]
    preparation_plan: list[str]
    final_advice: str
