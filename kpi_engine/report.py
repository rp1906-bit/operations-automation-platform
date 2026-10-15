"""Weekly stoplight: metric definitions in ONE place, bands, diagnosis order, coaching gate.

BANDS ARE ILLUSTRATIVE. The production thresholds are the business's operating targets and are
private. The shapes are what matter: an unmeasured metric is blank with a flag, never a red zero;
diagnosis walks Data -> Phone Health -> Systems -> Performance and stops at the first category
that worsened; nobody is coached until the first three are clear, with one carve-out.
"""
from __future__ import annotations

from dataclasses import dataclass

GREEN, YELLOW, RED, BLANK = "GREEN", "YELLOW", "RED", "BLANK"
DATA, PHONE, SYSTEMS, PERFORMANCE = "Data", "Phone Health", "Systems", "Performance"
ORDER = (DATA, PHONE, SYSTEMS, PERFORMANCE)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    num: str            # event counted in the numerator
    den: str | None     # event counted in the denominator; None = plain count
    higher_is_better: bool
    green: float | None  # illustrative band edges; None = informational, never colored
    yellow: float | None
    categories: tuple[str, ...]
    input_clean: bool = False  # carve-out: upstream data/phone noise cannot move it


METRICS = (
    Metric("dials", "Dials", "dial", None, True, None, None, ()),
    Metric("contact_rate", "Correct # / Dials", "connect_correct", "dial", True, 0.07, 0.05, (DATA,)),
    Metric("wrong_number_rate", "Wrong # / Connects", "wrong_number", "connect", False, 0.35, 0.50, (DATA,)),
    Metric("connect_rate", "Connects / Dials", "connect", "dial", True, 0.12, 0.09, (PHONE, SYSTEMS)),
    Metric("no_answer_rate", "No Answer / Dials", "no_answer", "dial", False, 0.85, 0.90, (PHONE, SYSTEMS)),
    Metric("leads", "Leads", "lead", None, True, None, None, ()),
    Metric("opp_to_lead", "Correct # / Leads", "connect_correct", "lead", False, 8.0, 12.0, (PERFORMANCE,), input_clean=True),
    Metric("connects_to_lead", "Connects / Leads", "connect", "lead", False, 20.0, 30.0, (PERFORMANCE,)),
    Metric("sms_replies", "Text replies", "sms_reply", None, True, None, None, ()),
)
BY_KEY = {m.key: m for m in METRICS}
UNMEASURED = "unmeasured"


def _count(counts: dict, event: str) -> int | None:
    """Missing = 0 (nothing happened). Explicit None = unmeasured (nobody could count it)."""
    if event == "connect":
        parts = [counts.get(e, 0) for e in ("connect_correct", "wrong_number", "dead_number")]
        return None if any(p is None for p in parts) else sum(parts)
    return counts.get(event, 0)


def compute(counts: dict) -> dict[str, float | None]:
    """counts: summed event counts for a caller or the team. None anywhere = unmeasured."""
    out = {}
    for m in METRICS:
        n = _count(counts, m.num)
        if n is None:
            out[m.key] = None
        elif m.den is None:
            out[m.key] = float(n)
        else:
            d = _count(counts, m.den)
            out[m.key] = None if d is None else (n / d if d else 0.0)
    return out


def color(key: str, value: float | None) -> str:
    m = BY_KEY[key]
    if value is None:
        return BLANK  # blank with a flag; a zero here would be a lie in red
    if m.green is None:
        return BLANK
    good = value >= m.green if m.higher_is_better else value <= m.green
    ok = value >= m.yellow if m.higher_is_better else value <= m.yellow
    return GREEN if good else YELLOW if ok else RED


def worsened(key: str, now: float | None, before: float | None, tolerance: float = 0.15) -> bool:
    if now is None or before is None or before == 0:
        return False
    change = (now - before) / abs(before)
    return change < -tolerance if BY_KEY[key].higher_is_better else change > tolerance


@dataclass
class Diagnosis:
    category: str | None      # first category that worsened, None = nothing worsened
    evidence: list[str]
    cleared: list[str]        # categories walked and found clean before stopping


def diagnose(week: dict, prior: dict) -> Diagnosis:
    """week/prior: {"team": metrics, "callers": {name: metrics}}.

    Phone Health is read as a TREND ACROSS CALLERS (team-level); Systems as a LEVEL FOR ONE
    caller. The same two ratios sit in both on purpose: a drop everyone shares is the phone
    system, a drop one person has is their setup.
    """
    cleared, evidence = [], []
    for cat in ORDER:
        hits = []
        for m in METRICS:
            if cat not in m.categories:
                continue
            if cat in (DATA, PHONE):
                if worsened(m.key, week["team"][m.key], prior["team"][m.key]):
                    hits.append(f"{m.label} {prior['team'][m.key]:.2f} -> {week['team'][m.key]:.2f} (team)")
            else:
                for c, mt in week["callers"].items():
                    if worsened(m.key, mt[m.key], prior["callers"][c][m.key]):
                        hits.append(f"{m.label} {prior['callers'][c][m.key]:.2f} -> {mt[m.key]:.2f} ({c})")
        if hits:
            return Diagnosis(cat, hits, cleared)
        cleared.append(cat)
    return Diagnosis(None, evidence, cleared)


def coaching_allowed(d: Diagnosis) -> bool:
    """No per-person judgment until Data, Phone Health and Systems are clear."""
    return all(c in d.cleared for c in (DATA, PHONE, SYSTEMS))


def caller_verdicts(week: dict, d: Diagnosis) -> dict[str, dict[str, str]]:
    """Per caller, per performance metric: a color, or 'withheld' while the funnel is suspect.

    The carve-out: a metric that is input-clean by construction (bad numbers and no-answers all
    happen before a correct conversation exists) is judged regardless of the gate.
    """
    gate = coaching_allowed(d)
    out = {}
    for c, mt in week["callers"].items():
        out[c] = {}
        for m in METRICS:
            if PERFORMANCE not in m.categories:
                continue
            out[c][m.key] = color(m.key, mt[m.key]) if (gate or m.input_clean) else f"withheld: {d.category} not ruled out"
    return out


def why_it_moved(week: dict, prior: dict, d: Diagnosis) -> str:
    now, before = week["team"]["leads"], prior["team"]["leads"]
    if before:
        pct = (now - before) / before * 100
        move = f"Leads {'up' if pct >= 0 else 'down'} {abs(pct):.0f}% vs last week ({before:.0f} -> {now:.0f})."
    else:
        move = f"Leads this week: {now:.0f}."
    if d.category is None:
        return f"{move} Nothing worsened in the diagnostic walk; any per-person read is fair."
    who = "the list, not the callers" if d.category == DATA else "the phone system, not the callers" \
        if d.category == PHONE else "one person's setup" if d.category == SYSTEMS else "closing"
    return f"{move} First thing that worsened: {d.category} ({d.evidence[0]}), so look at {who} first."
