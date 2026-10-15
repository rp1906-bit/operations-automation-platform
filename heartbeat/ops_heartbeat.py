#!/usr/bin/env python3
"""Fleet heartbeat: does every scheduled workflow still fire, succeed, and get seen?

Watches ALL scheduled GitHub Actions workflows from outside every one of them.

Why it has to exist separately from each job's own Slack post: a job that never
fires posts nothing, and a missing message looks exactly like a quiet morning.
Per-job alerting is structurally blind to absence — the only thing that can see
it is something that knows what was *supposed* to run. On 2026-09-09 this repo
had `weekly-rollup` with zero runs ever and `nightly-sync` with zero
successes ever, and neither had said a word.

Pattern and several of the rules below follow a single-server SSH-probe
heartbeat I had evaluated earlier, which hit the same
failure first ("four of the nine scheduled days produced zero notices, and the
only reason anyone found out was going to look").

Three rules inherited from that design, each load-bearing:
  1. The verdict is READ OFF THE PRINTED LINE, never inferred from an exit code.
     A watchdog that silently always passes is worse than no watchdog.
  2. A missing webhook is a FAILURE, not a reason to go quiet. This is the exact
     bug that made `nightly-sweep.yml` silent for its whole life: it referenced
     NIGHTLY_SWEEP_SLACK_WEBHOOK_URL, which was never created. Delivery is
     therefore attempted BEFORE the verdict line is printed, so a heartbeat that
     cannot reach anyone can never print a passing verdict.
  3. One missed run is noise; a sustained gap is the signal. Escalate on streaks.

    python heartbeat/ops_heartbeat.py --repo owner/name        # print verdict
    python heartbeat/ops_heartbeat.py --repo owner/name --post # and post to Slack
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
HTTP_TIMEOUT = 30
RUN_PAGE = 30

# Every error class a urlopen+json.loads round trip can raise. TimeoutError and
# JSONDecodeError are NOT URLError subclasses, so catching URLError alone lets a
# single flaky read kill the whole digest instead of degrading one workflow.
NET_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)

# How late a run may be before it counts as overdue. GitHub queues rather than
# guarantees cron, and delays of 5-7 h are routine here (the scraper watchdog's
# first live firing was ~6 h late), so the grace has to absorb that without
# absorbing a whole missed cycle.
GRACE = timedelta(hours=12)

# Consecutive failures before a red streak. One is a bad night; three is broken.
STREAK_RED = 3

# Conclusions that mean the run genuinely broke. `cancelled` is what concurrency
# groups produce and `skipped` is what a workflow-level `if:` produces; neither
# is a fault, and counting them as one would make healthy jobs red.
FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}

# A workflow opts out of the "no Slack wired" finding by SAYING SO in its own
# file, so silence is a recorded decision rather than an oversight:
#     # heartbeat: no-slack -- reports by opening a PR, not by posting
# A watchdog that is amber every single day gets ignored, which is the failure
# this module exists to fix.
NO_SLACK_MARKER = re.compile(r"^\s*#\s*heartbeat:\s*no-slack\b", re.M)

# `- cron: "0 11 * * 1"   # Mondays` / `- cron: '0 11 * * 1'` / `- cron: 0 11 * * 1`
CRON_RE = re.compile(
    r"""^\s*-\s*cron:\s*(?:"([^"\n]+)"|'([^'\n]+)'|([^#\n]+?))\s*(?:\#.*)?$""",
    re.M)

RED, AMBER, GREEN = "RED", "AMBER", "GREEN"
_RANK = {GREEN: 0, AMBER: 1, RED: 2}


def _worst(*levels: str) -> str:
    return max(levels, key=_RANK.get)


class UnsupportedCron(Exception):
    """A cron this module cannot reason about. Never treated as healthy."""


@dataclass
class Workflow:
    file: str
    name: str
    crons: list[str]
    notify_secrets: set[str] = field(default_factory=set)
    parse_warning: str | None = None
    slack_opt_out: bool = False


