"""Auto-fix bands. Every RED lands in exactly one lane.

Band 1 (auto_execute) only when ALL hold: a pre-vetted fix is registered for the exact
signature, it is idempotent, its independently recomputed cost is under the cap and within
20% of the declared estimate, confidence is HIGH, and two independent reviewers both say
"clean". Anything else is a draft (Band 2) or evidence-only escalation (Band 3).
Prices and cap below are illustrative, not the real figures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .signatures import HIGH, MEDIUM, Match

AUTO_EXECUTE, DRAFT, ESCALATE = "auto_execute", "draft", "escalate"
COST_CAP_USD = 5.00
MAX_DIVERGENCE = 0.20
UNIT_PRICES_USD = {"document_reread": 0.02, "grid_expand": 0.50}   # illustrative


@dataclass(frozen=True)
class Fix:
    module: str
    idempotent: bool
    unit: str


# Fixes that have been reviewed and vetted ahead of time. A signature that names a module
# absent from this table can never auto-execute, whatever its confidence.
FIX_REGISTRY = {
    "reread_documents": Fix("reread_documents", idempotent=True, unit="document_reread"),
    "expand_target_grid": Fix("expand_target_grid", idempotent=False, unit="grid_expand"),
}


@dataclass
class Decision:
    band: str
    reason: str
    cost_usd: float = 0.0
    draft: dict = field(default_factory=dict)


def recompute_cost(fix: Fix, units: int) -> float:
    return round(units * UNIT_PRICES_USD[fix.unit], 2)


def _draft(match: Match, cost: float, reason: str) -> Decision:
    return Decision(DRAFT, reason, cost, draft={
        "diff": f"run {match.signature.fix_module} on the affected rows",
        "rationale": f"{match.name} ({match.confidence})",
        "cost_usd": cost,
        "rollback": "restore the affected rows from the pre-fix snapshot",
    })


def triage(match: Match, run: dict, reviews: dict | None, breaker_reason: str | None) -> Decision:
    """`reviews` maps reviewer name → verdict string; two 'clean' verdicts are required."""
    if breaker_reason:
        return Decision(ESCALATE, breaker_reason)
    if match.confidence not in (HIGH, MEDIUM) or match.signature is None:
        return Decision(ESCALATE, "LOW confidence — evidence only")
    sig = match.signature
    fix = FIX_REGISTRY.get(sig.fix_module or "")
    if fix is None:
        return _draft(match, sig.declared_cost(run), f"no pre-vetted fix registered for {sig.name}")
    cost = recompute_cost(fix, sig.fix_units(run))
    if match.confidence == MEDIUM:
        return _draft(match, cost, "MEDIUM confidence")
    if not fix.idempotent:
        return _draft(match, cost, "fix is not idempotent")
    if cost >= COST_CAP_USD:
        return _draft(match, cost, f"cost ${cost:.2f} at or over the ${COST_CAP_USD:.2f} cap")
    declared = sig.declared_cost(run)
    if declared and abs(cost - declared) / declared > MAX_DIVERGENCE:
        return _draft(match, cost, f"recomputed cost ${cost:.2f} diverges >20% from declared ${declared:.2f}")
    reviews = reviews or {}
    if len(reviews) < 2 or any(v != "clean" for v in reviews.values()):
        return Decision(ESCALATE, f"review gate not clean: {reviews or 'no reviewers'}", cost)
    return Decision(AUTO_EXECUTE, f"all Band 1 conditions hold; {fix.module} on {sig.fix_units(run)} unit(s)", cost)
