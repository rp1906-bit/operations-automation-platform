"""Composite-key dedupe against a manifest of keys already adjudicated in earlier runs."""
from __future__ import annotations

from .normalize import Row


class Manifest:
    """Keys are committed only after a batch is written, so an aborted batch stays retry-eligible."""

    def __init__(self, keys: set[str] | None = None):
        self.keys: set[str] = set(keys or ())

    def commit(self, rows: list[Row]) -> None:
        self.keys.update(r.key for r in rows)


def dedupe(rows: list[Row], manifest: Manifest) -> tuple[list[Row], list[Row]]:
    """Return (new_rows, duplicates). Duplicates within the batch count too; the first one wins."""
    seen, new, dups = set(manifest.keys), [], []
    for row in rows:
        (dups if row.key in seen else new).append(row)
        seen.add(row.key)
    return new, dups
