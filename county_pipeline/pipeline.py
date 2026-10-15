"""One day's run, end to end. Pure functions over the fixtures; no network."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import breaker, quiet
from .dedupe import Manifest, dedupe
from .enrich import ParcelTable, enrich
from .normalize import Row, normalize
from .queue import route
from .reader import read_document
from .source import MarlowPortal


@dataclass
class DayReport:
    fetched: int = 0
    duplicates: int = 0
    new_rows: int = 0
    written: list[Row] = field(default_factory=list)
    queued: list[Row] = field(default_factory=list)
    aborted: bool = False
    wrong_ratio: float = 0.0
    status: str = quiet.OK
    streak: int = 0

    def summary(self) -> str:
        if self.aborted:
            return (f"ABORTED — {self.wrong_ratio:.0%} of {self.new_rows} new rows look wrong; nothing written, "
                    f"manifest not committed, batch stays retry-eligible")
        return (f"{self.status}: fetched {self.fetched}, duplicates {self.duplicates}, "
                f"written {len(self.written)}, review queue {len(self.queued)}"
                + (f", quiet streak {self.streak}" if self.streak else ""))


def run_day(portal: MarlowPortal, since: str, now: str, manifest: Manifest, parcels: ParcelTable,
            prior_streak: int = 0, probe_proved_health: bool = True) -> DayReport:
    records = portal.fetch_changed(since, now)
    report = DayReport(fetched=len(records))
    status = quiet.classify_run(len(records), probe_proved_health)
    report.streak, report.status = quiet.update_streak(status, prior_streak)
    if not records:
        return report
    rows = [enrich(normalize(r, read_document(r["document"])), parcels) for r in records]
    rows, dups = dedupe(rows, manifest)
    report.duplicates, report.new_rows = len(dups), len(rows)
    report.aborted, report.wrong_ratio = breaker.should_abort(rows)
    if report.aborted:
        return report
    report.written, report.queued = route(rows)
    manifest.commit(rows)               # queued rows are adjudicated too: seen, awaiting a human
    return report
