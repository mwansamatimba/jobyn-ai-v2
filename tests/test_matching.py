"""Focused unit tests for the deterministic matching engine.

Tests cover matching.py (pure scoring) and matcher.py (orchestration).
No database, no HTTP, no AI calls.
"""

from __future__ import annotations

import pytest

from backend.matching.matching import (
    MatchResult,
    compute_match,
    score_experience,
    score_role,
    score_skills,
    LEVEL_STRONG, LEVEL_GOOD, LEVEL_MODERATE, LEVEL_WEAK,
    REC_APPLY, REC_CONSIDER,
)
from backend.matching.matcher import match_candidate_to_job, _extract_skills_from_text

# ------------------------------------------------------------------ #
# Realistic fixture: Mwansa's marketing profile                       #
# ------------------------------------------------------------------ #

MARKETING_PROFILE = {
    "name": "Mwansa Matimba",
    "career_level": "Mid-level",
    "years_experience": "6 years",
    "skills": [
        "Social Media Management", "Content Writing", "Digital Advertising",
        "Paid Advertising", "Graphic Design", "Video Editing", "Analytics",
        "Brand Strategy", "Community Engagement", "Copywriting",
        "A/B Testing", "Brand Guidelines Development", "Creative Briefs",
        "Data Analysis", "Marketing ROI Tracking",
    ],
    "technical_skills": [
        "Canva", "Adobe InDesign", "Adobe Photoshop", "Adobe Premiere Pro",
        "Facebook", "Instagram", "TikTok", "Excel",
    ],
    "industries": ["Digital Marketing", "Retail", "Consulting"],
    "recommended_roles": [
        "Marketing Manager", "Digital Marketing Specialist",
        "Social Media Manager", "Content Strategist", "Brand Manager",
        "Digital Content Creator",
    ],
}

DIGITAL_MARKETING_JOB = {
    "title": "Digital Marketing Manager",
    "description": (
        "We are looking for a Digital Marketing Manager to lead our online marketing "
        "efforts. Requirements include: social media management, content writing, "
        "SEO, paid advertising, analytics, brand strategy, and graphic design. "
        "Experience with Canva and Adobe Photoshop is a plus. "
        "Strong copywriting and A/B testing skills required."
    ),
    "experience_level": "mid",
}

UNRELATED_JOB = {
    "title": "Mechanical Engineer",
    "description": (
        "Design and develop mechanical systems. Requires CAD, SolidWorks, "
        "thermodynamics, and fluid mechanics knowledge. Python scripting a plus."
    ),
    "experience_level": "mid",
}

ENTRY_JOB = {
    "title": "Junior Social Media Coordinator",
    "description": "Assist with social media management and content creation.",
    "experience_level": "entry",
}

SENIOR_JOB = {
    "title": "VP of Marketing",
    "description": "Lead global marketing strategy and brand management.",
    "experience_level": "executive",
}


# ------------------------------------------------------------------ #
# 1. Strong match                                                     #
# ------------------------------------------------------------------ #

def test_strong_match_digital_marketing():
    result = match_candidate_to_job(MARKETING_PROFILE, DIGITAL_MARKETING_JOB)
    # Marketing candidate vs digital marketing manager role: Good or Strong match
    assert result.match_score >= LEVEL_GOOD, f"Expected good+ match, got {result.match_score}"
    assert result.match_level in ("Good Match", "Strong Match")
    assert result.recommendation in ("Apply", "Consider")


# ------------------------------------------------------------------ #
# 2. Good / moderate match                                            #
# ------------------------------------------------------------------ #

def test_moderate_match_entry_level():
    result = match_candidate_to_job(MARKETING_PROFILE, ENTRY_JOB)
    # Senior candidate vs entry job: skill match will be high, but role match lower
    assert result.match_score >= LEVEL_MODERATE


# ------------------------------------------------------------------ #
# 3. Weak / poor match — unrelated job                               #
# ------------------------------------------------------------------ #

def test_poor_match_unrelated_role():
    result = match_candidate_to_job(MARKETING_PROFILE, UNRELATED_JOB)
    assert result.match_score < LEVEL_GOOD, f"Expected low score, got {result.match_score}"
    assert result.match_level in ("Poor Match", "Weak Match", "Moderate Match")


# ------------------------------------------------------------------ #
# 4. Skill extraction from description                                #
# ------------------------------------------------------------------ #

def test_skills_extracted_from_description():
    skills = _extract_skills_from_text(DIGITAL_MARKETING_JOB["description"])
    assert "social media management" in skills
    assert "content writing" in skills
    assert "paid advertising" in skills
    assert "analytics" in skills


