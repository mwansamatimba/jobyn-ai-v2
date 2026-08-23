from backend.ai.application_copilot import (
    normalize_application_analysis,
)


def test_normalizes_requirement_objects():
    result = normalize_application_analysis(
        {
            "key_selling_points": [
                {
                    "point": "Strong analytics experience",
                    "evidence": "Power BI and Tableau",
                }
            ],
            "matched_requirements": [
                {
                    "requirement": "Analytics",
                    "evidence": "Experience with Power BI and Tableau",
                }
            ],
            "addressed_skill_gaps": [
                {
                    "gap": "SEO",
                    "recommendation": "Highlight transferable digital marketing experience.",
                }
            ],
            "application_tips": [
                {
                    "tip": "Emphasise measurable campaign results",
                    "reason": "The role is performance-focused.",
                }
            ],
        }
    )

    assert isinstance(result["key_selling_points"], list)
    assert isinstance(result["matched_requirements"], list)
    assert isinstance(result["addressed_skill_gaps"], list)
    assert isinstance(result["application_tips"], list)

    assert all(
        isinstance(item, str)
        for item in result["key_selling_points"]
    )

    assert all(
        isinstance(item, str)
        for item in result["matched_requirements"]
    )

    assert all(
        isinstance(item, str)
        for item in result["addressed_skill_gaps"]
    )

    assert all(
        isinstance(item, str)
        for item in result["application_tips"]
    )


def test_normalizes_existing_strings():
    result = normalize_application_analysis(
        {
            "key_selling_points": [
                "Python",
                "FastAPI",
            ],
            "matched_requirements": [
                "Backend development",
            ],
            "addressed_skill_gaps": [
                "Kubernetes",
            ],
            "application_tips": [
                "Highlight API design experience",
            ],
        }
    )

    assert result == {
        "key_selling_points": [
            "Python",
            "FastAPI",
        ],
        "matched_requirements": [
            "Backend development",
        ],
        "addressed_skill_gaps": [
            "Kubernetes",
        ],
        "application_tips": [
            "Highlight API design experience",
        ],
    }