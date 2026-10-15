"""Second-source match against a parcel table. An ambiguous match blanks and flags; it never guesses."""
from __future__ import annotations

import json
from pathlib import Path

from .normalize import Row, clean


class ParcelTable:
    def __init__(self, path: Path):
        self.parcels = json.loads(path.read_text())

    def by_address(self, address: str) -> list[dict]:
        return [p for p in self.parcels if clean(p["address"]) == clean(address)]

    def by_owner(self, owner: str) -> list[dict]:
        return [p for p in self.parcels if clean(p["owner"]) == clean(owner)]


def _fill(row: Row, parcel: dict) -> None:
    row.parcel_id, row.mailing, row.value = parcel["parcel_id"], parcel["mailing"], parcel["value"]
    if not row.address:
        row.address = clean(parcel["address"])


def enrich(row: Row, table: ParcelTable) -> Row:
    if row.address:
        hits = table.by_address(row.address)
        if len(hits) == 1:
            _fill(row, hits[0])
            return row
        row.flags.append("second source: no parcel at this address" if not hits
                         else f"second source: {len(hits)} parcels at this address")
        return row
    hits = table.by_owner(row.owner)               # no address to compare against
    if len(hits) == 1:
        _fill(row, hits[0])                         # a single hit is unambiguous
        row.notes.append("address taken from second source")
    elif len(hits) > 1:
        row.parcel_id, row.mailing, row.value = "", "", None    # blank, do not pick one
        row.flags.append(f"ambiguous: {len(hits)} parcels for {row.owner}, no document address to disambiguate")
    else:
        row.flags.append("no document address and owner not found in second source")
    return row
