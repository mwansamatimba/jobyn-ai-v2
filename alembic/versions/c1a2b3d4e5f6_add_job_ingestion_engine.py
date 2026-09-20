"""add job ingestion engine

Adds ingestion metadata columns to the jobs table and creates four new tables
for the Job Ingestion Engine:

  - ingestion_sources     — approved source registry with compliance state
  - job_ingestion_sources — per-source job relationship / deduplication keys
  - job_observations      — raw payload audit log (pruned by retention policy)
  - compliance_events     — immutable compliance / takedown event log

Also adds new enum values used by these tables.

Revision ID: c1a2b3d4e5f6
Revises: b4c8e2f1a903
Create Date: 2026-09-17 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------

revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "b4c8e2f1a903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _varchar(length: int) -> sa.String:
    return sa.String(length=length)


def _text() -> sa.Text:
    return sa.Text()


def _bool(default: bool, server_default: str) -> sa.Column:
    return sa.Column(sa.Boolean(), nullable=False,
                     default=default, server_default=server_default)


def _now() -> str:
    return "CURRENT_TIMESTAMP"


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:

    # -----------------------------------------------------------------------
    # 1. New columns on jobs
    # -----------------------------------------------------------------------

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column(
            "canonical_key", _varchar(512), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "external_id", _varchar(255), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "source_name", _varchar(100), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "source_url", _varchar(1024), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "attribution", _text(), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "country", _varchar(100), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "province", _varchar(100), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "city", _varchar(100), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "remote_eligibility",
            _varchar(32),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "category",
            _varchar(32),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "requirements", _text(), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "deadline", sa.DateTime(timezone=True), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "ingestion_status",
            _varchar(32),
            nullable=True,
            server_default="active",
        ))
        batch_op.add_column(sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "last_verified", sa.DateTime(timezone=True), nullable=True
        ))
        batch_op.add_column(sa.Column(
            "missed_syncs",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ))

    # Indexes on the new jobs columns
    op.create_index("ix_jobs_canonical_key",  "jobs", ["canonical_key"])
    op.create_index("ix_jobs_source_name",    "jobs", ["source_name"])
    op.create_index("ix_jobs_country",        "jobs", ["country"])
    op.create_index("ix_jobs_province",       "jobs", ["province"])
    op.create_index("ix_jobs_city",           "jobs", ["city"])
    op.create_index("ix_jobs_remote_eligibility", "jobs", ["remote_eligibility"])
    op.create_index("ix_jobs_category",       "jobs", ["category"])
    op.create_index("ix_jobs_deadline",       "jobs", ["deadline"])
    op.create_index("ix_jobs_ingestion_status", "jobs", ["ingestion_status"])
    op.create_index("ix_jobs_last_seen",      "jobs", ["last_seen"])

    # -----------------------------------------------------------------------
    # 2. ingestion_sources
    # -----------------------------------------------------------------------

    op.create_table(
        "ingestion_sources",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("name", _varchar(100), nullable=False),
        sa.Column("organization_name", _varchar(255), nullable=False),
        sa.Column("source_type", _varchar(32), nullable=False),
        sa.Column("base_url", _varchar(1024), nullable=True),
        sa.Column("terms_url", _varchar(1024), nullable=True),
        sa.Column("robots_url", _varchar(1024), nullable=True),
        sa.Column("legal_status", _text(), nullable=True),
        sa.Column(
            "permission_status", _varchar(32),
            nullable=False, server_default="permission_required",
        ),
        sa.Column("permission_document_url", _varchar(1024), nullable=True),
        sa.Column("attribution_template", _text(), nullable=True),
        sa.Column("display_policy", _text(), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("max_requests_per_hour", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("crawl_interval_minutes", sa.Integer(), nullable=False, server_default="720"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_successful_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", _text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.UniqueConstraint("name", name="uq_ingestion_sources_name"),
    )
    op.create_index("ix_ingestion_sources_active", "ingestion_sources", ["active"])
    op.create_index("ix_ingestion_sources_permission_status", "ingestion_sources", ["permission_status"])

    # -----------------------------------------------------------------------
    # 3. job_ingestion_sources
    # -----------------------------------------------------------------------

    op.create_table(
        "job_ingestion_sources",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column(
            "job_id", sa.UUID(), nullable=False,
            # FK defined separately below to avoid dialect differences
        ),
        sa.Column(
            "source_id", sa.UUID(), nullable=False,
        ),
        sa.Column("external_id", _varchar(512), nullable=True),
        sa.Column("source_url", _varchar(1024), nullable=True),
        sa.Column("application_url", _varchar(1024), nullable=True),
        sa.Column("payload_hash", _varchar(64), nullable=True),
        sa.Column("attribution", _text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", _varchar(50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["ingestion_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "source_id", "external_id",
            name="uq_job_ingestion_sources_source_external",
        ),
    )
    op.create_index("ix_job_ingestion_sources_job_id",    "job_ingestion_sources", ["job_id"])
    op.create_index("ix_job_ingestion_sources_source_id", "job_ingestion_sources", ["source_id"])
    op.create_index("ix_job_ingestion_sources_last_seen", "job_ingestion_sources", ["last_seen"])

    # -----------------------------------------------------------------------
    # 4. job_observations
    # -----------------------------------------------------------------------

    op.create_table(
        "job_observations",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column(
            "source_id", sa.UUID(), nullable=False,
        ),
        sa.Column("external_id", _varchar(512), nullable=True),
        sa.Column("raw_payload", _text(), nullable=True),
        sa.Column("payload_hash", _varchar(64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.ForeignKeyConstraint(["source_id"], ["ingestion_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_observations_source_id",    "job_observations", ["source_id"])
    op.create_index("ix_job_observations_external_id",  "job_observations", ["external_id"])
    op.create_index("ix_job_observations_payload_hash", "job_observations", ["payload_hash"])
    op.create_index("ix_job_observations_fetched_at",   "job_observations", ["fetched_at"])
    op.create_index("ix_job_observations_created_at",   "job_observations", ["created_at"])

    # -----------------------------------------------------------------------
    # 5. compliance_events
    # -----------------------------------------------------------------------

    op.create_table(
        "compliance_events",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("job_id",    sa.UUID(), nullable=True),
        sa.Column("event_type", _varchar(32), nullable=False),
        sa.Column("notes", _text(), nullable=True),
        sa.Column("requester", _varchar(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text(_now()),
        ),
        sa.ForeignKeyConstraint(["source_id"], ["ingestion_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"],    ["jobs.id"],              ondelete="SET NULL"),
    )
    op.create_index("ix_compliance_events_source_id",   "compliance_events", ["source_id"])
    op.create_index("ix_compliance_events_job_id",      "compliance_events", ["job_id"])
    op.create_index("ix_compliance_events_event_type",  "compliance_events", ["event_type"])
    op.create_index("ix_compliance_events_created_at",  "compliance_events", ["created_at"])


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop new tables (reverse order of FK dependencies)
    op.drop_table("compliance_events")
    op.drop_table("job_observations")
    op.drop_table("job_ingestion_sources")
    op.drop_table("ingestion_sources")

    # Drop new indexes then columns from jobs
    for idx in [
        "ix_jobs_last_seen",
        "ix_jobs_ingestion_status",
        "ix_jobs_deadline",
        "ix_jobs_category",
        "ix_jobs_remote_eligibility",
        "ix_jobs_city",
        "ix_jobs_province",
        "ix_jobs_country",
        "ix_jobs_source_name",
        "ix_jobs_canonical_key",
    ]:
        op.drop_index(idx, table_name="jobs")

    with op.batch_alter_table("jobs") as batch_op:
        for col in [
            "missed_syncs", "last_verified", "last_seen", "first_seen",
            "ingestion_status", "deadline", "requirements", "category",
            "remote_eligibility", "city", "province", "country",
            "attribution", "source_url", "source_name", "external_id",
            "canonical_key",
        ]:
            batch_op.drop_column(col)
