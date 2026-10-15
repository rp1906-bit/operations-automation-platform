"""Portal record + read result -> one Row; composite key = address + filing date + owner."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .reader import ReadResult

_WS = re.compile(r"\s+")


def clean(text: str) -> str:
    return _WS.sub(" ", (text or "").strip()).upper()


@dataclass
class Row:
    doc_id: str
    filed: str
    owner: str
    address: str = ""
    parcel_id: str = ""
    mailing: str = ""
    value: int | None = None
    flags: list[str] = field(default_factory=list)     # blocking: row goes to review
    notes: list[str] = field(default_factory=list)     # informational

    @property
    def key(self) -> str:
        return f"{clean(self.address)}|{self.filed}|{clean(self.owner)}"

    @property
    def complete(self) -> bool:
        return bool(self.address and self.owner and self.filed) and not self.flags


def normalize(record: dict, read: ReadResult) -> Row:
    row = Row(doc_id=record["doc_id"], filed=record["filed"], owner=clean(record["owner"]),
              address=clean(read.address))
    if not read.ok:
        row.notes.append(f"extraction failed: {read.reason}")
    return row
