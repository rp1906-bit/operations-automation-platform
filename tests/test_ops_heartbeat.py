"""Tests for the fleet heartbeat.

The bias throughout: every uncertain path must land on AMBER or RED. A watchdog
that silently passes is worse than no watchdog, so "we could not tell" is never
allowed to render as GREEN.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from heartbeat.ops_heartbeat import (
    AMBER, GREEN, RED, UnsupportedCron, Workflow, _dow_set, assess, main,
    parse_workflows, post_slack, prev_fire, render,
)

UTC = timezone.utc


def _wf(**kw):
    base = dict(file="w.yml", name="W", crons=["0 12 * * 1-5"],
                notify_secrets={"SLACK_WEBHOOK_URL"})
    base.update(kw)
    return Workflow(**base)


def _run(days_ago, conclusion="success", event="schedule", now=None):
    now = now or datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    return {"run_started_at": (now - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
            "conclusion": conclusion, "event": event}


# ---------- cron maths ----------

def test_prev_fire_weekday_returns_today_when_hour_passed():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)          # Wednesday
    assert prev_fire("0 12 * * 1-5", now) == datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_prev_fire_weekday_skips_back_over_the_weekend():
    now = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)          # Sunday
    assert prev_fire("0 12 * * 1-5", now) == datetime(2026, 9, 4, 12, 0, tzinfo=UTC)  # Friday


def test_prev_fire_before_todays_hour_uses_yesterday():
    now = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)           # Wed, before 12:00
    assert prev_fire("0 12 * * 1-5", now) == datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def test_prev_fire_weekly_monday():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)          # Wednesday
    assert prev_fire("0 11 * * 1", now) == datetime(2026, 9, 7, 11, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", [
    "*/5 * * * *",        # step values
    "0 12 1 * *",         # day-of-month
    "0 12 * 3 *",         # month
    "0 12 * * MON",       # names
    "0 12 * *",           # too few fields
])
def test_unsupported_crons_raise_rather_than_guess(cron):
    with pytest.raises(UnsupportedCron):
        prev_fire(cron, datetime(2026, 9, 9, 15, 0, tzinfo=UTC))


def test_dow_handles_ranges_and_both_spellings_of_sunday():
    assert _dow_set("1-5") == {1, 2, 3, 4, 5}
    assert _dow_set("2-6") == {2, 3, 4, 5, 6}
    assert _dow_set("0") == _dow_set("7") == {0}
    assert _dow_set("*") == set(range(7))


# ---------- verdicts ----------

def test_healthy_job_is_green():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, _notes, watched = assess(_wf(), [_run(0)], now, {"SLACK_WEBHOOK_URL"})
    assert level == GREEN and watched


def test_missing_secret_is_red_because_the_job_can_never_alert():
    """The real nightly-sweep.yml bug: it referenced a secret nobody created."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    wf = _wf(notify_secrets={"NIGHTLY_SWEEP_SLACK_WEBHOOK_URL"})
    level, notes, _w = assess(wf, [_run(0)], now, {"SLACK_WEBHOOK_URL"})
    assert level == RED
    assert "does not exist" in " ".join(notes)


def test_never_ran_on_schedule_is_red():
    """weekly-rollup: zero runs ever, and nothing ever said so."""
    now = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)   # past due+grace
    level, notes, _w = assess(_wf(), [], now, {"SLACK_WEBHOOK_URL"}, total=0)
    assert level == RED
    assert "EVER happened" in " ".join(notes)