def test_no_skills_in_empty_description():
    assert _extract_skills_from_text("") == []
    assert _extract_skills_from_text(None) == []


# ------------------------------------------------------------------ #
# 5. Exact and case-insensitive skill matching                        #
# ------------------------------------------------------------------ #

def test_case_insensitive_skill_match():
    score, matched, missing = score_skills(
        ["Python", "JAVASCRIPT"],
        [],
        ["python", "javascript"],
    )
    assert score == 1.0
    assert len(matched) == 2
    assert len(missing) == 0


# ------------------------------------------------------------------ #
# 6. Skill synonym matching                                           #
# ------------------------------------------------------------------ #

def test_synonym_seo():
    score, matched, missing = score_skills(
        ["seo"],
        [],
        ["search engine optimization"],
    )
    assert score == 1.0
    assert len(missing) == 0


def test_synonym_social_media():
    score, matched, missing = score_skills(
        ["social media management"],
        [],
        ["social media marketing"],
    )
    assert score == 1.0


def test_synonym_analytics():
    score, matched, missing = score_skills(
        ["data analytics"],
        [],
        ["analytics"],
    )
    assert score == 1.0


# ------------------------------------------------------------------ #
# 7. Missing skills                                                   #
# ------------------------------------------------------------------ #

def test_missing_skills_returned():
    _, matched, missing = score_skills(
        ["python"],
        [],
        ["python", "kubernetes", "terraform"],
    )
    assert "kubernetes" in missing
    assert "terraform" in missing
    assert "python" in matched


# ------------------------------------------------------------------ #
# 8. Experience match — sufficient years                              #
# ------------------------------------------------------------------ #

def test_experience_match_sufficient():
    score, matched = score_experience("6 years", "senior")
    assert matched is True
    assert score == 1.0


# ------------------------------------------------------------------ #
# 9. Experience mismatch — not enough years                           #
# ------------------------------------------------------------------ #

def test_experience_mismatch():
    score, matched = score_experience("2 years", "senior")  # senior needs 6
    assert matched is False
    assert score < 1.0


def test_experience_range_uses_lower_bound():
    score, matched = score_experience("3-5 years", "mid")  # mid needs 3
    assert matched is True


def test_experience_plus_syntax():
    score, matched = score_experience("5+ years", "mid")
    assert matched is True


# ------------------------------------------------------------------ #
# 10. Missing experience                                              #
# ------------------------------------------------------------------ #

def test_missing_candidate_experience_no_crash():
    score, matched = score_experience(None, "mid")
    assert 0.0 <= score <= 1.0
    # Should not crash; matched is False when candidate years unknown


def test_missing_job_experience_no_penalty():
    score, matched = score_experience("3 years", None)
    assert score == 1.0
    assert matched is True


def test_both_experience_missing_no_penalty():
    score, matched = score_experience(None, None)
    assert score == 1.0
    assert matched is True


# ------------------------------------------------------------------ #
# 11. Role / title match                                              #
# ------------------------------------------------------------------ #

def test_role_match_digital_marketing():
    score, matched = score_role(
        ["digital marketing specialist", "marketing manager"],
        "Digital Marketing Manager",
    )
    assert matched is True
    assert score > 0.5


def test_role_mismatch_unrelated():
    score, matched = score_role(
        ["marketing manager", "social media manager"],
        "Mechanical Engineer",
    )
    assert matched is False
    assert score < 0.5


# ------------------------------------------------------------------ #
# 12. Empty candidate skills                                          #
# ------------------------------------------------------------------ #

def test_empty_candidate_skills_no_crash():
    result = compute_match(
        candidate_skills=[],
        candidate_technical=[],
        candidate_roles=["developer"],
        candidate_years_text="3 years",
        job_required_skills=["python", "sql"],
        job_title="Software Engineer",
        job_experience_level="mid",
    )
    assert 0 <= result.match_score <= 100
    assert len(result.missing_skills) == 2


# ------------------------------------------------------------------ #
# 13. Empty job skills                                                #
# ------------------------------------------------------------------ #

def test_empty_job_skills_no_penalty():
    result = compute_match(
        candidate_skills=["python"],
        candidate_technical=[],
        candidate_roles=["developer"],
        candidate_years_text="3 years",
        job_required_skills=[],      # no skills listed
        job_title="Developer",
        job_experience_level="mid",
    )
    assert result.skill_score == 100  # full credit when no job skills listed


