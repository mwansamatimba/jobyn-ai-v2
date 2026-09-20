"""Zambia location classifier and deterministic job category classifier.

Both classifiers are stateless pure functions — no I/O, no database, no LLM.
They operate on plain strings and return enum value strings suitable for
direct storage in the Job model.

Zambia classifier
-----------------
Determines remote_eligibility and fills country/province/city from a free-text
location string. Rules:

* Zambia cities/provinces → NOT remote; country = Zambia
* "Remote" alone + no geographic restriction → GLOBAL
* "Remote" + "Zambia" → ZAMBIA_ELIGIBLE
* "Remote" + "Africa" (not Zambia-specific) → AFRICA_ELIGIBLE
* "Remote" + US/EU/other restriction → NOT_REMOTE (ineligible for Zambia)
* Location-free remote → RESTRICTIONS_UNCLEAR (not assumed eligible)

Important: "Africa" is NOT automatically "Zambia eligible" — only if the
posting explicitly includes Zambia in the eligibility.

Category classifier
-------------------
Deterministic keyword matching. No LLM. Falls back to "other" if no match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Zambia geography
# ---------------------------------------------------------------------------

_ZAMBIA_PROVINCES: dict[str, str] = {
    "lusaka": "Lusaka Province",
    "copperbelt": "Copperbelt Province",
    "ndola": "Copperbelt Province",
    "kitwe": "Copperbelt Province",
    "livingstone": "Southern Province",
    "central province": "Central Province",
    "southern province": "Southern Province",
    "eastern province": "Eastern Province",
    "northern province": "Northern Province",
    "north-western province": "North-Western Province",
    "northwestern province": "North-Western Province",
    "western province": "Western Province",
    "luapula province": "Luapula Province",
    "muchinga province": "Muchinga Province",
}

# City → province map for common Zambian cities
_ZAMBIA_CITIES: dict[str, str] = {
    "lusaka": "Lusaka Province",
    "ndola": "Copperbelt Province",
    "kitwe": "Copperbelt Province",
    "kabwe": "Central Province",
    "livingstone": "Southern Province",
    "chingola": "Copperbelt Province",
    "mufulira": "Copperbelt Province",
    "luanshya": "Copperbelt Province",
    "chipata": "Eastern Province",
    "kasama": "Northern Province",
    "mongu": "Western Province",
    "solwezi": "North-Western Province",
    "mansa": "Luapula Province",
    "choma": "Southern Province",
    "mazabuka": "Southern Province",
    "kafue": "Lusaka Province",
}

# Known non-Zambia African countries (exclude from "Africa eligible" → Zambia)
_AFRICAN_COUNTRIES = frozenset({
    "nigeria", "kenya", "ghana", "ethiopia", "tanzania", "south africa",
    "egypt", "uganda", "rwanda", "senegal", "cameroon", "ivory coast",
    "cote d'ivoire", "zimbabwe", "mozambique", "malawi", "botswana",
    "namibia", "angola", "congo", "drc", "madagascar", "ethiopia",
    "sudan", "somalia", "tunisia", "morocco", "algeria", "libya",
    "mali", "burkina faso", "niger", "chad",
})

# Phrases that explicitly restrict remote work to non-African/non-Zambia regions
_RESTRICTING_PHRASES = frozenset({
    "us only", "usa only", "united states only", "us-based", "us based",
    "north america only", "us citizens only",
    "eu only", "europe only", "european union",
    "uk only", "united kingdom only",
    "canada only", "australia only",
    "must be authorized to work in the us",
    "must be authorized to work in the united states",
})

# ---------------------------------------------------------------------------
# Location classifier result
# ---------------------------------------------------------------------------

@dataclass
class LocationResult:
    country: str = ""
    province: str = ""
    city: str = ""
    remote_eligibility: str = ""  # RemoteEligibility enum value


def classify_location(location: str, country_hint: str = "") -> LocationResult:
    """Classify a free-text location string into structured fields.

    Args:
        location:     Raw location string from the source connector.
        country_hint: Country name from the source (e.g. ReliefWeb provides
                      a structured country field). Used as a fallback.

    Returns:
        A :class:`LocationResult` with country, province, city, remote_eligibility.
    """
    result = LocationResult()
    if country_hint:
        result.country = country_hint.strip()

    if not location and not country_hint:
        result.remote_eligibility = "restrictions_unclear"
        return result

    lower = location.lower().strip()

    # -----------------------------------------------------------------------
    # Check for explicit geographic restrictions on remote roles
    # -----------------------------------------------------------------------
    for phrase in _RESTRICTING_PHRASES:
        if phrase in lower:
            result.remote_eligibility = "not_remote"
            return result

    # -----------------------------------------------------------------------
    # Check if it is a Zambia-based location
    # -----------------------------------------------------------------------
    zambia_detected = (
        "zambia" in lower
        or country_hint.lower() == "zambia"
        or any(city in lower for city in _ZAMBIA_CITIES)
        or any(prov in lower for prov in _ZAMBIA_PROVINCES)
    )

    if zambia_detected:
        result.country = "Zambia"
        # Province/city
        for city, province in _ZAMBIA_CITIES.items():
            if city in lower:
                result.city = city.title()
                result.province = province
                break
        if not result.province:
            for prov_key, prov_name in _ZAMBIA_PROVINCES.items():
                if prov_key in lower:
                    result.province = prov_name
                    break

        # If "remote" is also in the string → Zambia-eligible remote
        if "remote" in lower:
            result.remote_eligibility = "zambia_eligible"
        else:
            result.remote_eligibility = "not_remote"
        return result

    # -----------------------------------------------------------------------
    # Remote classifications
    # -----------------------------------------------------------------------
    is_remote = "remote" in lower or "work from home" in lower or "wfh" in lower

    if is_remote:
        # Check for Zambia-explicit remote
        if "zambia" in lower:
            result.remote_eligibility = "zambia_eligible"
            result.country = "Zambia"
            return result

        # Africa-eligible (but NOT Zambia-specific)
        africa_keywords = ["africa", "sub-saharan", "sub saharan", "east africa",
                           "west africa", "southern africa", "central africa"]
        if any(kw in lower for kw in africa_keywords):
            result.remote_eligibility = "africa_eligible"
            return result

        # "Worldwide", "Global", "Anywhere" → truly global
        global_keywords = ["worldwide", "global", "anywhere", "international",
                            "all countries", "location independent"]
        if any(kw in lower for kw in global_keywords):
            result.remote_eligibility = "global"
            return result

        # Plain "Remote" with no geographic qualifier
        if lower.strip() in {"remote", "work from home", "wfh", "home-based",
                              "fully remote", "100% remote", "remote (global)"}:
            result.remote_eligibility = "global"
            return result

        # Remote + a known non-Zambia African country name → Africa eligible
        for african_country in _AFRICAN_COUNTRIES:
            if african_country in lower:
                result.remote_eligibility = "africa_eligible"
                result.country = african_country.title()
                return result

        # Remote + unknown qualifier → unclear
        result.remote_eligibility = "restrictions_unclear"
        return result

    # -----------------------------------------------------------------------
    # Non-remote: try to extract country
    # -----------------------------------------------------------------------
    if not result.country:
        result.country = _extract_country(lower, country_hint)

    result.remote_eligibility = "not_remote"
    return result


def _extract_country(lower: str, hint: str) -> str:
    """Best-effort country extraction from a location string."""
    if hint:
        return hint.strip()
    # Check some high-value countries for Jobyn
    country_keywords = {
        "zambia": "Zambia",
        "zimbabwe": "Zimbabwe",
        "malawi": "Malawi",
        "mozambique": "Mozambique",
        "botswana": "Botswana",
        "namibia": "Namibia",
        "south africa": "South Africa",
        "kenya": "Kenya",
        "tanzania": "Tanzania",
        "uganda": "Uganda",
        "nigeria": "Nigeria",
        "ghana": "Ghana",
        "rwanda": "Rwanda",
        "ethiopia": "Ethiopia",
        "egypt": "Egypt",
        "senegal": "Senegal",
    }
    for key, name in country_keywords.items():
        if key in lower:
            return name
    return ""


# ---------------------------------------------------------------------------
# Job category classifier
# ---------------------------------------------------------------------------

# Keyword groups → JobCategory enum value.
# Evaluated in order — first match wins.
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("internships_graduate", [
        "intern", "internship", "graduate trainee", "graduate programme",
        "trainee", "graduate scheme", "attachment", "apprentice",
    ]),
    ("ict_technology", [
        "software", "developer", "engineer", "programmer", "devops",
        "data scientist", "data analyst", "machine learning", "ai ", "artificial intelligence",
        "cybersecurity", "information technology", "it ", "ict", "network",
        "cloud", "frontend", "backend", "full stack", "fullstack",
        "database", "infrastructure", "tech support", "help desk",
        "system admin", "sysadmin", "web developer",
    ]),
    ("healthcare", [
        "doctor", "nurse", "physician", "pharmacist", "midwife",
        "laboratory", "health officer", "medical", "clinical",
        "epidemiologist", "public health", "nutrition", "dentist",
        "health worker", "community health",
    ]),
    ("education", [
        "teacher", "lecturer", "professor", "tutor", "trainer",
        "curriculum", "school", "education officer", "headteacher",
        "headmaster", "headmistress", "academic",
    ]),
    ("ngo_development", [
        "ngo", "humanitarian", "development", "relief", "aid ",
        "unicef", "undp", "unhcr", "wfp", "who ", "fao ", "oxfam",
        "save the children", "world vision", "plan international",
        "msf", "médecins", "peace corps", "usaid", "dfid",
        "programme officer", "program officer", "field officer",
        "monitoring and evaluation", "m&e", "livelihoods",
        "protection officer", "wash", "shelter",
    ]),
    ("project_management", [
        "project manager", "programme manager", "project coordinator",
        "project officer", "pmo", "project lead",
    ]),
    ("accounting_finance", [
        "accountant", "auditor", "finance", "financial", "treasury",
        "budget", "tax", "bookkeeper", "cpa", "acca", "cima",
        "controller", "comptroller", "payroll",
    ]),
    ("human_resources", [
        "human resources", "hr ", "talent acquisition", "recruiter",
        "recruitment", "people operations", "people and culture",
        "organizational development",
    ]),
    ("marketing_communications", [
        "marketing", "communications", "brand", "public relations", "pr ",
        "digital marketing", "content", "social media", "copywriter",
        "advertising", "media", "communications officer",
    ]),
    ("sales", [
        "sales", "business development", "account manager", "account executive",
        "client", "revenue", "commercial",
    ]),
    ("legal", [
        "lawyer", "legal", "attorney", "counsel", "paralegal",
        "compliance officer", "litigation",
    ]),
    ("research", [
        "researcher", "research officer", "research analyst",
        "scientist", "data collection", "survey",
    ]),
    ("administration", [
        "administrator", "administrative", "secretary", "receptionist",
        "office manager", "personal assistant", "pa to",
        "executive assistant", "clerk",
    ]),
    ("customer_service", [
        "customer service", "customer care", "customer support",
        "call centre", "call center", "help desk", "service desk",
    ]),
    ("agriculture", [
        "agriculture", "agronomy", "agronomist", "farmer",
        "livestock", "veterinary", "forestry", "fisheries",
        "food security", "soil",
    ]),
    ("engineering", [
        "civil engineer", "structural engineer", "mechanical engineer",
        "electrical engineer", "construction", "architecture",
        "architect", "quantity surveyor", "surveyor",
    ]),
    ("security", [
        "security", "guard", "safety officer", "fire safety",
    ]),
    ("skilled_trades", [
        "driver", "plumber", "electrician", "mechanic", "technician",
        "carpenter", "welder", "mason",
    ]),
    ("business_management", [
        "manager", "director", "chief executive", "ceo", "coo",
        "managing director", "general manager", "operations",
        "business analyst", "strategy",
    ]),
]


def classify_category(title: str, description: str = "") -> str:
    """Classify a job into a category using keyword matching.

    Args:
        title:       Job title (weighted higher than description).
        description: Job description text (optional fallback).

    Returns:
        A ``JobCategory`` enum value string, or ``"other"`` if no match.
    """
    title_lower = title.lower()
    desc_lower = (description or "")[:2000].lower()  # only scan first 2000 chars

    for category_value, keywords in _CATEGORY_RULES:
        # Title match has priority
        for kw in keywords:
            if kw in title_lower:
                return category_value
        # Description fallback
        for kw in keywords:
            if kw in desc_lower:
                return category_value

    return "other"
