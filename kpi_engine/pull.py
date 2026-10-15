"""Synthetic activity source with a hard per-query result cap, and the cap-aware day pull.

The real API refuses any query that would return more than 10,000 records. A day that bulk
automation touched can exceed that. The wrong fix is one narrower query ("records the dialer
touched"), because a lead born from a text reply or an inbound call flips status with zero dials
and would vanish. The fix here: two queries, unioned, and the day flagged `narrowed` so nobody
mistakes it for a complete read.
"""
from __future__ import annotations

import random

CAP = 10000


class CapExceeded(Exception):
    """The query would return more than the cap. The API returns nothing, not a partial page."""


class SyntheticSource:
    """Generates one deterministic record set per fixture day. `query` enforces the cap."""

    def __init__(self, fixture: dict):
        self.cap = fixture.get("cap", CAP)
        self.callers = fixture["callers"]
        self.campaigns = fixture["campaigns"]
        self.days = {d["date"]: d for d in fixture["days"]}

    def records(self, day: str) -> list[dict]:
        spec = self.days[day]
        rng = random.Random(spec["seed"])
        out = []
        for i in range(spec["records"]):
            caller = self.callers[i % len(self.callers)]
            r = {"id": f"{day}-{i}", "caller": caller, "campaign": rng.choice(self.campaigns),
                 "dialed": True, "status_changed": False, "events": ["dial"]}
            if rng.random() < 0.004:  # text-born lead: status flipped, nobody dialed it
                r.update(dialed=False, status_changed=True, events=["sms_reply", "lead"])
            elif rng.random() < spec["connect"]:
                outcome = "connect_correct" if rng.random() < spec["correct_share"] else rng.choice(("wrong_number", "dead_number"))
                r["events"].append(outcome)
                if outcome == "connect_correct" and rng.random() < spec["skill"][caller]:
                    r["events"].append("lead")
                    r["status_changed"] = True
            else:
                r["events"].append("no_answer")
            out.append(r)
        for i in range(spec.get("automation_touched", 0)):  # bumped by a bulk job; nobody dialed, nothing changed
            out.append({"id": f"{day}-auto-{i}", "caller": self.callers[0], "campaign": self.campaigns[0],
                        "dialed": False, "status_changed": False, "events": []})
        return out

    def query(self, day: str, where: str | None = None) -> list[dict]:
        rows = self.records(day)
        if where == "dialed":
            rows = [r for r in rows if r["dialed"]]
        elif where == "status_changed":
            rows = [r for r in rows if r["status_changed"]]
        if len(rows) > self.cap:
            raise CapExceeded(f"{day}: {len(rows)} candidates exceeds cap {self.cap}")
        return rows


def pull_day(source: SyntheticSource, day: str) -> tuple[list[dict], set[str]]:
    """One query when it fits; otherwise dialed ∪ status_changed, flagged `narrowed`.

    A narrowed day can still miss a record whose only activity was an inbound touch with no
    status flip. The flag exists so that gap is visible, not so it is closed.
    """
    try:
        return source.query(day), set()
    except CapExceeded:
        seen: dict[str, dict] = {}
        for where in ("dialed", "status_changed"):
            for r in source.query(day, where):
                seen[r["id"]] = r
        return list(seen.values()), {"narrowed"}
