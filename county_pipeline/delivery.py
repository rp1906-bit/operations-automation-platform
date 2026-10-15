"""CRM delivery stage: one writer per tab, stack don't skip, acceptance from the summary alone."""
from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import Row, clean

PARALLEL_RUN_DAYS = 5


class WriterConflict(Exception):
    pass


class TabRegistry:
    """A tab has exactly one writer. Two writers is a last-write-wins race no guard can see."""

    def __init__(self):
        self.owners: dict[str, str] = {}

    def claim(self, tab: str, writer: str) -> None:
        holder = self.owners.setdefault(tab, writer)
        if holder != writer:
            raise WriterConflict(f"tab {tab!r} is written by {holder!r}; {writer!r} refused")


class FakeCrm:
    def __init__(self, seed: list[dict]):
        self.records = {clean(r["address"]): dict(r) for r in seed}
        self._n = len(seed)

    def find(self, address: str) -> dict | None:
        return self.records.get(clean(address))

    def near_miss(self, address: str) -> dict | None:
        a = clean(address).split(" ")
        for rec in self.records.values():
            b = clean(rec["address"]).split(" ")
            if a != b and a[0] == b[0] and a[1][:4] == b[1][:4]:
                return rec
        return None

    def create(self, row: Row, tab: str) -> str:
        self._n += 1
        uuid = f"crm-{self._n:04d}"
        self.records[clean(row.address)] = {"uuid": uuid, "address": row.address, "lists": [tab],
                                            "notes": [f"{tab} filed {row.filed}"]}
        return uuid

    def stack(self, existing: dict, row: Row, tab: str) -> None:
        """Add the list and the note onto the existing record. Never overwrite a field."""
        if tab not in existing["lists"]:
            existing["lists"].append(tab)
        existing["notes"].append(f"{tab} filed {row.filed}")


@dataclass
class RunSummary:
    tab: str
    writer: str
    created: list[tuple[str, str]] = field(default_factory=list)       # (address, new uuid)
    stacked: list[tuple[str, str]] = field(default_factory=list)       # (address, existing uuid)
    unconfirmed: list[tuple[str, str]] = field(default_factory=list)   # (address, reason)

    def card(self) -> str:
        lines = [f"{self.tab} — {len(self.created)} created, {len(self.stacked)} stacked, "
                 f"{len(self.unconfirmed)} unconfirmed (writer: {self.writer})"]
        lines += [f"  created   {a}  -> {u}" for a, u in self.created]
        lines += [f"  stacked   {a}  on {u}" for a, u in self.stacked]
        lines += [f"  unconfirmed {a}  ({r})" for a, r in self.unconfirmed]
        return "\n".join(lines)


def deliver(rows: list[Row], crm: FakeCrm, tab: str, writer: str, registry: TabRegistry) -> RunSummary:
    registry.claim(tab, writer)
    summary = RunSummary(tab, writer)
    for row in rows:
        existing = crm.find(row.address)
        if existing:
            crm.stack(existing, row, tab)
            summary.stacked.append((row.address, existing["uuid"]))
        elif (near := crm.near_miss(row.address)):
            summary.unconfirmed.append((row.address, f"near-miss spelling of {near['address']!r}, left to a human"))
        else:
            summary.created.append((row.address, crm.create(row, tab)))
    return summary


def accept(summary: RunSummary) -> tuple[bool, list[str]]:
    """Acceptance is computed from the summary alone, so the Slack card is enough to check a day."""
    problems = []
    created = {clean(a) for a, _ in summary.created}
    stacked = {clean(a) for a, _ in summary.stacked}
    if created & stacked:
        problems.append(f"created and stacked the same address: {sorted(created & stacked)}")
    if len(created) != len(summary.created) or len(stacked) != len(summary.stacked):
        problems.append("an address appears twice in one run")
    if any(not u for _, u in summary.stacked):
        problems.append("a stacked row names no existing record")
    if any(not r for _, r in summary.unconfirmed):
        problems.append("an unconfirmed row gives no reason")
    return not problems, problems


class ParallelRun:
    """Human keeps uploading while the bridge runs; the tab cuts over after five accepted weekdays in a row."""

    def __init__(self, days_required: int = PARALLEL_RUN_DAYS):
        self.required, self.passed = days_required, 0

    def record(self, accepted: bool, weekday: bool = True) -> None:
        if not weekday:
            return
        self.passed = self.passed + 1 if accepted else 0

    @property
    def cutover_ready(self) -> bool:
        return self.passed >= self.required


def diagnose_denial(unauthenticated: int, authenticated_get: int, authenticated_post: int,
                    code_changed_since_last_success: bool) -> str:
    """Read an HTTP denial as evidence. 401 without credentials plus 403 with them, on GET and POST alike,
    is the server refusing a known caller on a route it has closed, not a broken request."""
    if unauthenticated == 401 and authenticated_get == 403 and authenticated_post == 403:
        if not code_changed_since_last_success:
            return "vendor-side route closure: authenticated caller denied on GET and POST; our code unchanged"
        return "permission denied on a route; our code changed since it last worked — check both sides"
    if authenticated_post == 403 and authenticated_get == 200:
        return "method-level permission: reads allowed, writes denied on this route"
    return "inconclusive: gather 401-vs-403 and GET-vs-POST evidence before proposing a fix"
