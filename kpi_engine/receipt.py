"""Delivery receipt: proof that THIS attempt's write landed, holding what the write RETURNED.

Deleted at the very start of every run, before anything that can raise. The run directory is
keyed on the run date and never cleaned, so a same-day redo lands in the same folder; a receipt
inherited from an earlier attempt would say "the day is in the sheet" about an attempt that never
reached the write. Written atomically after the write succeeds; a failed rename removes its own
temp file so nothing that parses as a receipt is left beside the receipt that does not exist.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

NAME = "sheet_written.json"


def clear(run_dir: Path) -> None:
    for name in (NAME, NAME + ".tmp"):
        try:
            (run_dir / name).unlink()
        except FileNotFoundError:
            pass


def write(run_dir: Path, returned: dict) -> Path:
    """`returned` is what the sheet API answered (rows updated / appended), never rows attempted."""
    run_dir.mkdir(parents=True, exist_ok=True)
    final, tmp = run_dir / NAME, run_dir / (NAME + ".tmp")
    tmp.write_text(json.dumps({"returned": dict(returned)}, indent=1))
    try:
        os.replace(tmp, final)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return final


def read(run_dir: Path) -> dict | None:
    p = run_dir / NAME
    return json.loads(p.read_text()) if p.exists() else None
