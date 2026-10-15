"""One named test per KPI-engine decision-logic line in the publication spec, plus the fail-closed
and delivery rules the case study relies on. Synthetic data, illustrative bands."""
from __future__ import annotations

import json

import pytest

from kpi_engine import receipt, report
from kpi_engine.deliver import DeliveryStop, chip, deliver, to_html
from kpi_engine.demo import FIXTURE, week_of
from kpi_engine.digest import first_failing_step
from kpi_engine.pull import CapExceeded, SyntheticSource, pull_day
from kpi_engine.report import BLANK, DATA, GREEN, PERFORMANCE, PHONE, RED, SYSTEMS, Diagnosis
from kpi_engine.runner import GREEN as EXIT_OK, RED as EXIT_RED, run

OVER_CAP_DAY, STALE_DAY = "2026-09-01", "2026-09-10"


@pytest.fixture
def source():
    return SyntheticSource(json.loads(FIXTURE.read_text()))


def metrics(**counts):
    return report.compute(counts)


def week(team, callers):
    return {"team": team, "callers": callers}


# --- spec: unmeasured metric renders blank with a flag, never a red zero; stale store tagged, not failed

def test_unmeasured_metric_renders_blank_with_flag_never_red_zero():
    m = metrics(dial=100, connect_correct=10, lead=2, sms_reply=None)
    assert m["sms_replies"] is None
    assert report.color("sms_replies", m["sms_replies"]) == BLANK
    html = to_html("t", week(m, {"Caller A": m}), {}, "", ["store_stale=2026-09-10"])
    assert "unmeasured" in html and "store_stale" in html
    assert "#c62828" not in html.split("Text replies")[1].split("</tr>")[0]  # no red on that row


def test_stale_store_is_tagged_not_failed(source, tmp_path):
    result = run(source, [STALE_DAY], tmp_path, sms_store_ok=lambda d: False)
    assert result["exit_code"] == EXIT_OK
    assert "store_stale" in result["days"][STALE_DAY]["flags"]
    assert all(c["sms_reply"] is None for c in result["days"][STALE_DAY]["callers"].values())


# --- spec: output is written before the exit code is decided

def test_artifact_is_written_before_exit_code_is_decided(source, tmp_path):
    def always_fails(record):
        raise ConnectionError("activity log unavailable")
    result = run(source, ["2026-09-14"], tmp_path, read_log=always_fails)
    assert result["exit_code"] == EXIT_RED
    artifact = json.loads((tmp_path / "daily.json").read_text())
    assert artifact["2026-09-14"]["log_read_failures"] > 0  # the RED run still left a full artifact


# --- spec: receipt holds counts returned by the write, and is deleted at the start of every run

def test_receipt_reflects_returned_not_attempted_counts(source, tmp_path):
    days = ["2026-09-14", "2026-09-15", "2026-09-16"]
    result = run(source, days, tmp_path, sheet_writer=lambda rows: {"updated": 1, "appended": 0})
    assert len(result["days"]) == 3  # three attempted
    assert receipt.read(tmp_path)["returned"] == {"updated": 1, "appended": 0}


def test_stale_receipt_cannot_survive_into_a_new_run(source, tmp_path):
    receipt.write(tmp_path, {"updated": 99, "appended": 99})
    (tmp_path / "sheet_written.json.tmp").write_text("{}")

    def always_fails(record):
        raise ConnectionError
    result = run(source, ["2026-09-14"], tmp_path, read_log=always_fails)  # RED: never reaches the write
    assert result["exit_code"] == EXIT_RED
    assert receipt.read(tmp_path) is None
    assert not (tmp_path / "sheet_written.json.tmp").exists()


def test_receipt_write_is_atomic_and_cleans_its_temp_on_failure(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("rename failed")))
    with pytest.raises(OSError):
        receipt.write(tmp_path, {"updated": 1})
    assert not any(tmp_path.iterdir())


