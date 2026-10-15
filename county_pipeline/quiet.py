"""Tell a quiet county apart from a stale scraper.

Zero rows is only benign when a probe of the portal proved it reachable; a long run of benign
quiet days is still escalated, because a slow-onset block can keep the probe alive while the
search quietly returns nothing.
"""
from __future__ import annotations

QUIET_STREAK_THRESHOLD = 10     # illustrative; above the normal quiet cadence so it does not cry wolf

OK, QUIET, QUIET_STALE, SUSPICIOUS = "ok", "quiet_day", "quiet_streak_stale", "empty_suspicious"


def classify_run(rows_scraped: int, probe_proved_health: bool) -> str:
    if rows_scraped > 0:
        return OK
    return QUIET if probe_proved_health else SUSPICIOUS


def update_streak(status: str, prior_streak: int, threshold: int = QUIET_STREAK_THRESHOLD) -> tuple[int, str]:
    """Return (new_streak, effective_status). Any non-quiet status resets the streak."""
    if status != QUIET:
        return 0, status
    streak = prior_streak + 1
    return streak, (QUIET_STALE if streak >= threshold else QUIET)
