"""Deterministic candidate-to-job matching engine.

Pure scoring logic — no I/O, no database, no HTTP, no LLM.
All public functions accept plain Python dicts/lists and return plain values
so they can be tested in isolation and called from any layer.

Scoring formula (weights sum to 1.0):
  total_score = (
      WEIGHT_SKILLS * skill_score  +   # 50 %
      WEIGHT_EXP    * exp_score    +   # 20 %
      WEIGHT_ROLE   * role_score       # 30 %
  ) * 100

Industry is excluded because the Job model has no industry field.
All component scores are in [0.0, 1.0].  The final score is clamped to [0, 100].
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------------ #
# Weights (must sum to 1.0)                                           #
# ------------------------------------------------------------------ #

WEIGHT_SKILLS: float = 0.50
WEIGHT_EXP: float = 0.20
WEIGHT_ROLE: float = 0.30   # absorbs the 15% previously allocated to industry

# ------------------------------------------------------------------ #
# Match-level thresholds                                              #
# ------------------------------------------------------------------ #

LEVEL_STRONG = 85    # 85-100
LEVEL_GOOD = 70      # 70-84
LEVEL_MODERATE = 50  # 50-69
LEVEL_WEAK = 30      # 30-49
# 0-29  → Poor Match

# ------------------------------------------------------------------ #
# Recommendation thresholds                                           #
# ------------------------------------------------------------------ #

REC_APPLY = 75        # 75-100  → Apply
REC_CONSIDER = 55     # 55-74   → Consider
# 0-54              → Low Priority

# ------------------------------------------------------------------ #
# Skill synonyms                                                      #
# ------------------------------------------------------------------ #
# Each group is a frozenset of canonical equivalents.

_SYNONYM_GROUPS: list[frozenset[str]] = [
    frozenset({"seo", "search engine optimization"}),
    frozenset({"sem", "search engine marketing", "paid search"}),
    frozenset({"crm", "customer relationship management"}),
    frozenset({"social media", "social media management", "social media marketing"}),
    frozenset({"content writing", "copywriting", "content creation"}),
    frozenset({"ms excel", "excel", "microsoft excel"}),
    frozenset({"ms word", "word", "microsoft word"}),
    frozenset({"ms office", "microsoft office", "office suite"}),
    frozenset({"ui", "user interface", "ui design"}),
    frozenset({"ux", "user experience", "ux design"}),
    frozenset({"ui/ux", "ux/ui", "ui & ux", "user interface & user experience"}),
    frozenset({"data analysis", "data analytics", "analytics"}),
    frozenset({"digital marketing", "online marketing", "internet marketing"}),
    frozenset({"paid advertising", "paid ads", "ppc", "pay-per-click"}),
    frozenset({"graphic design", "visual design", "design"}),
    frozenset({"video editing", "video production"}),
    frozenset({"brand strategy", "branding", "brand management"}),
    frozenset({"a/b testing", "ab testing", "split testing"}),
    frozenset({"roi tracking", "marketing roi tracking", "roi analysis"}),
    frozenset({"python", "python programming"}),
    frozenset({"javascript", "js"}),
    frozenset({"typescript", "ts"}),
    frozenset({"react", "reactjs", "react.js"}),
    frozenset({"node", "nodejs", "node.js"}),
    frozenset({"postgresql", "postgres"}),
    frozenset({"mongodb", "mongo"}),
    frozenset({"machine learning", "ml"}),
    frozenset({"artificial intelligence", "ai"}),
    frozenset({"project management", "pm"}),
    frozenset({"agile", "agile methodology", "scrum"}),
]

_SYNONYM_MAP: dict[str, int] = {}
for _gid, _group in enumerate(_SYNONYM_GROUPS):
    for _member in _group:
        _SYNONYM_MAP[_member] = _gid

# ------------------------------------------------------------------ #
# Experience level → minimum years                                    #
# ------------------------------------------------------------------ #

_EXP_LEVEL_MIN_YEARS: dict[str, int] = {
    "entry": 0,
    "junior": 1,
    "mid": 3,
    "senior": 6,
    "lead": 8,
    "executive": 12,
}

# ------------------------------------------------------------------ #
# Result dataclass                                                     #
# ------------------------------------------------------------------ #


@dataclass
class MatchResult:
    """Structured output of a single candidate-job match."""

    match_score: int                        # 0-100
    match_level: str                        # "Strong Match" etc.
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    experience_match: bool = False
    role_match: bool = False
    recommendation: str = "Low Priority"   # "Apply" | "Consider" | "Low Priority"

    # Component scores (0-100 each) – useful for debugging/display
    skill_score: int = 0
    experience_score: int = 0
    role_score: int = 0


# ------------------------------------------------------------------ #
# Normalisation helpers                                               #
# ------------------------------------------------------------------ #


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    s = text.strip().lower()
    s = re.sub(r"[,;\\|]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalise_skills(skills: list[Any] | None) -> list[str]:
    if not skills:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for s in skills:
        n = _normalise(str(s))
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _canonical_id(skill: str) -> str | int:
    return _SYNONYM_MAP.get(skill, skill)


# ------------------------------------------------------------------ #
# Skill scoring                                                       #
# ------------------------------------------------------------------ #


def score_skills(
    candidate_skills: list[str],
    candidate_technical: list[str],
    job_required_skills: list[str],
) -> tuple[float, list[str], list[str]]:
    """Return (score 0-1, matched_labels, missing_labels)."""
    if not job_required_skills:
        return 1.0, [], []

    candidate_canonical: dict[str | int, str] = {}
    for s in candidate_skills + candidate_technical:
        n = _normalise(s)          # ensure lower-case for canonical lookup
        cid = _canonical_id(n)
        if cid not in candidate_canonical:
            candidate_canonical[cid] = n   # store normalised label

    matched: list[str] = []
    missing: list[str] = []

    for job_skill in job_required_skills:
        jn = _normalise(job_skill)
        jcid = _canonical_id(jn)
        if jcid in candidate_canonical:
            matched.append(candidate_canonical[jcid])
        else:
            missing.append(job_skill)

    score = len(matched) / len(job_required_skills) if job_required_skills else 1.0
    return min(1.0, score), matched, missing


# ------------------------------------------------------------------ #
# Experience scoring                                                  #
# ------------------------------------------------------------------ #


def _parse_years(text: str | None) -> float | None:
    if not text:
        return None
    t = text.lower().strip()
    m = re.search(r"(\d+)\s*\+", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*[-\u2013]\s*(\d+)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1))
    return None


def score_experience(
    candidate_years_text: str | None,
    job_experience_level: str | None,
) -> tuple[float, bool]:
    """Return (score 0-1, experience_match_bool)."""
    if not candidate_years_text and not job_experience_level:
        return 1.0, True

    candidate_years = _parse_years(candidate_years_text)

    if not job_experience_level:
        return 1.0, True

    required_min = _EXP_LEVEL_MIN_YEARS.get(_normalise(job_experience_level))
    if required_min is None:
        return 1.0, True

    if candidate_years is None:
        return 0.5, False

    if candidate_years >= required_min:
        return 1.0, True

    score = candidate_years / required_min if required_min > 0 else 1.0
    return min(1.0, score), False


# ------------------------------------------------------------------ #
# Role / title scoring                                                #
# ------------------------------------------------------------------ #


def score_role(
    candidate_roles: list[str],
    job_title: str | None,
) -> tuple[float, bool]:
    """Return (score 0-1, role_match_bool)."""
    if not job_title:
        return 1.0, True
    if not candidate_roles:
        return 0.0, False

    job_tokens = set(_normalise(job_title).split())
    _stop = {"and", "or", "the", "a", "an", "of", "for", "in", "at", "&", "-"}
    job_tokens -= _stop

    if not job_tokens:
        return 1.0, True

    best_overlap = 0.0
    for role in candidate_roles:
        role_tokens = set(_normalise(role).split()) - _stop
        if not role_tokens:
            continue
        overlap = len(job_tokens & role_tokens) / len(job_tokens)
        if overlap > best_overlap:
            best_overlap = overlap

    match = best_overlap >= 0.5
    return best_overlap, match


# ------------------------------------------------------------------ #
# Match level / recommendation helpers                                #
# ------------------------------------------------------------------ #


def _match_level(score: int) -> str:
    if score >= LEVEL_STRONG:
        return "Strong Match"
    if score >= LEVEL_GOOD:
        return "Good Match"
    if score >= LEVEL_MODERATE:
        return "Moderate Match"
    if score >= LEVEL_WEAK:
        return "Weak Match"
    return "Poor Match"


def _recommendation(score: int) -> str:
    if score >= REC_APPLY:
        return "Apply"
    if score >= REC_CONSIDER:
        return "Consider"
    return "Low Priority"


# ------------------------------------------------------------------ #
# Top-level scoring function                                          #
# ------------------------------------------------------------------ #


def compute_match(
    candidate_skills: list[str],
    candidate_technical: list[str],
    candidate_roles: list[str],
    candidate_years_text: str | None,
    job_required_skills: list[str],
    job_title: str | None,
    job_experience_level: str | None,
) -> MatchResult:
    """Compute a deterministic match score between a candidate and a job.

    All inputs are plain Python values — no ORM objects, no HTTP context.
    Industry is excluded because the Job model has no industry field.

    Args:
        candidate_skills: General skills from candidate profile.
        candidate_technical: Technical skills from candidate profile.
        candidate_roles: Recommended roles from candidate profile.
        candidate_years_text: Experience string, e.g. "6 years".
        job_required_skills: Skills extracted from the job description.
        job_title: The job's title.
        job_experience_level: ExperienceLevel enum value, e.g. "senior".

    Returns:
        A :class:`MatchResult` with score, components, and recommendation.
    """
    c_skills = _normalise_skills(candidate_skills)
    c_tech = _normalise_skills(candidate_technical)
    c_roles = [_normalise(r) for r in (candidate_roles or []) if r]
    j_skills = _normalise_skills(job_required_skills)
    j_title = _normalise(job_title)

    skill_s, matched, missing = score_skills(c_skills, c_tech, j_skills)
    exp_s, exp_match = score_experience(candidate_years_text, job_experience_level)
    role_s, role_match = score_role(c_roles, j_title)

    raw = WEIGHT_SKILLS * skill_s + WEIGHT_EXP * exp_s + WEIGHT_ROLE * role_s
    total = max(0, min(100, round(raw * 100)))

    return MatchResult(
        match_score=total,
        match_level=_match_level(total),
        matched_skills=matched,
        missing_skills=missing,
        experience_match=exp_match,
        role_match=role_match,
        recommendation=_recommendation(total),
        skill_score=round(skill_s * 100),
        experience_score=round(exp_s * 100),
        role_score=round(role_s * 100),
    )
