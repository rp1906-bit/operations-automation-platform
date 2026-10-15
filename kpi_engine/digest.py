"""Name the FIRST step that failed. A downstream "skipped" is a consequence, never the cause."""
from __future__ import annotations

STEP_ORDER = ("checkout", "setup", "deps", "fetch", "auth", "run", "sheet")


def first_failing_step(results: dict[str, str]) -> str | None:
    """`results` maps step id -> 'success' | 'failure' | 'skipped'. Scanned in pipeline order."""
    for step in STEP_ORDER:
        if results.get(step) == "failure":
            return step
    return None


def failure_card(results: dict[str, str]) -> str:
    step = first_failing_step(results)
    if step is None:
        return "KPI run finished; see receipt for what reached the sheet."
    return f"KPI run FAILED at {step}. No numbers tonight."
