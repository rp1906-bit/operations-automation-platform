"""HTML delivery that survives a lossy conversion, plus a post-upload byte check with a hard stop.

The managed file connector converts uploaded HTML into a document and strips background colors
while keeping foreground text color, so the stoplight is a colored glyph via `color:`, never a
cell fill. The connector can also truncate in transit, so after the upload the remote size is
compared to the local byte count; more than 5% apart is a hard stop, not a warning.
"""
from __future__ import annotations

from html import escape
from typing import Callable

from .report import BLANK, BY_KEY, GREEN, METRICS, RED, YELLOW, color

GLYPH = {GREEN: "#1a7f37", YELLOW: "#b08800", RED: "#c62828"}
MAX_DIVERGENCE = 0.05


class DeliveryStop(Exception):
    """The bytes that arrived are not the bytes that were sent. Nothing ships."""


def chip(band: str) -> str:
    if band == BLANK:
        return '<span title="unmeasured">&mdash;</span>'
    return f'<span style="color:{GLYPH[band]}">&#9679;</span> {band}'


def fmt(key: str, value: float | None) -> str:
    if value is None:
        return "&mdash; <em>(unmeasured)</em>"
    m = BY_KEY[key]
    return f"{value:.0f}" if m.den is None or m.den == "lead" else f"{value:.2f}"


def to_html(title: str, week: dict, verdicts: dict, why: str, flags: list[str]) -> str:
    cols = ["Team"] + list(week["callers"])
    rows = []
    for m in METRICS:
        cells = []
        for col in cols:
            v = week["team"][m.key] if col == "Team" else week["callers"][col][m.key]
            cells.append(f"<td>{chip(color(m.key, v))} {fmt(m.key, v)}</td>")
        rows.append(f"<tr><td>{escape(m.label)}</td>{''.join(cells)}</tr>")
    vrows = [f"<tr><td>{escape(c)}</td>" + "".join(f"<td>{escape(str(v))}</td>" for v in d.values()) + "</tr>"
             for c, d in verdicts.items()]
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:720px;margin:24px auto}}table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 8px;text-align:left}}</style>
</head><body><h1>{escape(title)}</h1>
<p><strong>Why it moved:</strong> {escape(why)}</p>
<p><em>Synthetic data. Bands illustrative. Flags: {escape(', '.join(flags) or 'none')}.</em></p>
<table><tr><th>Metric</th>{''.join(f'<th>{escape(c)}</th>' for c in cols)}</tr>{''.join(rows)}</table>
<h2>Per-caller performance</h2>
<table><tr><th>Caller</th><th>Correct # / Leads (input-clean)</th><th>Connects / Leads</th></tr>{''.join(vrows)}</table>
</body></html>"""


def deliver(html: str, upload: Callable[[bytes], int]) -> int:
    """`upload` returns the remote size in bytes. Returns local size; raises DeliveryStop on divergence."""
    local = html.encode("utf-8")
    remote = upload(local)
    if abs(remote - len(local)) / len(local) > MAX_DIVERGENCE:
        raise DeliveryStop(f"uploaded {remote} bytes, local {len(local)}: more than {MAX_DIVERGENCE:.0%} apart")
    return len(local)
