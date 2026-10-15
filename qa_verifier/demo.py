"""Run the bundled synthetic fixtures end to end: `python -m qa_verifier.demo`."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .breakers import RunLock
from .ledger import Ledger
from .verify import verify_run

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixtures():
    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]


def main():
    ledger, lock = Ledger(), RunLock()
    for run in load_fixtures():
        now = datetime.fromisoformat(run["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        reviews = run.get("reviews")
        _, _, _, card = verify_run(run, ledger, lock, now, reviews)
        print(card, "\n")
    print("=" * 60, "\nRUN LEDGER (append-only)\n")
    print(ledger.render())


if __name__ == "__main__":
    main()