# --- spec: over-cap day splits into two unioned queries and is flagged `narrowed`

def test_over_cap_day_splits_unions_and_flags_narrowed(source):
    with pytest.raises(CapExceeded):
        source.query(OVER_CAP_DAY)
    records, flags = pull_day(source, OVER_CAP_DAY)
    assert flags == {"narrowed"}
    dialed_only = {r["id"] for r in source.query(OVER_CAP_DAY, "dialed")}
    recovered = [r for r in records if r["id"] not in dialed_only]
    assert recovered and all(r["status_changed"] and not r["dialed"] for r in recovered)
    assert len({r["id"] for r in records}) == len(records)  # union, not concatenation


def test_normal_day_is_one_query_with_no_flag(source):
    _, flags = pull_day(source, "2026-09-14")
    assert flags == set()


# --- spec: digest names the first failing step, not the last symptom

def test_digest_names_first_failing_step_not_last_symptom():
    results = {"checkout": "success", "setup": "success", "deps": "failure",
               "fetch": "skipped", "auth": "skipped", "run": "skipped", "sheet": "skipped"}
    assert first_failing_step(results) == "deps"
    assert first_failing_step({"run": "failure", "sheet": "failure"}) == "run"
    assert first_failing_step({"run": "success", "sheet": "success"}) is None


# --- spec: diagnostic order Data, Phone Health, Systems, Performance; coaching gated; one carve-out

def _weeks(prior_counts, this_counts):
    p = {c: metrics(**v) for c, v in prior_counts.items()}
    t = {c: metrics(**v) for c, v in this_counts.items()}
    tot = lambda d: {k: sum(v[k] for v in d.values()) for k in next(iter(d.values()))}
    return week(metrics(**tot(this_counts)), t), week(metrics(**tot(prior_counts)), p)


BASE = {"dial": 1000, "no_answer": 860, "connect_correct": 80, "wrong_number": 40, "dead_number": 20, "lead": 10}


def test_diagnostic_order_stops_at_first_category_that_worsened():
    prior = {"Caller A": dict(BASE), "Caller B": dict(BASE)}
    bad_data = {c: {**BASE, "connect_correct": 30, "wrong_number": 90} for c in prior}  # data AND perf look worse
    this, last = _weeks(prior, bad_data)
    d = report.diagnose(this, last)
    assert d.category == DATA and d.cleared == []

    bad_phone = {c: {**BASE, "no_answer": 900, "wrong_number": 15, "dead_number": 5} for c in prior}  # reach fell, list did not
    d = report.diagnose(*_weeks(prior, bad_phone))
    assert d.category == PHONE and d.cleared == [DATA]

    one_caller = {"Caller A": dict(BASE), "Caller B": {**BASE, "no_answer": 900, "wrong_number": 15, "dead_number": 5}}
    d = report.diagnose(*_weeks(prior, one_caller))
    assert d.category == SYSTEMS and "Caller B" in d.evidence[0]

    worse_closing = {"Caller A": dict(BASE), "Caller B": {**BASE, "lead": 5}}
    d = report.diagnose(*_weeks(prior, worse_closing))
    assert d.category == PERFORMANCE and d.cleared == [DATA, PHONE, SYSTEMS]


def test_coaching_is_gated_on_data_phone_and_systems_being_clear():
    assert not report.coaching_allowed(Diagnosis(DATA, ["x"], []))
    assert not report.coaching_allowed(Diagnosis(SYSTEMS, ["x"], [DATA, PHONE]))
    assert report.coaching_allowed(Diagnosis(PERFORMANCE, ["x"], [DATA, PHONE, SYSTEMS]))
    assert report.coaching_allowed(Diagnosis(None, [], [DATA, PHONE, SYSTEMS, PERFORMANCE]))
    this = week(metrics(**BASE), {"Caller A": metrics(**BASE)})
    verdicts = report.caller_verdicts(this, Diagnosis(DATA, ["x"], []))
    assert verdicts["Caller A"]["connects_to_lead"].startswith("withheld")


