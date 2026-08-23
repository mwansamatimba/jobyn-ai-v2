"""Candidate-to-job matching orchestration layer.

Responsibilities:
  1. Accept a candidate profile dict (from Resume.content) and a job
     representation (ORM object or plain dict).
  2. Extract job skills from the description using a controlled vocabulary.
  3. Normalise inputs into the flat lists expected by matching.py.
  4. Call matching.compute_match().
  5. Return the MatchResult.

No database I/O, no HTTP, no LLM.  This layer can be called from a service,
a CLI script, or a test with zero infrastructure.
"""

from __future__ import annotations

import re
from typing import Any

from backend.matching.matching import MatchResult, compute_match

# ------------------------------------------------------------------ #
# Controlled skill vocabulary for description extraction              #
# ------------------------------------------------------------------ #
# Skills are listed in lower-case. The extractor scans the job description
# text for whole-word occurrences of each entry and returns the matched terms.
# Add or remove entries here; do NOT rely on fuzzy/semantic matching.

_KNOWN_SKILLS: list[str] = [
    # Digital / Social Media
    "social media management", "social media marketing", "social media",
    "content writing", "content creation", "copywriting",
    "digital marketing", "online marketing",
    "paid advertising", "paid ads", "ppc", "pay-per-click",
    "seo", "search engine optimization",
    "sem", "search engine marketing",
    "email marketing",
    "influencer marketing",
    "community management",
    "community engagement",
    "brand strategy", "branding", "brand management",
    "graphic design", "visual design",
    "video editing", "video production",
    "photography",
    "a/b testing", "ab testing",
    "analytics", "data analytics", "data analysis",
    "marketing roi tracking", "roi tracking",
    "google analytics",
    "facebook ads", "instagram ads", "tiktok ads",
    "creative briefs",
    "brand guidelines",
    # Tools
    "canva", "adobe indesign", "adobe photoshop", "adobe premiere pro",
    "adobe illustrator", "adobe creative suite",
    "facebook", "instagram", "tiktok", "twitter", "linkedin",
    "excel", "microsoft excel", "ms excel",
    "word", "microsoft word",
    "powerpoint", "microsoft powerpoint",
    "google workspace", "google docs", "google sheets",
    "hubspot", "mailchimp", "hootsuite", "buffer",
    # Engineering / Tech
    "python", "javascript", "typescript", "java", "c#", "c++", "go", "rust",
    "react", "reactjs", "angular", "vue", "vuejs",
    "node", "nodejs", "express", "fastapi", "django", "flask", "spring",
    "html", "css", "html/css",
    "sql", "postgresql", "postgres", "mysql", "sqlite",
    "mongodb", "redis",
    "docker", "kubernetes", "k8s",
    "aws", "azure", "gcp", "google cloud",
    "git", "github", "gitlab",
    "rest api", "restful api", "graphql",
    "machine learning", "ml", "deep learning", "nlp",
    "artificial intelligence", "ai",
    "data science", "data engineering",
    "ci/cd", "devops",
    # Business / Management
    "project management", "agile", "scrum", "kanban",
    "crm", "customer relationship management",
    "business development", "sales", "account management",
    "financial analysis", "budgeting", "forecasting",
    "market research", "competitive analysis",
    "stakeholder management",
    "leadership", "team management",
    "communication", "presentation",
    "problem solving", "critical thinking",
    "time management",
]

# Pre-sort by length descending so longer phrases match before substrings
_SORTED_SKILLS = sorted(_KNOWN_SKILLS, key=len, reverse=True)