def test_never_succeeded_is_red():
    """nightly-sync: three runs, three failures, no success ever."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    runs = [_run(0, "failure"), _run(1, "failure"), _run(2, "failure")]
    level, notes, _w = assess(_wf(), runs, now, {"SLACK_WEBHOOK_URL"})
    assert level == RED
    # Scoped, not absolute: all we know is the fetched window.
    assert "no success in the 3 most recent" in " ".join(notes)


def test_overdue_job_is_red():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, notes, _w = assess(_wf(), [_run(9)], now, {"SLACK_WEBHOOK_URL"})
    assert level == RED
    assert "OVERDUE" in " ".join(notes)


def test_a_run_still_inside_the_grace_window_is_not_overdue():
    """GitHub starts crons hours late. Crying wolf is how a watchdog gets ignored.

    Due Wed 12:00; it is now 15:00, so only 3 h past due against a 12 h grace.
    Yesterday's run is the most recent one, and that must read as pending, not
    as a failure. This was a real false RED on Caller Reputation Monitor.
    """
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    yesterday_late = {"run_started_at": "2026-09-08T23:00:00Z",
                      "conclusion": "success", "event": "schedule"}
    level, notes, _w = assess(_wf(), [yesterday_late], now, {"SLACK_WEBHOOK_URL"})
    assert level == GREEN
    assert "OVERDUE" not in " ".join(notes)


def test_overdue_fires_once_the_grace_window_has_passed():
    now = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)   # 18 h past Wed 12:00
    stale = {"run_started_at": "2026-09-08T23:00:00Z",
             "conclusion": "success", "event": "schedule"}
    level, notes, _w = assess(_wf(), [stale], now, {"SLACK_WEBHOOK_URL"})
    assert level == RED
    assert "OVERDUE" in " ".join(notes)


def test_zero_runs_ever_is_red_as_soon_as_a_slot_has_elapsed():
    """Deliberate: "never ran" outranks the grace window.

    A newly added workflow will flag once and go green after its first fire.
    That false positive is worth accepting, because the silent case it replaces
    is weekly-rollup — a scheduled job with zero runs in its entire life that
    nothing ever mentioned, precisely because absence emits nothing.
    """
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, notes, _w = assess(_wf(), [], now, {"SLACK_WEBHOOK_URL"}, total=0)
    assert level == RED
    assert "EVER happened" in " ".join(notes)


def test_missing_a_whole_prior_cycle_is_red_without_waiting_for_grace():
    """Nine days silent must not hide behind today's grace window."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, notes, _w = assess(_wf(), [_run(9, now=now)], now, {"SLACK_WEBHOOK_URL"})
    assert level == RED
    assert "missed at least one full cycle" in " ".join(notes)


def test_one_failure_is_amber_but_a_streak_is_red():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    one = [_run(0, "failure"), _run(1, "success")]
    assert assess(_wf(), one, now, {"SLACK_WEBHOOK_URL"})[0] == AMBER

    streak = [_run(0, "failure"), _run(1, "failure"), _run(2, "failure"), _run(3, "success")]
    assert assess(_wf(), streak, now, {"SLACK_WEBHOOK_URL"})[0] == RED


def test_two_failures_in_a_row_is_still_AMBER_not_a_red_streak():
    """STREAK_RED is three. Two bad nights after a success is not yet broken."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    two = [_run(0, "failure"), _run(1, "failure"), _run(2, "success")]
    assert assess(_wf(), two, now, {"SLACK_WEBHOOK_URL"})[0] == AMBER


def test_no_notification_wired_is_amber():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, notes, _w = assess(_wf(notify_secrets=set()), [_run(0)], now, {"X"})
    assert level == AMBER
    assert "no Slack notification" in " ".join(notes)


def test_unparseable_schedule_never_reads_as_healthy():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    level, notes, _w = assess(_wf(crons=["*/5 * * * *"]), [_run(0)], now, {"SLACK_WEBHOOK_URL"})
    assert level == AMBER
    assert "not one this check understands" in " ".join(notes)


def test_unreadable_secret_list_can_never_render_as_all_healthy():
    """The single assertion whose absence let the first version ship broken.

    On the default GITHUB_TOKEN the secret list is unreadable, so this is the
    PRODUCTION path, not an edge case. It must never headline as healthy.
    """
    caveat = ["Notification wiring is UNVERIFIED for every job below — "
              "could not read the repo's secret list (HTTP 403 Forbidden)."]
    headline, text = render([(_wf(), GREEN, ["last ok"], True)], "o/r", caveat)
    assert not headline.startswith(GREEN), "unknown wiring rendered as healthy"
    assert headline.startswith(AMBER)
    assert "UNVERIFIED" in text


def test_never_ran_says_EVER_only_when_the_true_total_is_zero():
    """Scope the negative claim. "Never" is only sayable from total_count."""
    now = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    lvl, notes, _w = assess(_wf(), [], now, {"SLACK_WEBHOOK_URL"}, total=0)
    assert lvl == RED and "EVER happened" in " ".join(notes)

    # Same empty page, but the API says older runs exist: claim only the window.
    lvl, notes, _w = assess(_wf(), [], now, {"SLACK_WEBHOOK_URL"}, total=57)
    assert lvl == RED and "fetched window" in " ".join(notes)
    assert "EVER" not in " ".join(notes)


# ---------- rendering ----------

def test_verdict_line_leads_with_the_level_so_it_can_be_grepped():
    wfs = [(_wf(), RED, ["broken"], True), (_wf(name="B"), GREEN, ["ok"], True)]
    headline, text = render(wfs, "owner/repo")
    assert headline.startswith(RED)
    assert "1 of 2" in headline
    assert "*B*" in text


def test_all_green_says_so():
    headline, _ = render([(_wf(), GREEN, [], True)], "owner/repo")
    assert headline.startswith(GREEN)


# ---------- regressions from the review gate ----------

def test_skipped_and_cancelled_are_not_failures():
    """A concurrency cancel or an `if:` guard is not a broken job."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    runs = [_run(0, "skipped"), _run(1, "cancelled"), _run(2, "success")]
    level, notes, _w = assess(_wf(), runs, now, {"SLACK_WEBHOOK_URL"})
    assert level == GREEN
    assert "failing" not in " ".join(notes)
    assert "skipped" in " ".join(notes)      # surfaced, not silently ignored