# ------------------------------------------------------------------ #
# 14. Missing optional fields — no crash                              #
# ------------------------------------------------------------------ #

def test_none_fields_no_crash():
    result = compute_match(
        candidate_skills=None,
        candidate_technical=None,
        candidate_roles=None,
        candidate_years_text=None,
        job_required_skills=None,
        job_title=None,
        job_experience_level=None,
    )
    assert isinstance(result, MatchResult)
    assert 0 <= result.match_score <= 100


def test_matcher_with_minimal_profile():
    result = match_candidate_to_job({}, {"title": "Engineer"})
    assert isinstance(result, MatchResult)


def test_matcher_with_none_description():
    result = match_candidate_to_job(
        MARKETING_PROFILE,
        {"title": "Marketing Manager", "description": None, "experience_level": "mid"},
    )
    assert isinstance(result, MatchResult)
    assert 0 <= result.match_score <= 100


# ------------------------------------------------------------------ #
# 15. Score always in [0, 100]                                        #
# ------------------------------------------------------------------ #

def test_score_bounded_full_match():
    result = compute_match(
        ["python", "sql", "react"],
        ["docker", "git"],
        ["software engineer"],
        "8 years",
        ["python", "sql", "react"],
        "Software Engineer",
        "senior",
    )
    assert 0 <= result.match_score <= 100


def test_score_bounded_no_match():
    result = compute_match(
        ["knitting", "ceramics"],
        [],
        ["craft artist"],
        "1 year",
        ["python", "kubernetes", "machine learning"],
        "Senior ML Engineer",
        "lead",
    )
    assert 0 <= result.match_score <= 100


# ------------------------------------------------------------------ #
# 16. Recommendation corresponds to score                             #
# ------------------------------------------------------------------ #

def test_recommendation_apply():
    result = compute_match(
        ["social media management", "content writing", "analytics", "copywriting",
         "brand strategy", "graphic design", "a/b testing", "paid advertising"],
        ["canva", "adobe photoshop", "facebook", "instagram"],
        ["digital marketing specialist", "social media manager", "marketing manager"],
        "6 years",
        ["social media management", "content writing", "analytics", "graphic design"],
        "Digital Marketing Specialist",
        "mid",
    )
    if result.match_score >= REC_APPLY:
        assert result.recommendation == "Apply"
    elif result.match_score >= REC_CONSIDER:
        assert result.recommendation == "Consider"
    else:
        assert result.recommendation == "Low Priority"


def test_recommendation_low_priority():
    result = compute_match([], [], [], None, ["python", "ml", "kubernetes"], "ML Engineer", "lead")
    assert result.recommendation == "Low Priority"


# ------------------------------------------------------------------ #
# 17. match_level thresholds                                          #
# ------------------------------------------------------------------ #

def test_match_level_labels():
    for score, expected_level in [
        (90, "Strong Match"),
        (75, "Good Match"),
        (60, "Moderate Match"),
        (35, "Weak Match"),
        (15, "Poor Match"),
    ]:
        from backend.matching.matching import _match_level
        assert _match_level(score) == expected_level, f"score={score}"


# ------------------------------------------------------------------ #
# 18. ORM-like object interface                                       #
# ------------------------------------------------------------------ #

class _FakeJob:
    def __init__(self, title, description, experience_level):
        self.title = title
        self.description = description
        self.experience_level = experience_level


def test_matcher_accepts_orm_like_object():
    job = _FakeJob(
        title="Social Media Manager",
        description="Manage social media management and content creation for our brand.",
        experience_level="mid",
    )
    result = match_candidate_to_job(MARKETING_PROFILE, job)
    assert isinstance(result, MatchResult)
    assert result.match_score > 0


# ------------------------------------------------------------------ #
# 19. Duplicate skills do not inflate score                           #
# ------------------------------------------------------------------ #

def test_duplicate_candidate_skills_deduped():
    score_deduped, m1, _ = score_skills(
        ["python", "python", "Python"],
        [],
        ["python"],
    )
    score_normal, m2, _ = score_skills(["python"], [], ["python"])
    assert score_deduped == score_normal == 1.0


# ------------------------------------------------------------------ #
# 20. Component scores transparency                                   #
# ------------------------------------------------------------------ #

def test_component_scores_present():
    result = match_candidate_to_job(MARKETING_PROFILE, DIGITAL_MARKETING_JOB)
    assert hasattr(result, "skill_score")
    assert hasattr(result, "experience_score")
    assert hasattr(result, "role_score")
    assert 0 <= result.skill_score <= 100
    assert 0 <= result.experience_score <= 100
    assert 0 <= result.role_score <= 100


