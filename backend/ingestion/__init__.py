"""Jobyn Job Ingestion Engine.

Modular pipeline for collecting job vacancies from official public APIs and
approved feeds, normalising them into PostgreSQL, and keeping them fresh.

Package layout
--------------
  __init__.py        — this file
  schema.py          — NormalizedJob dataclass (connector output contract)
  http_client.py     — shared async HTTP client with timeout/retry
  sanitize.py        — HTML sanitizer and URL validator
  base.py            — JobSourceConnector abstract base class
  classifiers.py     — Zambia location classifier + job category classifier
  dedup.py           — deterministic deduplication and canonical-key logic
  expiry.py          — job lifecycle / expiry detection
  sync.py            — sync orchestrator + per-source metrics
  sources/
    greenhouse.py    — Greenhouse public Job Board API connector
    lever.py         — Lever public postings API connector
    ashby.py         — Ashby public job postings API connector
    reliefweb.py     — ReliefWeb humanitarian jobs API connector
    un_careers.py    — UN Careers official RSS connector
    zambia_partner.py — Future Zambia partner feed connector skeleton
"""
