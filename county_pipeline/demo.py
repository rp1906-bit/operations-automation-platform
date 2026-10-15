"""Run the bundled synthetic fixtures end to end: `python -m county_pipeline.demo`."""
from __future__ import annotations

import json

from . import quiet
from .dedupe import Manifest
from .delivery import FakeCrm, ParallelRun, TabRegistry, WriterConflict, accept, deliver, diagnose_denial
from .enrich import ParcelTable
from .pipeline import run_day
from .source import FIXTURES, MarlowPortal


def main():
    portal, manifest, parcels = MarlowPortal(), Manifest(), ParcelTable(FIXTURES / "parcels.json")
    since, streak = "", 0
    print("MARLOW COUNTY — lien filings, three synthetic days\n")
    for day, stamp in (("2026-06-01", "2026-06-01T23:59"), ("2026-06-02", "2026-06-02T23:59"), ("2026-06-03", "2026-06-03T23:59")):
        report = run_day(portal, since, stamp, manifest, parcels, prior_streak=streak)
        print(f"{day}  {report.summary()}")
        for row in report.queued:
            print(f"    review  {row.doc_id} {row.owner:<18} {row.address or '(no address)':<14} {row.flags[0]}")
        for row in report.written:
            if row.notes:
                print(f"    written {row.doc_id} {row.owner:<18} {row.address:<14} {'; '.join(row.notes)}")
        since, streak = stamp, report.streak
        if day == "2026-06-01":
            written = report.written
    print(f"\nportal served {portal.fetches} records across the three fetches (unchanged records were not re-fetched)")

    print("\nQUIET STREAK — eleven quiet days in a row")
    s = 0
    for d in range(1, 12):
        s, status = quiet.update_streak(quiet.classify_run(0, probe_proved_health=True), s)
        if status != quiet.QUIET or d == 1:
            print(f"  day {d:>2}: streak {s:>2} -> {status}")

    print("\nCRM DELIVERY — day-one rows, one writer per tab, stack don't skip")
    crm, registry = FakeCrm(json.loads((FIXTURES / "crm_seed.json").read_text())), TabRegistry()
    summary = deliver(written, crm, "Marlow Lien", "bridge", registry)
    print(summary.card())
    ok, problems = accept(summary)
    print(f"acceptance from the card alone: {'PASS' if ok else 'FAIL ' + '; '.join(problems)}")
    try:
        deliver(written, crm, "Marlow Lien", "team-member-upload", registry)
    except WriterConflict as e:
        print(f"second writer refused: {e}")
    run = ParallelRun()
    for _ in range(5):
        run.record(accepted=ok)
    print(f"parallel run: {run.passed} accepted weekdays -> cutover_ready={run.cutover_ready}")
    print("\nDENIAL DIAGNOSIS —", diagnose_denial(401, 403, 403, code_changed_since_last_success=False))


if __name__ == "__main__":
    main()