def test_an_unparseable_cron_still_reports_the_failures_it_can_see():
    """Uncertainty about the schedule must not suppress known outcomes."""
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    runs = [_run(i, "failure") for i in range(5)]
    level, notes, _w = assess(_wf(crons=["*/15 * * * *"]), runs, now, {"SLACK_WEBHOOK_URL"})
    assert level == RED, "five straight failures were discarded by a cron it could not parse"
    assert "no success" in " ".join(notes)


def test_runs_stuck_queued_forever_are_not_healthy():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    queued = [{"run_started_at": "2026-09-09T12:00:00Z", "conclusion": None,
               "event": "schedule"}]
    level, notes, _w = assess(_wf(), queued, now, {"SLACK_WEBHOOK_URL"})
    assert level == AMBER
    assert "queued or in progress" in " ".join(notes)


def test_runs_with_unreadable_timestamps_are_counted_not_dropped():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    runs = [{"run_started_at": "not-a-date", "conclusion": "success", "event": "schedule"},
            _run(0)]
    level, notes, _w = assess(_wf(), runs, now, {"SLACK_WEBHOOK_URL"})
    assert level == AMBER
    assert "no readable start time" in " ".join(notes)


def test_pending_is_neither_healthy_nor_a_problem():
    wf = _wf()
    headline, _ = render([(wf, GREEN, ["due later", "PENDING"], True)], "o/r")
    assert "pending" in headline
    assert "all 1 scheduled jobs healthy" not in headline


def test_digest_states_how_many_files_it_actually_watched():
    """Without the denominator, an unparsed workflow is invisible."""
    _, text = render([(_wf(), GREEN, [], True)], "o/r", scanned=12)
    assert "watching 1 of 12" in text


# ---------- parse_workflows: the module's source of truth ----------

def _write(tmp_path, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")


def test_unquoted_cron_is_still_watched(tmp_path):
    """Valid YAML that GitHub honours. Missing it would silently unwatch a job."""
    _write(tmp_path, "a.yml", "name: A\non:\n  schedule:\n    - cron: 0 12 * * 1\n")
    wfs = parse_workflows(str(tmp_path))
    assert [w.crons for w in wfs] == [["0 12 * * 1"]]


def test_a_schedule_with_no_parseable_cron_is_reported_not_dropped(tmp_path):
    _write(tmp_path, "a.yml", "name: A\non:\n  schedule:\n    - {}\n")
    wfs = parse_workflows(str(tmp_path))
    assert len(wfs) == 1
    level, notes, _w = assess(wfs[0], [], datetime.now(timezone.utc), set())
    assert level == AMBER
    assert "NOT being watched" in " ".join(notes)


def test_unscheduled_workflows_are_ignored(tmp_path):
    _write(tmp_path, "a.yml", "name: A\non:\n  push:\n    branches: [main]\n")
    assert parse_workflows(str(tmp_path)) == []


def test_a_job_can_opt_out_of_the_slack_finding_by_saying_so(tmp_path):
    body = ("name: A\n# heartbeat: no-slack -- reports by opening a PR\n"
            "on:\n  schedule:\n    - cron: '0 12 * * 1'\n")
    _write(tmp_path, "a.yml", body)
    wf = parse_workflows(str(tmp_path))[0]
    assert wf.slack_opt_out is True
    level, notes, _w = assess(wf, [_run(0)], datetime(2026, 9, 9, 15, 0, tzinfo=UTC), set())
    assert "no Slack notification" not in " ".join(notes)


def test_without_the_marker_a_slackless_job_is_still_flagged(tmp_path):
    _write(tmp_path, "a.yml", "name: A\non:\n  schedule:\n    - cron: '0 12 * * 1'\n")
    wf = parse_workflows(str(tmp_path))[0]
    assert wf.slack_opt_out is False


# ---------- delivery: the bug this whole module exists to prevent ----------

def test_slack_rejecting_the_digest_raises(monkeypatch):
    """A 200 with a non-ok body means the message did not land."""
    class _R:
        def read(self): return b"no_service"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _R())
    with pytest.raises(RuntimeError, match="Slack rejected"):
        post_slack("https://hooks.example/x", "hi")


