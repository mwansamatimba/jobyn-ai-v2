"""Job lifecycle / expiry detection for the Job Ingestion Engine.

State machine
-------------

  active          — visible, verified
  possibly_closed — absent from ONE complete successful sync
  closed          — absent from TWO consecutive complete successful syncs
                    OR explicitly closed by source
  expired         — deadline has passed (deadline < now)
  removed         — source owner requested takedown
  error           — persistent error state (manual review needed)

Rules
-----
* A FAILED or PARTIAL sync must NEVER trigger disappearance-based closure.
  Only mark possibly_closed / closed after a COMPLETE, SUCCESSFUL sync.
* A passed deadline triggers expired even if the job still appears in the feed.
* Source-requested removal always wins over other states.
* Expiry / closure does NOT delete the row — it marks it inactive so it is
  excluded from job discovery and matching.

Usage::

    from backend.ingestion.expiry import compute_next_status, should_deactivate

    new_status = compute_next_status(
        current_status="active",
        seen_in_latest_sync=False,
        sync_was_complete=True,
        deadline=job.deadline,
        source_says_closed=False,
    )
"""

from __future__ import annotations

from datetime import datetime, timezone


def compute_next_status(
    *,
    current_status: str,
    seen_in_latest_sync: bool,
    sync_was_complete: bool,
    deadline: datetime | None,
    source_says_closed: bool = False,
    missed_syncs: int = 0,
) -> tuple[str, int]:
    """Compute the next ingestion_status and missed_syncs count for a job.

    Args:
        current_status:    The job's current ingestion_status value.
        seen_in_latest_sync: Whether the job appeared in the latest sync.
        sync_was_complete:   Whether the sync ran to completion without a
                             source-level error.  MUST be False for partial
                             or errored syncs.
        deadline:          The job's application deadline (timezone-aware).
        source_says_closed: True if the source explicitly marks the job closed.
        missed_syncs:      Current consecutive missed-sync count on the job.

    Returns:
        A tuple of (next_status_str, next_missed_syncs_int).
    """
    now = datetime.now(timezone.utc)

    # Source-requested removal always wins.
    if current_status == "removed":
        return "removed", missed_syncs

    # Explicit source closure.
    if source_says_closed:
        return "closed", missed_syncs

    # Deadline expiry.
    if deadline and deadline.tzinfo is not None and deadline < now:
        return "expired", missed_syncs

    # Job was seen in this sync → mark active/verified.
    if seen_in_latest_sync:
        return "active", 0

    # Job was NOT seen.  Only apply disappearance logic if sync was complete.
    if not sync_was_complete:
        # Partial or failed sync — preserve current status unchanged.
        return current_status, missed_syncs

    # Complete sync; job absent.
    new_missed = missed_syncs + 1

    if new_missed == 1:
        return "possibly_closed", new_missed
    # Two or more consecutive complete syncs without the job.
    return "closed", new_missed


def should_deactivate(status: str) -> bool:
    """Return True if a job in this state should have is_active=False."""
    return status in {"closed", "expired", "removed"}


def should_exclude_from_matching(status: str) -> bool:
    """Return True if a job should be excluded from candidate matching."""
    return status in {"closed", "expired", "removed"}
