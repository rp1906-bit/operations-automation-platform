"""Root-cause signatures. First match in priority order wins.

Each signature names the cause, carries a confidence level, and may declare the module of
a pre-vetted fix. A fault inside the checker itself is its own signature so the card can
say "the check broke, not the pipeline" — historically the most common RED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .verdict import RED, UNVERIFIED, Verdict

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


@dataclass(frozen=True)
class Signature:
    name: str
    confidence: str
    matches: Callable[[Verdict], bool]
    fix_module: Optional[str] = None       # name in bands.FIX_REGISTRY, or None
    declared_cost: Callable[[dict], float] = lambda run: 0.0   # the signature's own estimate; never trusted as-is
    fix_units: Callable[[dict], int] = lambda run: 0


@dataclass
class Match:
    signature: Optional[Signature]
    confidence: str

    @property
    def name(self) -> str:
        return self.signature.name if self.signature else "unknown"


def _metric_red(v: Verdict, metric) -> bool:
    f = v.worst(metric)
    return bool(f and f.verdict == RED)


def _metric_unverified(v: Verdict, metric) -> bool:
    f = v.worst(metric)
    return bool(f and f.verdict == UNVERIFIED)


def _timeout_dominated(v: Verdict) -> bool:
    errs = v.run.get("log_errors", [])
    return bool(errs) and sum("timeout" in e.lower() for e in errs) * 2 > len(errs)


SIGNATURES = (
    Signature("checker_environment_fault", LOW,
              lambda v: _metric_unverified(v, "metric_source_missing")),
    Signature("document_read_timeout", HIGH,
              lambda v: _metric_red(v, "doc_read_rate") and _timeout_dominated(v),
              fix_module="reread_documents", fix_units=lambda run: run["docs_attempted"] - run["docs_read"],
              declared_cost=lambda run: (run["docs_attempted"] - run["docs_read"]) * 0.02),
    Signature("ambiguous_empty_address", HIGH,
              lambda v: _metric_red(v, "empty_address_rate") and _metric_red(v, "ambiguous_owner_count"),
              fix_module="re_enrich_by_address", fix_units=lambda run: run["rows_missing_address"],
              declared_cost=lambda run: run["rows_missing_address"] * 0.02),
    Signature("delivery_stalled", MEDIUM,
              lambda v: _metric_red(v, "delivered"),
              fix_module="expand_target_grid", fix_units=lambda run: 1, declared_cost=lambda run: 0.50),
)


def diagnose(verdict: Verdict) -> Match:
    for sig in SIGNATURES:
        if sig.matches(verdict):
            return Match(sig, sig.confidence)
    return Match(None, LOW)
