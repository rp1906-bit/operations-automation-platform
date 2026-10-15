"""One QA run, end to end: lock → grade → diagnose → breakers → triage → card → ledger."""
from __future__ import annotations

from datetime import datetime

from . import bands, breakers, plain_english, signatures, verdict as verdict_mod
from .ledger import Ledger


def verify_run(run: dict, ledger: Ledger, lock: breakers.RunLock, now: datetime, reviews=None):
    if not lock.acquire(run["pipeline"], now):
        return None, None, bands.Decision("busy", "a QA run for this pipeline is still in flight"), "BUSY"
    try:
        v = verdict_mod.grade(run)
        match = signatures.diagnose(v)
        if v.overall == verdict_mod.RED:
            tripped = breakers.check(ledger.rows, match.name, now, run.get("fix_targets"))
            decision = bands.triage(match, run, reviews, tripped)
        elif v.overall == verdict_mod.UNVERIFIED:
            # The checker, not the pipeline, failed. Nothing to fix in the pipeline; a person restores the check.
            decision = bands.Decision("escalate", "check incomplete — the checker could not read its evidence")
        else:
            decision = bands.Decision("none", "no action" if v.overall == "GREEN" else "post and watch next run")
        card = plain_english.render_card(v, match, decision)
        ledger.append(now, run, v, match, decision)
        return v, match, decision, card
    finally:
        lock.release(run["pipeline"])