def test_main_refuses_to_print_a_passing_verdict_when_it_cannot_deliver(monkeypatch, capsys):
    """The nightly-sweep bug, reproduced inside the watchdog, must be impossible."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("OPS_HEARTBEAT_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows",
                        lambda d: [_wf()])
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: ({"SLACK_WEBHOOK_URL"}, ""))
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_registered",
                        lambda r, t: {"w.yml"})
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs",
                        lambda r, f, t, limit=30: ([_run(0)], 9))
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r", "--post"])
    rc = main()
    out = capsys.readouterr().out
    assert rc == 2
    assert out.startswith(RED), "printed a non-RED verdict while unable to reach anyone"
    assert GREEN not in out.splitlines()[0]


# ---------- the 2026-09-09 regression: blind, and green about it ----------

def test_a_blind_fleet_is_RED_not_a_green_tick():
    """Run 34385475247: a PAT without Actions:read 403'd all 8 jobs, every row
    went AMBER, and the workflow reported success. A watchdog that cannot see is
    not "a bit amber" — it is not watching, and that must never exit 0."""
    results = [(_wf(name=f"W{i}"), AMBER, ["could not assess (HTTP 403 Forbidden)"], False)
               for i in range(8)]
    headline, text = render(results, "o/r")
    assert headline.startswith(RED), "total blindness rendered as a passing verdict"
    assert "BLIND" in text


def test_half_the_fleet_blind_is_already_RED():
    results = ([(_wf(name=f"B{i}"), AMBER, ["could not assess (HTTP 403 Forbidden)"], False)
                for i in range(4)]
               + [(_wf(name=f"G{i}"), GREEN, ["last ok"], True) for i in range(4)])
    headline, _ = render(results, "o/r")
    assert headline.startswith(RED)


def test_one_flaky_seam_does_not_page_the_whole_fleet():
    """A single transient 403 is noise; escalating on it would train people to
    ignore the channel, which is the failure this tool exists to fix."""
    results = ([(_wf(name="B"), AMBER, ["could not assess (HTTP 403 Forbidden)"], False)]
               + [(_wf(name=f"G{i}"), GREEN, ["last ok"], True) for i in range(7)])
    headline, _ = render(results, "o/r")
    assert headline.startswith(AMBER), "one transient seam should not page the fleet"


def _fleet(blind, healthy):
    return ([(_wf(name=f"B{i}"), AMBER, ["could not assess (HTTP 403 Forbidden)"], False)
             for i in range(blind)]
            + [(_wf(name=f"G{i}"), GREEN, ["last ok"], True) for i in range(healthy)])


def test_a_third_of_the_fleet_blind_is_RED_and_just_under_a_third_is_not():
    """ceil(8/3) = 3: three blind of eight escalates, two blind of eight does not."""
    assert render(_fleet(3, 5), "o/r")[0].startswith(RED)
    assert render(_fleet(2, 6), "o/r")[0].startswith(AMBER)


def test_blindness_needs_at_least_two_jobs_even_in_a_small_fleet():
    """In a fleet of three, a third is one job, but one unreadable seam is still
    noise. The minimum-two floor keeps it AMBER; two of three is RED."""
    assert render(_fleet(1, 2), "o/r")[0].startswith(AMBER)
    assert render(_fleet(2, 1), "o/r")[0].startswith(RED)


def test_the_secrets_token_is_never_used_for_run_history(monkeypatch):
    """The two tokens have different scopes. Falling back from one to the other
    is what blinded the fleet — so prove each call gets the right one."""
    seen = {}

    def fake_runs(repo, wf_file, tok, limit=30):
        seen["runs"] = tok
        return [_run(0)], 9

    def fake_secrets(repo, tok):
        seen["secrets"] = tok
        return {"SLACK_WEBHOOK_URL"}, ""

    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")
    monkeypatch.setenv("HEARTBEAT_SECRETS_TOKEN", "secrets-pat")
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: [_wf()])
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs", fake_runs)
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names", fake_secrets)
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])
    main()

    assert seen["runs"] == "actions-token"
    assert seen["secrets"] == "secrets-pat"


def test_without_a_secrets_pat_the_workflow_token_is_reused(monkeypatch):
    seen = {}
    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")
    monkeypatch.delenv("HEARTBEAT_SECRETS_TOKEN", raising=False)
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: [_wf()])
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs",
                        lambda r, f, t, limit=30: ([_run(0)], 9))
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: (seen.setdefault("secrets", t), ({"SLACK_WEBHOOK_URL"}, ""))[1])
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])
    main()
    assert seen["secrets"] == "actions-token"


# ---------- F6: drive main() into blindness. The render tests were decoration. ----------

def _blind_fleet(monkeypatch, raiser, n=8):
    wfs = [_wf(name=f"W{i}", file=f"w{i}.yml") for i in range(n)]
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: wfs)
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: ({"SLACK_WEBHOOK_URL"}, ""))
    # every file IS registered, so a 404 below cannot be excused as "new file"
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_registered",
                        lambda r, t: {w.file for w in wfs})
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs", raiser)
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])


def test_a_fleet_that_403s_on_every_job_exits_RED(monkeypatch, capsys):
    """Run 34385475247 verbatim: a token without Actions:read 403s every lookup.
    main() must COUNT that, not merely be capable of rendering it."""
    import urllib.error

    def forbidden(repo, f, tok, limit=30):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    _blind_fleet(monkeypatch, forbidden)
    rc = main()
    assert capsys.readouterr().out.startswith(RED)
    assert rc == 1


def test_a_fleet_that_404s_on_every_job_exits_RED(monkeypatch, capsys):
    """The production repo is private. A token with no grant on the repo 404s rather
    than 403s, so 'not registered on the default branch yet' would be printed
    for all 8 jobs — a comforting, unverified story over a totally blind fleet."""
    import urllib.error

    def missing(repo, f, tok, limit=30):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    _blind_fleet(monkeypatch, missing)
    rc = main()
    assert capsys.readouterr().out.startswith(RED)
    assert rc == 1


def test_a_fleet_whose_crons_all_stopped_parsing_exits_RED(monkeypatch, capsys):
    """An earlier CRON_RE regression silently un-watched nightly-sweep.yml. If a
    future one un-watches everything, every row reads 'this job is NOT being
    watched' — which must not be a green tick."""
    wfs = [Workflow(f"w{i}.yml", f"W{i}", [], {"SLACK_WEBHOOK_URL"},
                    parse_warning="declares schedule: but no cron could be parsed")
           for i in range(8)]
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: wfs)
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: ({"SLACK_WEBHOOK_URL"}, ""))
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_registered",
                        lambda r, t: {w.file for w in wfs})
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs",
                        lambda r, f, t, limit=30: ([_run(0)], 9))
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])
    rc = main()
    assert capsys.readouterr().out.startswith(RED)
    assert rc == 1


