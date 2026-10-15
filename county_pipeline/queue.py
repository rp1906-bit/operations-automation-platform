"""Incomplete rows go to a review queue. Nothing is dropped silently."""
from __future__ import annotations

from .normalize import Row


def route(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """Return (ready_to_write, review_queue). Every input row lands in exactly one of them."""
    ready = [r for r in rows if r.complete]
    review = [r for r in rows if not r.complete]
    assert len(ready) + len(review) == len(rows)
    return ready, review