# ------------------------------------------------------------------ #
# 21. Slash-containing skill synonym normalization                     #
# ------------------------------------------------------------------ #

def test_slash_skill_ui_ux_synonym():
    """ui/ux on candidate should match ux/ui via synonym group."""
    score, matched, missing = score_skills(["ui/ux"], [], ["ux/ui"])
    assert score == 1.0
    assert len(missing) == 0


def test_slash_skill_ci_cd_preserved():
    """ci/cd should not be broken into 'ci cd' by normalization."""
    score, matched, missing = score_skills(["ci/cd"], [], ["ci/cd"])
    assert score == 1.0


def test_slash_skill_a_b_testing_synonym():
    """a/b testing should match ab testing via synonym group."""
    score, matched, missing = score_skills(["a/b testing"], [], ["ab testing"])
    assert score == 1.0
    assert len(missing) == 0


def test_slash_skill_html_css_preserved():
    """html/css should be extractable and matchable."""
    skills = _extract_skills_from_text("Must know html/css and javascript.")
    assert "html/css" in skills


# ------------------------------------------------------------------ #
# 22. ExperienceLevel enum handling                                    #
# ------------------------------------------------------------------ #

def test_experience_level_enum_instance():
    """matcher.py should handle actual ExperienceLevel enum, not just strings."""
    from enum import StrEnum

    class ExperienceLevel(StrEnum):
        ENTRY = "entry"
        JUNIOR = "junior"
        MID = "mid"
        SENIOR = "senior"
        LEAD = "lead"
        EXECUTIVE = "executive"

    class FakeJob:
        def __init__(self):
            self.title = "Senior Backend Engineer"
            self.description = "Python, FastAPI, PostgreSQL experience required."
            self.experience_level = ExperienceLevel.SENIOR

    result = match_candidate_to_job(
        {"skills": ["python"], "technical_skills": ["fastapi", "postgresql"],
         "recommended_roles": ["backend engineer"], "years_experience": "8 years"},
        FakeJob(),
    )
    assert result.experience_match is True
    assert result.experience_score == 100


def test_experience_level_plain_string_still_works():
    """Plain string experience_level should still work after enum fix."""
    result = match_candidate_to_job(
        {"skills": ["python"], "years_experience": "2 years",
         "recommended_roles": ["developer"]},
        {"title": "Engineer", "description": "Python required.", "experience_level": "senior"},
    )
    assert result.experience_match is False
    assert result.experience_score < 100


# ------------------------------------------------------------------ #
# 23. False-positive skill extraction guards                           #
# ------------------------------------------------------------------ #

def test_false_positive_go_in_prose():
    """'go' in ordinary English should NOT be extracted."""
    skills = _extract_skills_from_text("Please go above and beyond in this role.")
    assert "go" not in skills


def test_legitimate_go_extracted():
    """'Go' as a programming language should be extracted."""
    skills = _extract_skills_from_text("We use Go for backend services.")
    assert "go" in skills


def test_false_positive_spring_in_prose():
    """'spring' in 'spring semester' should NOT be extracted."""
    skills = _extract_skills_from_text("Internship available during spring semester.")
    assert "spring" not in skills


def test_legitimate_spring_extracted():
    """'Spring Boot' should trigger spring extraction."""
    skills = _extract_skills_from_text("Spring Boot experience required.")
    assert "spring" in skills


def test_false_positive_express_in_prose():
    """'express' in 'express interest' should NOT be extracted."""
    skills = _extract_skills_from_text("Please express interest in the role.")
    assert "express" not in skills


def test_legitimate_express_extracted():
    """'Express.js' should trigger express extraction."""
    skills = _extract_skills_from_text("Experience with Express.js required.")
    assert "express" in skills


def test_false_positive_rust_in_prose():
    """'rust' in 'rust-proof' should NOT be extracted."""
    skills = _extract_skills_from_text("Must maintain rust-proof coating on equipment.")
    assert "rust" not in skills


def test_legitimate_rust_extracted():
    """Rust programming language should be extracted."""
    skills = _extract_skills_from_text("Experience with Rust programming required.")
    assert "rust" in skills


def test_false_positive_node_in_prose():
    """'node' in 'node of the network' should NOT be extracted."""
    skills = _extract_skills_from_text("Each node of the network must be configured.")
    assert "node" not in skills


def test_legitimate_node_extracted():
    """Node.js should trigger node extraction."""
    skills = _extract_skills_from_text("Build APIs using Node.js and Express.")
    assert "node" in skills
