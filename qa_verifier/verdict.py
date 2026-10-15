"""Dimensions → GREEN / YELLOW / UNVERIFIED / RED. Worst dimension wins.

A run record is a plain dict (see fixtures/). Every metric is graded against an explicit
threshold. A metric the checker itself could not read is UNVERIFIED (`metric_source_missing`):
never a false RED about a healthy run, never a default GREEN. A real RED still outranks it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GREEN, YELLOW, UNVERIFIED, RED = "GREEN", "YELLOW", "UNVERIFIED", "RED"
_RANK = {GREEN: 0, YELLOW: 1, UNVERIFIED: 2, RED: 3}

# metric: (green_if, yellow_if) — first predicate that holds wins, else RED.
THRESHOLDS = {
    "doc_read_rate":        (lambda v: v >= 0.80, lambda v: v >= 0.60),
    "empty_address_rate":   (lambda v: v <= 0.10, lambda v: v <= 0.25),
    "ambiguous_owner_count": (lambda v: v <= 2,    lambda v: v <= 7),
}


@dataclass
class Finding:
    dimension: str
    metric: str
    value: object
    verdict: str
    detail: str = ""


@dataclass
class Verdict:
    run: dict
    overall: str
    dimensions: dict
    findings: list = field(default_factory=list)

    def worst(self, metric):
        return next((f for f in self.findings if f.metric == metric), None)


def worst_of(verdicts) -> str:
    return max(verdicts, key=_RANK.get, default=GREEN)


def grade_metric(metric: str, value) -> str:
    green, yellow = THRESHOLDS[metric]
    return GREEN if green(value) else YELLOW if yellow(value) else RED


def _run_health(run) -> list[Finding]:
    code = run.get("exit_code")
    ok = run.get("status") == "SUCCEEDED" and isinstance(code, int) and code == 0
    v = RED if not ok else (YELLOW if run.get("warnings", 0) else GREEN)
    return [Finding("run_health", "exit_status", f"{run.get('status')}/{code}", v,
                    f"{run.get('warnings', 0)} warning(s)")]


def _extraction(run) -> list[Finding]:
    if run.get("checker_errors"):
        # The checker could not read its evidence. Absence of evidence is UNVERIFIED:
        # not GREEN (nothing was checked) and not RED (nothing is known to be wrong).
        return [Finding("extraction", "metric_source_missing", None, UNVERIFIED,
                        "; ".join(run["checker_errors"]))]
    attempts = run.get("docs_attempted", 0)
    rate = run.get("docs_read", 0) / attempts if attempts else 1.0
    return [Finding("extraction", "doc_read_rate", round(rate, 3), grade_metric("doc_read_rate", rate),
                    f"{run.get('docs_read', 0)} of {attempts} documents read")]


def _row_compliance(run) -> list[Finding]:
    rows = run.get("rows_written", 0)
    rate = run.get("rows_missing_address", 0) / rows if rows else 0.0
    count = run.get("rows_ambiguous_owner", 0)
    return [
        Finding("row_compliance", "empty_address_rate", round(rate, 3),
                grade_metric("empty_address_rate", rate), f"{run.get('rows_missing_address', 0)} of {rows} rows"),
        Finding("row_compliance", "ambiguous_owner_count", count,
                grade_metric("ambiguous_owner_count", count), "rows flagged for a human"),
    ]


def _delivery(run) -> list[Finding]:
    ok = run.get("delivered", False) or run.get("rows_written", 0) == 0
    return [Finding("delivery", "delivered", ok, GREEN if ok else RED,
                    "rows reached the sheet" if ok else "rows never reached the sheet")]


DIMENSIONS = (_run_health, _extraction, _row_compliance, _delivery)


def grade(run: dict) -> Verdict:
    findings = [f for dim in DIMENSIONS for f in dim(run)]
    dims = {}
    for f in findings:
        dims[f.dimension] = worst_of([dims.get(f.dimension, GREEN), f.verdict])
    return Verdict(run=run, overall=worst_of(dims.values()), dimensions=dims, findings=findings)
