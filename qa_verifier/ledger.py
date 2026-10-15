"""Append-only run ledger. Records every run, GREEN included — a history without the
greens cannot show a recovery. Nothing here can rewrite or delete a row."""
from __future__ import annotations

from datetime import datetime


class LedgerRewriteError(Exception):
    pass


class Ledger:
    def __init__(self):
        self._rows: list[dict] = []

    def append(self, at: datetime, run: dict, verdict, match, decision) -> dict:
        row = {
            "at": at, "run_id": run["run_id"], "pipeline": run["pipeline"], "county": run["county"],
            "verdict": verdict.overall, "dimensions": dict(verdict.dimensions),
            "signature": match.name if verdict.overall in ("RED", "UNVERIFIED") else "none",
            "confidence": match.confidence if verdict.overall in ("RED", "UNVERIFIED") else "",
            "band": decision.band if verdict.overall in ("RED", "UNVERIFIED") else None, "reason": decision.reason,
        }
        self._rows.append(row)
        return row

    @property
    def rows(self) -> tuple:
        return tuple(self._rows)

    def __len__(self):
        return len(self._rows)

    def __setitem__(self, *_):
        raise LedgerRewriteError("ledger rows are append-only; corrections append a new row")

    def __delitem__(self, *_):
        raise LedgerRewriteError("ledger rows are append-only; nothing is deleted")

    def render(self) -> str:
        out = []
        for r in self._rows:
            dims = " ".join(f"{k}={v}" for k, v in r["dimensions"].items())
            band = f"{r['band']} — {r['reason']}" if r["band"] else "—"
            out.append(f"## {r['at']:%Y-%m-%d} — {r['pipeline']} — {r['county']} — {r['verdict']}\n"
                       f"- **Source run:** {r['run_id']}\n- **Dimensions:** {dims}\n"
                       f"- **RCA:** {r['signature']}{' (' + r['confidence'] + ')' if r['confidence'] else ''}\n- **Band / action:** {band}\n")
        return "\n".join(out)