def parse_workflows(wf_dir: str) -> list[Workflow]:
    """Read schedules straight out of the workflow files.

    The workflow files are the single source of truth for what should run, so
    this never drifts from reality the way a hand-maintained list would.

    A file that declares `schedule:` but yields no parseable cron is returned
    WITH a warning rather than dropped: an unwatched job must never look
    identical to a job that does not exist.
    """
    out: list[Workflow] = []
    for fn in sorted(os.listdir(wf_dir)):
        if not fn.endswith((".yml", ".yaml")):
            continue
        text = open(os.path.join(wf_dir, fn), encoding="utf-8").read()
        # Quotes are optional in YAML and a trailing `# comment` is legal after
        # the value. An earlier version of this regex required quotes AND a bare
        # end of line, which silently dropped nightly-sweep.yml -- the very job
        # whose silence this module was written to catch. Handle all three forms.
        crons = [(a or b or c).strip() for a, b, c in CRON_RE.findall(text)]
        declares_schedule = re.search(r"^\s*schedule:\s*$", text, re.M) is not None
        if not crons and not declares_schedule:
            continue
        m = re.search(r"^name:\s*(.+?)\s*$", text, re.M)
        secrets = set(re.findall(r"secrets\.([A-Z0-9_]*(?:SLACK|WEBHOOK)[A-Z0-9_]*)", text))
        warn = None if crons else "declares schedule: but no cron could be parsed"
        out.append(Workflow(fn, m.group(1) if m else fn, crons, secrets, warn,
                            bool(NO_SLACK_MARKER.search(text))))
    return out


def prev_fire(cron: str, now: datetime) -> datetime:
    """Most recent time this cron should have fired, at or before `now`.

    Deliberately supports only `M H * * DOW` — every schedule in this repo. Any
    richer expression raises rather than being guessed at, because a wrong
    "not due yet" is exactly the silent pass this whole module exists to stop.
    """
    parts = cron.split()
    if len(parts) != 5:
        raise UnsupportedCron(cron)
    minute, hour, dom, mon, dow = parts
    if dom != "*" or mon != "*":
        raise UnsupportedCron(cron)
    if not (minute.isdigit() and hour.isdigit()):
        raise UnsupportedCron(cron)
    if not 0 <= int(minute) <= 59 or not 0 <= int(hour) <= 23:
        raise UnsupportedCron(cron)

    days = _dow_set(dow)
    target = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    # Walk back at most a fortnight; every supported pattern fires within a week.
    for back in range(15):
        cand = target - timedelta(days=back)
        # cron uses 0=Sunday; Python's weekday() uses 0=Monday.
        if cand <= now and ((cand.weekday() + 1) % 7) in days:
            return cand
    raise UnsupportedCron(cron)


def _dow_set(dow: str) -> set[int]:
    """Expand a cron day-of-week field to {0..6}, 0=Sunday."""
    if dow in ("*", "?"):
        return set(range(7))
    days: set[int] = set()
    for piece in dow.split(","):
        if "-" in piece:
            lo, hi = piece.split("-", 1)
            if not (lo.isdigit() and hi.isdigit()) or int(lo) > int(hi):
                raise UnsupportedCron(dow)
            days.update(range(int(lo), int(hi) + 1))
        elif piece.isdigit():
            days.add(int(piece))
        else:
            raise UnsupportedCron(dow)
    if any(d > 7 for d in days):
        raise UnsupportedCron(dow)
    return {d % 7 for d in days}


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ops-heartbeat",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def fetch_runs(repo: str, wf_file: str, token: str,
               limit: int = RUN_PAGE) -> tuple[list[dict], int]:
    """(scheduled runs, total_count). Scheduled events only.

    Filtering server-side matters: a dispatch-heavy workflow would otherwise
    crowd its own cron runs out of the page and be reported as never having run.

    A 200 whose envelope we do not recognise is upstream drift, NOT an empty
    result — returning [] there would announce that every job had never run.
    """
    url = (f"{API}/repos/{repo}/actions/workflows/{wf_file}/runs"
           f"?event=schedule&per_page={limit}")
    data = _get(url, token)
    if "workflow_runs" not in data:
        raise ValueError("GitHub returned 200 without workflow_runs (schema drift)")
    return data["workflow_runs"], int(data.get("total_count", len(data["workflow_runs"])))


def fetch_registered(repo: str, token: str) -> set[str] | None:
    """Basenames of the workflows GitHub has registered, or None if unreadable.

    Without this, a 404 on the runs endpoint is ambiguous: it means "new file,
    not registered yet" OR "this token has no grant on the repo at all". On a
    PRIVATE repo GitHub returns 404 rather than 403 for the second case, so
    guessing the benign one prints a reassuring story over a totally blind fleet.
    """
    try:
        data = _get(f"{API}/repos/{repo}/actions/workflows?per_page=100", token)
    except NET_ERRORS:
        return None
    if "workflows" not in data:
        return None
    return {os.path.basename(w.get("path", "")) for w in data["workflows"]}


