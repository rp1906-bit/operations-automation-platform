"""One command: `python -m kpi_engine.demo`. Nightly runs over the fixture, then the weekly stoplight."""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import digest, report
from .deliver import deliver, to_html
from .pull import SyntheticSource
from .runner import run

FIXTURE = Path(__file__).parent / "fixtures" / "activity.json"


def _add(acc: dict, event: str, n: int | None) -> None:
    """Summing an unmeasured day poisons the week: None + anything is None, never zero."""
    acc[event] = None if (n is None or acc.get(event, 0) is None) else acc.get(event, 0) + n


def week_of(days: dict, ids: list[str]) -> dict:
    team, callers = {}, {}
    for d in ids:
        for c, counts in days[d]["callers"].items():
            for e, n in counts.items():
                _add(callers.setdefault(c, {}), e, n)
                _add(team, e, n)
    return {"team": report.compute(team), "callers": {c: report.compute(v) for c, v in callers.items()}}


def cell(key: str, v: float | None) -> str:
    if v is None:
        return "- (unmeasured)"
    band = report.color(key, v)
    return f"{band[0] if band != report.BLANK else ' '} {v:.0f}" if report.BY_KEY[key].den is None else f"{band[0]} {v:.2f}"


def main(out_dir: Path | None = None) -> str:
    fixture = json.loads(FIXTURE.read_text())
    source = SyntheticSource(fixture)
    ids = [d["date"] for d in fixture["days"]]
    stale = {d["date"] for d in fixture["days"] if d["sms_store"] != "ok"}
    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="kpi_"))
    result = run(source, ids, out_dir, sms_store_ok=lambda d: d not in stale,
                 sheet_writer=lambda rows: {"updated": 0, "appended": len(rows)})

    this, prior = ids[-5:], ids[-10:-5]
    week, last = week_of(result["days"], this), week_of(result["days"], prior)
    d = report.diagnose(week, last)
    verdicts = report.caller_verdicts(week, d)
    why = report.why_it_moved(week, last, d)
    flags = sorted({f"{f}={day}" for day in ids for f in result["days"][day]["flags"]})

    lines = [f"KPI weekly stoplight, week of {this[0]}  (synthetic data, illustrative bands)",
             f"nightly runs: exit {result['exit_code']}, receipt {json.loads(result['receipt'].read_text())['returned']}",
             f"flags: {', '.join(flags) or 'none'}",
             f"{'metric':<20}{'team':>18}" + "".join(f"{c:>18}" for c in week["callers"])]
    for m in report.METRICS:
        values = [week["team"][m.key]] + [week["callers"][c][m.key] for c in week["callers"]]
        lines.append(f"{m.label:<20}" + "".join(f"{cell(m.key, v):>18}" for v in values))
    for k in range(3, 0, -1):  # the three most recent week-over-week walks, oldest first
        w, p_ = week_of(result["days"], ids[-5 * k:len(ids) - 5 * (k - 1)]), week_of(result["days"], ids[-5 * (k + 1):-5 * k])
        dk = report.diagnose(w, p_)
        lines.append(f"week of {ids[-5 * k]}: first worsened = {dk.category or 'none':<13} "
                     f"coaching gate {'open' if report.coaching_allowed(dk) else 'CLOSED'}"
                     + (f"  ({dk.evidence[0]})" if dk.evidence else ""))
    lines.append(f"this week's per-caller verdicts: {verdicts}")
    lines.append(f"why it moved: {why}")
    lines.append(f"digest example: {digest.failure_card({'checkout': 'success', 'setup': 'success', 'deps': 'success', 'fetch': 'failure', 'auth': 'skipped', 'run': 'skipped', 'sheet': 'skipped'})}")

    html = to_html(f"KPI weekly report, week of {this[0]}", week, verdicts, why, flags)
    size = deliver(html, upload=len)  # a faithful transport returns exactly the bytes it was given
    html_path = out_dir / "kpi_report.html"
    html_path.write_text(html)
    lines.append(f"html report: {html_path} ({size} bytes, post-upload check passed)")
    text = "\n".join(lines)
    print(text)
    return text


if __name__ == "__main__":
    sys.exit(0 if main(Path(".tmp")) else 1)