# Ambiguous short words that appear in everyday English.  Each maps to a
# regex that must match *somewhere* in the text for the skill to count.
# The qualifier pattern is tested against the full (lowered) text.
_AMBIGUOUS_SKILL_QUALIFIERS: dict[str, re.Pattern[str]] = {
    "go": re.compile(
        r"(?:golang|go\s*lang|\bin\s+go\b|\busing\s+go\b|go\s+for\b|go\s+programming"
        r"|go\s+developer|go\s+engineer|go\s+service|go\s+backend|go\s+api"
        r"|written\s+in\s+go|experience\s+with\s+go|proficien\w*\s+in\s+go"
        r"|knowledge\s+of\s+go)",
        re.IGNORECASE,
    ),
    "rust": re.compile(
        r"(?:rust\s+programming|rust\s+developer|rust\s+engineer|\bin\s+rust\b"
        r"|\busing\s+rust\b|experience\s+with\s+rust|written\s+in\s+rust"
        r"|rust\s+lang|rustlang|knowledge\s+of\s+rust)",
        re.IGNORECASE,
    ),
    "express": re.compile(
        r"(?:express\.js|expressjs|express\s+framework|express\s+server"
        r"|node.*express|express.*middleware|experience\s+with\s+express"
        r"|using\s+express\b|express\s+api)",
        re.IGNORECASE,
    ),
    "node": re.compile(
        r"(?:node\.js|nodejs|node\s+js|node\s+developer|node\s+server"
        r"|node\s+backend|node\s+api|experience\s+with\s+node"
        r"|using\s+node\b|node\s+framework)",
        re.IGNORECASE,
    ),
    "spring": re.compile(
        r"(?:spring\s+boot|spring\s+framework|spring\s+mvc|spring\s+cloud"
        r"|spring\s+security|spring\s+data|java.*spring|spring.*java"
        r"|experience\s+with\s+spring|using\s+spring\b)",
        re.IGNORECASE,
    ),
    "buffer": re.compile(
        r"(?:buffer\s+(?:app|tool|platform|integration|api)|(?:hootsuite|social).*buffer"
        r"|buffer.*(?:hootsuite|social)|experience\s+with\s+buffer"
        r"|using\s+buffer\b.*(?:social|schedule|post))",
        re.IGNORECASE,
    ),
}


def _extract_skills_from_text(text: str | None) -> list[str]:
    """Return skills found verbatim in *text* from the controlled vocabulary.

    Uses whole-word boundary matching, case-insensitive. Each skill is returned
    at most once in its canonical (lower-case) form.

    Ambiguous short words (go, rust, express, node, spring, buffer) require
    a nearby technical qualifier to avoid false positives from ordinary English.
    """
    if not text:
        return []
    normalised = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for skill in _SORTED_SKILLS:
        if skill in seen:
            continue
        # Whole-word match — allows punctuation boundaries
        pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])"
        if re.search(pattern, normalised):
            qualifier = _AMBIGUOUS_SKILL_QUALIFIERS.get(skill)
            if qualifier is not None and not qualifier.search(text):
                continue
            found.append(skill)
            seen.add(skill)
    return found


# ------------------------------------------------------------------ #
# Input normalisation helpers                                         #
# ------------------------------------------------------------------ #


def _list_of_strings(value: Any) -> list[str]:
    """Safely coerce to a list of non-empty strings."""
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v and str(v).strip()]
    return []


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# ------------------------------------------------------------------ #
# Public API                                                          #
# ------------------------------------------------------------------ #


def match_candidate_to_job(
    candidate_profile: dict[str, Any],
    job: Any,
) -> MatchResult:
    """Match a candidate profile against a job.

    Args:
        candidate_profile: Dict from ``Resume.content`` with keys such as
            ``skills``, ``technical_skills``, ``recommended_roles``,
            ``years_experience``.
        job: Either a ``backend.models.job.Job`` ORM instance or a plain dict
            with at least a ``title`` key.  Optional keys: ``description``,
            ``experience_level``.

    Returns:
        A :class:`~backend.matching.matching.MatchResult`.
    """
    # ── Extract candidate fields ──────────────────────────────────
    c_skills = _list_of_strings(candidate_profile.get("skills"))
    c_technical = _list_of_strings(candidate_profile.get("technical_skills"))
    c_roles = _list_of_strings(candidate_profile.get("recommended_roles"))
    c_years = _str_or_none(candidate_profile.get("years_experience"))

    # ── Extract job fields (ORM object or plain dict) ─────────────
    if isinstance(job, dict):
        j_title = _str_or_none(job.get("title"))
        j_description = _str_or_none(job.get("description"))
        j_exp_level = _str_or_none(job.get("experience_level"))
    else:
        # ORM object — use attribute access
        j_title = _str_or_none(getattr(job, "title", None))
        j_description = _str_or_none(getattr(job, "description", None))
        raw_exp = getattr(job, "experience_level", None)
        # ExperienceLevel enum → string value; use .value if available
        if raw_exp is not None and hasattr(raw_exp, "value"):
            j_exp_level = _str_or_none(raw_exp.value)
        else:
            j_exp_level = _str_or_none(str(raw_exp) if raw_exp is not None else None)

    # ── Extract required skills from job description ───────────────
    j_skills = _extract_skills_from_text(j_description)

    return compute_match(
        candidate_skills=c_skills,
        candidate_technical=c_technical,
        candidate_roles=c_roles,
        candidate_years_text=c_years,
        job_required_skills=j_skills,
        job_title=j_title,
        job_experience_level=j_exp_level,
    )


__all__ = ["match_candidate_to_job"]
