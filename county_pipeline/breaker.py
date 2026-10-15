"""Abort the batch when about nine in ten rows look wrong; a batch that bad is a broken source, not data."""
from __future__ import annotations

from .normalize import Row

ABORT_RATIO = 0.9


def looks_wrong(row: Row) -> bool:
    return bool(row.flags)


def should_abort(rows: list[Row], ratio: float = ABORT_RATIO) -> tuple[bool, float]:
    if not rows:
        return False, 0.0
    wrong = sum(looks_wrong(r) for r in rows) / len(rows)
    return wrong >= ratio, wrong