def test_carve_out_metric_is_judged_even_when_gate_is_closed():
    m = report.BY_KEY["opp_to_lead"]
    assert m.input_clean and PERFORMANCE in m.categories
    this = week(metrics(**BASE), {"Caller A": metrics(**{**BASE, "lead": 4})})  # 80 correct / 4 leads = 20
    verdicts = report.caller_verdicts(this, Diagnosis(DATA, ["list went bad"], []))
    assert verdicts["Caller A"]["opp_to_lead"] == RED
    assert verdicts["Caller A"]["connects_to_lead"] == f"withheld: {DATA} not ruled out"


def test_metric_definitions_live_in_one_place_and_color_is_shared():
    keys = [m.key for m in report.METRICS]
    assert len(keys) == len(set(keys))
    assert report.color("opp_to_lead", 5.0) == GREEN and report.color("opp_to_lead", 50.0) == RED
    html = to_html("t", week(metrics(**BASE), {"Caller A": metrics(**BASE)}), {}, "", [])
    assert html.count("&#9679;") == sum(1 for m in report.METRICS if m.green is not None) * 2


# --- fail-closed rules (runner)

def test_all_zero_weekday_window_is_red(source, tmp_path):
    result = run(source, ["2026-09-14", "2026-09-15"], tmp_path, read_log=lambda r: [])
    assert result["exit_code"] == EXIT_RED
    assert any("0 dials" in r for r in result["reasons"])


def test_log_read_failing_twice_is_red_and_counted(source, tmp_path):
    calls = {}

    def flaky(record):
        n = calls[record["id"]] = calls.get(record["id"], 0) + 1
        if record["id"].endswith("-7"):
            raise TimeoutError
        return record["events"]
    result = run(source, ["2026-09-14"], tmp_path, read_log=flaky)
    assert result["exit_code"] == EXIT_RED
    assert calls["2026-09-14-7"] == 2  # exactly one retry
    assert result["days"]["2026-09-14"]["log_read_failures"] == 1


def test_mostly_empty_log_scan_of_enough_records_is_red(source, tmp_path):
    result = run(source, ["2026-09-14"], tmp_path, read_log=lambda r: [] if not r["id"].endswith("-1") else r["events"])
    assert result["exit_code"] == EXIT_RED
    assert any("empty" in r for r in result["reasons"])


def test_green_run_reaches_the_write_and_leaves_a_receipt(source, tmp_path):
    result = run(source, ["2026-09-14"], tmp_path)
    assert result["exit_code"] == EXIT_OK and receipt.read(tmp_path) is not None


def test_week_aggregation_carries_unmeasured_through(source, tmp_path):
    result = run(source, ["2026-09-09", STALE_DAY], tmp_path, sms_store_ok=lambda d: d != STALE_DAY)
    w = week_of(result["days"], ["2026-09-09", STALE_DAY])
    assert w["team"]["sms_replies"] is None and w["team"]["leads"] > 0


# --- delivery: foreground glyphs, post-upload byte check with a hard stop

def test_stoplight_is_foreground_glyph_not_background_fill():
    for band in (GREEN, RED):
        c = chip(band)
        assert "color:" in c and "background" not in c and "&#9679;" in c
    assert "background" not in to_html("t", week(metrics(**BASE), {"Caller A": metrics(**BASE)}), {}, "", [])


def test_post_upload_size_divergence_over_5_percent_stops():
    html = "<html>" + "x" * 1000 + "</html>"
    assert deliver(html, upload=len) == len(html.encode())
    assert deliver(html, upload=lambda b: int(len(b) * 0.97))  # within tolerance
    with pytest.raises(DeliveryStop):
        deliver(html, upload=lambda b: int(len(b) * 0.90))
