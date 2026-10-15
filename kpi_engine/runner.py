"""Nightly runner: pull, aggregate, WRITE THE ARTIFACT, then decide the exit code, then deliver.

Fail-closed: the conditions below force a non-zero exit because each one is far more often a
broken read than a quiet day. A store that is merely stale is tagged, not failed: nobody measured
those days, but nothing broke, and the difference is the whole point.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from . import receipt
from .pull import SyntheticSource, pull_day

RED, GREEN = 1, 0
COUNTED = ("dial", "no_answer", "wrong_number", "dead_number", "connect_correct", "lead", "sms_reply")


def read_log_with_retry(read_log: Callable[[dict], list], record: dict) -> list | None:
    """One retry. Returns None when both attempts raise; the caller counts it, never swallows it."""
    for _ in range(2):
        try:
            return read_log(record)
        except Exception:
            continue
    return None


def aggregate_day(records: list[dict], read_log: Callable[[dict], list], sms_store_ok: bool) -> dict:
    counts = {c: Counter() for c in sorted({r["caller"] for r in records})}
    failures = empties = 0
    for r in records:
        events = read_log_with_retry(read_log, r)
        if events is None:
            failures += 1
            continue
        if not events:
            empties += 1
        for e in events:
            if e in COUNTED:
                counts[r["caller"]][e] += 1
    row = {"callers": {c: dict(v) for c, v in counts.items()},
           "log_read_failures": failures, "logs_scanned": len(records) - failures, "logs_empty": empties,
           "flags": set()}
    if not sms_store_ok:
        row["flags"].add("store_stale")
        for c in row["callers"]:
            row["callers"][c]["sms_reply"] = None  # unmeasured, not zero
    return row


def decide_exit(days: dict[str, dict]) -> tuple[int, list[str]]:
    reasons = []
    weekdays = [d for d in days if date.fromisoformat(d).weekday() < 5]
    if weekdays and all(sum(c.get("dial", 0) for c in days[d]["callers"].values()) == 0 for d in weekdays):
        reasons.append("every measured weekday reports 0 dials")
    if any(days[d]["log_read_failures"] for d in days):
        reasons.append("an activity-log read failed after one retry")
    scanned = sum(days[d]["logs_scanned"] for d in days)
    empty = sum(days[d]["logs_empty"] for d in days)
    if scanned >= 50 and empty / scanned > 0.90:
        reasons.append(f">90% of {scanned} scanned activity logs came back empty")
    return (RED if reasons else GREEN), reasons


def run(source: SyntheticSource, day_ids: list[str], run_dir: Path, *,
        read_log: Callable[[dict], list] = lambda r: r["events"],
        sms_store_ok: Callable[[str], bool] = lambda d: True,
        sheet_writer: Callable[[dict], dict] = lambda rows: {"updated": 0, "appended": len(rows)}) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    receipt.clear(run_dir)  # first thing, before anything that can raise

    days = {}
    for d in day_ids:
        records, flags = pull_day(source, d)
        row = aggregate_day(records, read_log, sms_store_ok(d))
        row["flags"] |= flags
        days[d] = row

    artifact = run_dir / "daily.json"
    artifact.write_text(json.dumps({d: {**row, "flags": sorted(row["flags"])} for d, row in days.items()}, indent=1))

    exit_code, reasons = decide_exit(days)  # decided AFTER the artifact exists
    result = {"exit_code": exit_code, "reasons": reasons, "artifact": artifact, "days": days}
    if exit_code == GREEN:
        returned = sheet_writer(days)
        result["receipt"] = receipt.write(run_dir, returned)
    return result
