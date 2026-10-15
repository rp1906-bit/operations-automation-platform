"""Slack-style card from a verdict. The team reads it to answer three questions in five
seconds: did the run succeed, if not what broke, and what happens next.

Every registered signature MUST have wording in SIGNATURE_TEXT; `coverage_gaps()` is
the build gate that catches a new signature shipped without words.
"""
from __future__ import annotations

from .bands import AUTO_EXECUTE, DRAFT
from .signatures import SIGNATURES
from .verdict import Verdict

GLOSSARY = {
    "exit_status": "scrape job status",
    "doc_read_rate": "county document images we could read",
    "empty_address_rate": "rows with no property address",
    "ambiguous_owner_count": "rows flagged as an uncertain owner match",
    "delivered": "rows reaching the sheet",
    "metric_source_missing": "evidence the checker could not read",
}

STATUS = {"GREEN": ("🟢", "PASSED"), "YELLOW": ("🟡", "PASSED WITH A WARNING"),
          "UNVERIFIED": ("⚪", "UNVERIFIED"), "RED": ("🔴", "FAILED")}

# signature → (what happened, why). The checker-fault entry is deliberately a different
# message class: it says the CHECK broke, so nobody distrusts a healthy scrape.
SIGNATURE_TEXT = {
    "checker_environment_fault": (
        "CHECK INCOMPLETE — the scrape may be fine. The checker itself could not read its evidence.",
        "The checker's own environment failed (missing package or broken credentials), not the pipeline."),
    "document_read_timeout": (
        "Most county document images did not load, so rows are missing the details read from the document.",
        "The county records site was slow or timing out. Usually on their end."),
    "ambiguous_empty_address": (
        "Rows came out with no property address, so owner lookups ran on the name alone and many came back uncertain. Those rows may point at the wrong parcel.",
        "The document images are not yielding a readable property address."),
    "delivery_stalled": (
        "The scrape finished but its rows never reached the sheet.",
        "The target tab most likely ran out of room."),
}

NEXT = {
    AUTO_EXECUTE: "Next: this run qualifies for the one pre-vetted repair; it is logged, and the next check confirms it.",
    DRAFT: "Next: DRAFT — awaiting approval. A proposed fix (diff, cost, rollback) is attached; nothing ships until a human says so.",
    "escalate": "Next: ESCALATE — evidence posted, no fix drafted. A human needs to look.",
}


def coverage_gaps() -> list[str]:
    return [s.name for s in SIGNATURES if s.name not in SIGNATURE_TEXT]


def render_card(verdict: Verdict, match, decision) -> str:
    run = verdict.run
    emoji, word = STATUS[verdict.overall]
    lines = [f"{emoji} {run['county'].title()} County {run['pipeline'].replace('_', ' ')} — {run['date'][:10]} — {word}"]
    if verdict.overall == "GREEN":
        lines.append(f"The scrape ran normally and wrote {run.get('rows_written', 0)} new rows.")
        return "\n".join(lines)
    if verdict.overall == "YELLOW":
        lines.append(f"The scrape ran and wrote {run.get('rows_written', 0)} rows, with a warning worth watching next run. No fix.")
        return "\n".join(lines)
    what, why = SIGNATURE_TEXT.get(match.name, ("The check failed on something it has not seen before.", "Unknown."))
    lines += [what, f"Why: {why}", "Numbers: " + "; ".join(
        f"{GLOSSARY.get(f.metric, f.metric)} = {f.detail if f.value is None else f.value}"
        for f in verdict.findings if f.verdict in ("RED", "UNVERIFIED"))]
    lines.append(NEXT[decision.band] + f" ({decision.reason})")
    return "\n".join(lines)
