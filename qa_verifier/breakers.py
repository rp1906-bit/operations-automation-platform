"""Circuit breakers: hard limits the triage cannot override."""
from __future__ import annotations

from datetime import datetime, timedelta

REPEAT_WINDOW = timedelta(hours=24)
LOCK_TTL = timedelta(minutes=30)
PROTECTED_TARGETS = frozenset({"crm_upload"})   # never written by any fix, ever


def repeat_signature(entries, signature: str, now: datetime) -> bool:
    """Same signature already fired in the window, across ALL pipelines (keyed on signature only)."""
    return any(e["signature"] == signature and now - e["at"] <= REPEAT_WINDOW and e["at"] < now
               for e in entries)


def touches_protected(targets) -> bool:
    return bool(PROTECTED_TARGETS & set(targets or ()))


def check(entries, signature: str, now: datetime, fix_targets) -> str | None:
    """Return the tripped breaker's reason, or None."""
    if touches_protected(fix_targets):
        return "fix would write a protected target — escalate"
    if repeat_signature(entries, signature, now):
        return f"repeat signature {signature} within 24h — human review"
    return None


class RunLock:
    """One run per pipeline; a stale lock expires rather than blocking forever."""

    def __init__(self):
        self._held: dict[str, datetime] = {}

    def acquire(self, pipeline: str, now: datetime) -> bool:
        expires = self._held.get(pipeline)
        if expires and now < expires:
            return False
        self._held[pipeline] = now + LOCK_TTL
        return True

    def release(self, pipeline: str) -> None:
        self._held.pop(pipeline, None)