def test_the_verdict_line_never_counts_more_issues_than_jobs(monkeypatch, capsys):
    """The verdict line IS the contract. Reproducing the real incident (403 on
    runs AND 403 on secrets) printed 'RED 10 of 8 scheduled jobs'."""
    import urllib.error

    def forbidden(repo, f, tok, limit=30):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    wfs = [_wf(name=f"W{i}", file=f"w{i}.yml") for i in range(8)]
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: wfs)
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: (None, "HTTP 403 Forbidden"))
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_registered",
                        lambda r, t: {w.file for w in wfs})
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs", forbidden)
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])
    main()
    head = capsys.readouterr().out.splitlines()[0]
    import re as _re
    m = _re.search(r"(\d+) of (\d+)", head)
    assert m, head
    assert int(m.group(1)) <= int(m.group(2)), f"counted more issues than jobs: {head}"


def test_a_fleet_of_unsupported_crons_exits_RED(monkeypatch, capsys):
    """Distinct from the parse-warning path: here the file parses and a cron
    string IS found, but `prev_fire` cannot reason about it, so the did-it-fire
    check is off for that job. Mutation testing caught this gap — removing the
    `watched = False` on this branch left the whole suite green."""
    wfs = [Workflow(f"w{i}.yml", f"W{i}", ["*/15 * * * *"], {"SLACK_WEBHOOK_URL"})
           for i in range(8)]
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr("heartbeat.ops_heartbeat.parse_workflows", lambda d: wfs)
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_secret_names",
                        lambda r, t: ({"SLACK_WEBHOOK_URL"}, ""))
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_registered",
                        lambda r, t: {w.file for w in wfs})
    monkeypatch.setattr("heartbeat.ops_heartbeat.fetch_runs",
                        lambda r, f, t, limit=30: ([_run(0)], 9))
    monkeypatch.setattr("sys.argv", ["x", "--repo", "o/r"])
    rc = main()
    out = capsys.readouterr().out
    assert out.startswith(RED), "a fleet whose timing is unwatched read as a pass"
    assert "NOT being watched" in out
    assert rc == 1