def fetch_secret_names(repo: str, token: str) -> tuple[set[str] | None, str]:  # noqa: D401
    """(names, reason_it_failed). names is None when the question could not be asked.

    Never conflates "could not ask" with "no secrets exist": the first must raise
    a fleet-level caveat, the second would mark every webhook as missing. The
    reason is carried because 401 (dead token), 403 (no scope OR rate limit),
    404 (renamed repo) and 500 (GitHub down) are different problems and the
    operator needs to know which one they have.
    """
    try:
        data = _get(f"{API}/repos/{repo}/actions/secrets?per_page=100", token)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} {exc.reason}"
    except NET_ERRORS as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    if "secrets" not in data:
        return None, "200 with an unrecognised envelope (schema drift)"
    return {s["name"] for s in data["secrets"]}, ""


def _ts(run: dict) -> datetime | None:
    raw = run.get("run_started_at") or run.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def assess(wf: Workflow, runs: list[dict], now: datetime, secrets: set[str] | None,
           total: int | None = None) -> tuple[str, list[str], bool]:
    """(level, reasons, watched).

    `watched` is False whenever this check could not actually judge the job —
    an unreadable schedule, an unreadable run history. It is a separate fact
    from the level: "I looked and it is fine" and "I could not look" are both
    non-RED and must never be added together.
    """
    level, notes = GREEN, []

    if wf.parse_warning:
        return AMBER, [wf.parse_warning + " — this job is NOT being watched"], False

    # --- can it even tell anyone? ---
    if not wf.notify_secrets and not wf.slack_opt_out:
        level = _worst(level, AMBER)
        notes.append("has no Slack notification wired at all")
    elif secrets is not None and wf.notify_secrets:
        missing = sorted(s for s in wf.notify_secrets if s not in secrets)
        if missing:
            level = RED
            notes.append(f"references secret {', '.join(missing)} which does not exist "
                         f"— this job can never alert anyone")

    # --- did it fire? ---
    # An unparseable schedule is reported, never assumed well — but it must not
    # discard the run outcomes, which were fetched and are unambiguous. So this
    # sets a level and falls through rather than returning early.
    watched = True
    due = prior = None
    try:
        due = max(prev_fire(c, now) for c in wf.crons)
        prior = max(prev_fire(c, due - timedelta(seconds=1)) for c in wf.crons)
    except (UnsupportedCron, ValueError) as exc:
        level = _worst(level, AMBER)
        watched = False
        notes.append(f"schedule '{exc}' is not one this check understands "
                     f"— its timing is NOT being watched")

    stamps, undated = [], 0
    for r in runs:
        t_ = _ts(r)
        stamps.append(t_) if t_ else (undated := undated + 1)
    if undated:
        level = _worst(level, AMBER)
        notes.append(f"{undated} run(s) had no readable start time and were excluded")
    last = max(stamps, default=None)

    pending = False
    if due is not None:
        if last is None:
            level = RED
            # Scope the claim: this window is the newest RUN_PAGE scheduled runs.
            scope = ("no scheduled run has EVER happened" if total == 0
                     else f"no scheduled run in the {len(runs)} most recent"
                     if runs else "no scheduled run found in the fetched window")
            notes.append(f"{scope} (due since {due:%Y-%m-%d %H:%M} UTC)")
        elif last < prior or (last < due and now >= due + GRACE):
            level = RED
            hrs = int((now - last).total_seconds() // 3600)
            missed = " (missed at least one full cycle)" if last < prior else ""
            notes.append(f"OVERDUE{missed} — expected {due:%a %d %b %H:%M} UTC, nothing "
                         f"since {last:%a %d %b %H:%M} UTC ({hrs}h ago)")
        elif last < due:
            pending = True
            notes.append(f"due {due:%a %d %b %H:%M} UTC, still within the grace window")

    # --- did it work? ---
    finished = [r for r in runs if r.get("conclusion")]
    if runs and not finished:
        level = _worst(level, AMBER)
        notes.append(f"all {len(runs)} recent runs are still queued or in progress "
                     f"— no runner, or the workflow is stuck")
    elif finished:
        # GitHub returns newest-first, but nothing here should depend on an
        # undocumented ordering guarantee.
        finished.sort(key=lambda r: _ts(r) or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=True)
        broken = [r for r in finished if r["conclusion"] in FAILED_CONCLUSIONS]
        if broken and len(broken) == len(finished):
            level = RED
            notes.append(f"no success in the {len(finished)} most recent scheduled runs "
                         f"({', '.join(sorted({r['conclusion'] for r in finished}))})")
        else:
            streak = 0
            for r in finished:
                if r["conclusion"] not in FAILED_CONCLUSIONS:
                    break
                streak += 1
            if streak >= STREAK_RED:
                level = RED
                notes.append(f"failing {streak} runs in a row")
            elif streak:
                level = _worst(level, AMBER)
                notes.append(f"last run failed ({streak} since a success)")
            odd = sorted({r["conclusion"] for r in finished
                          if r["conclusion"] not in FAILED_CONCLUSIONS
                          and r["conclusion"] != "success"})
            if odd:
                notes.append(f"note: recent runs include {', '.join(odd)}")

    if level == GREEN and finished and not pending:
        when = _ts(finished[0])
        if when:
            notes.append(f"last ok {when:%a %d %b %H:%M} UTC")
    if pending:
        notes.append("PENDING")
    return level, notes, watched


def is_blind(blind: int, total: int) -> bool:
    """Is the fleet blind enough that this run must not read as a pass?

    The realistic failure is all-or-nothing — a scope or credential fault hits
    every call — so total blindness is always RED regardless of fleet size. Below
    that, one unreadable seam is noise (escalating on it trains people to ignore
    the channel, which is the failure this tool exists to fix) but a third of the
    fleet, minimum two, is not.
    """
    if total and blind == total:
        return True
    return blind >= max(2, -(-total // 3))


def render(results: list[tuple[Workflow, str, list[str], bool]], repo: str,
           caveats: list[str] | None = None, scanned: int | None = None) -> tuple[str, str]:
    """Return (verdict_line, slack_text). The verdict line is the contract.

    Blindness is counted from the rows, never passed in: a caller that forgot to
    pass it would silently disable the escalation, and per ENGINEERING_STANDARDS
    the safe default is never optional.
    """
    caveats = caveats or []
    worst = _worst(*[r[1] for r in results], GREEN)
    if caveats:
        worst = _worst(worst, AMBER)
    blind = sum(1 for r in results if not r[3])
    if is_blind(blind, len(results)):
        worst = RED
        caveats = [f"BLIND — {blind} of {len(results)} jobs could not be judged at all. "
                   f"Nothing is being watched while this is true."] + caveats
    bad = [r for r in results if r[1] != GREEN]
    # A job merely waiting inside its grace window is not "healthy" and not a
    # problem. Counting it as either makes the headline a lie in one direction.
    pending = [r for r in results if r[1] == GREEN and "PENDING" in r[2]]
    # Row problems and fleet-level caveats are different denominators. Adding
    # them printed "RED 10 of 8 scheduled jobs" on the real incident.
    n_issues = len(bad)

    if n_issues or caveats:
        headline = f"{worst} {n_issues} of {len(results)} scheduled jobs need attention"
        if caveats:
            headline += f" (+{len(caveats)} config warning{'s' if len(caveats) > 1 else ''})"
    elif pending:
        headline = (f"{GREEN} {len(results) - len(pending)} healthy, "
                    f"{len(pending)} pending (not yet overdue)")
    else:
        headline = f"{GREEN} all {len(results)} scheduled jobs healthy"

    icon = {RED: ":red_circle:", AMBER: ":large_yellow_circle:", GREEN: ":white_check_mark:"}
    lines = [f"*Ops heartbeat — {datetime.now(timezone.utc):%a %d %b}*  ({repo})", ""]
    for c in caveats:
        lines.append(f":warning: {c}")
    if caveats:
        lines.append("")
    # Problems first: a job nobody chases is a job nobody is running.
    for wf, lvl, notes, _w in sorted(results, key=lambda r: -_RANK[r[1]]):
        shown = [n for n in notes if n != "PENDING"] or ["ok"]
        lines.append(f"{icon[lvl]} *{wf.name}* — {'; '.join(shown)}")
    lines.append("")
    if scanned is not None:
        # State the denominator: a workflow this module failed to parse would
        # otherwise be invisible under a headline claiming everything is watched.
        lines.append(f"_watching {len(results)} of {scanned} workflow files scanned._")
    lines.append(f"_{n_issues + len(caveats)} need you._ This came from the fleet watcher, not from the "
                 "jobs themselves, so it also fires when a job never runs at all.")
    return headline, "\n".join(lines)


def post_slack(webhook: str, text: str) -> None:
    """Deliver the digest. Raises on any failure — delivery is not optional here.

    A Slack webhook can answer 200 with a non-`ok` body, so the body is checked
    rather than the status alone.
    """
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(webhook, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        answer = r.read().decode("utf-8", "replace").strip()
    if answer != "ok":
        raise RuntimeError(f"Slack rejected the digest: {answer[:200]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                    help="owner/name (defaults to $GITHUB_REPOSITORY)")
    ap.add_argument("--workflows-dir", default=".github/workflows")
    ap.add_argument("--post", action="store_true", help="post the digest to Slack")
    args = ap.parse_args()

    if not args.repo:
        print("RED cannot run: no --repo and no $GITHUB_REPOSITORY")
        return 2
    # Two tokens, two scopes, deliberately NOT interchangeable. The workflow
    # GITHUB_TOKEN can read Actions history but never secret names; a
    # fine-grained PAT scoped to Secrets:read can read names but has no
    # Actions:read. Using one for both blinded the whole fleet the moment the
    # PAT was added, and the run still went green (2026-09-09, run 34385475247).
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("RED cannot run: no GITHUB_TOKEN")
        return 2
    secrets_token = os.environ.get("HEARTBEAT_SECRETS_TOKEN") or token

    workflows = parse_workflows(args.workflows_dir)
    if not workflows:
        print(f"RED no scheduled workflows found in {args.workflows_dir} — "
              "either they were deleted or this check is pointed at the wrong tree")
        return 1

    scanned = len([f for f in os.listdir(args.workflows_dir)
                   if f.endswith((".yml", ".yaml"))])

    caveats: list[str] = []
    secrets, why = fetch_secret_names(args.repo, secrets_token)
    if secrets is None:
        # Not a per-job note: it disables the missing-secret detector fleet-wide,
        # which is one of the two failures this module exists to catch.
        fix = ("rotate HEARTBEAT_READ_TOKEN — it is present but rejected (expired?)"
               if "401" in why else
               "grant HEARTBEAT_READ_TOKEN the Secrets:read permission on this repo"
               if "403" in why else
               "check the repo name and that HEARTBEAT_READ_TOKEN is granted this repo"
               if "404" in why else
               "set HEARTBEAT_READ_TOKEN")
        caveats.append(f"Notification wiring is UNVERIFIED for every job below — "
                       f"could not read the repo's secret list ({why}). To fix: {fix}.")

    registered = fetch_registered(args.repo, token)
    if registered is None:
        caveats.append("Could not list this repo's registered workflows, so a 404 below "
                       "cannot be told apart from a missing permission.")

    results = []
    now = datetime.now(timezone.utc)
    for wf in workflows:
        try:
            runs, total = fetch_runs(args.repo, wf.file, token)
            results.append((wf, *assess(wf, runs, now, secrets, total)))
        except urllib.error.HTTPError as exc:
            # A 404 is only benign when we have INDEPENDENTLY confirmed the file
            # is not registered. Otherwise it is a permissions failure wearing a
            # friendly face — on a private repo that is what "no grant" looks like.
            if exc.code == 404 and registered is not None and wf.file not in registered:
                results.append((wf, AMBER, ["not registered on the default branch yet "
                                            "— it will not fire until this merges"], True))
            else:
                results.append((wf, AMBER,
                                [f"could not assess (HTTP {exc.code} {exc.reason})"], False))
        except Exception as exc:      # noqa: BLE001 - deliberate, see below
            # Deliberately broad: one unreadable seam must degrade ONE row, never
            # discard the verdicts already computed for the rest of the fleet.
            results.append((wf, AMBER,
                            [f"could not assess ({exc.__class__.__name__}: {exc})"], False))

    headline, text = render(results, args.repo, caveats, scanned)

    # Rule 2: deliver BEFORE printing the verdict. If the digest cannot reach a
    # human, this run must not be able to print a passing line at all.
    if args.post:
        webhook = os.environ.get("OPS_HEARTBEAT_SLACK_WEBHOOK_URL")
        if not webhook:
            print("RED heartbeat cannot reach anyone: "
                  "OPS_HEARTBEAT_SLACK_WEBHOOK_URL is not set")
            return 2
        try:
            post_slack(webhook, text)
        except (RuntimeError, *NET_ERRORS) as exc:
            print(f"RED heartbeat could not deliver its digest: {exc}")
            return 2

    print(headline)          # rule 1: the verdict is this line
    print()
    print(text)
    return 1 if headline.startswith(RED) else 0


if __name__ == "__main__":
    raise SystemExit(main())
