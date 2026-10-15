"""A fake county portal backed by a fixtures folder. Fetches only what changed."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
WATCH_LIST = frozenset({"lien"})   # generic watch-list; the real list is private


class MarlowPortal:
    """Every record carries a `modified` stamp; a fetch returns only stamps after `since`, up to `now`."""

    def __init__(self, fixtures: Path = FIXTURES):
        self._records = json.loads((fixtures / "filings.json").read_text())
        self.fetches = 0            # how many records the portal has served, for the test

    def fetch_changed(self, since: str, now: str = "9999") -> list[dict]:
        out = [dict(r) for r in self._records
               if since < r["modified"] <= now and r["filing_type"] in WATCH_LIST]
        self.fetches += len(out)
        return out

    def latest_stamp(self, records: list[dict]) -> str:
        return max((r["modified"] for r in records), default="")
