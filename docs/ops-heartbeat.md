# Ops Heartbeat — reliability monitoring for business automations

An independent control layer that detects scheduled workflows that fail, never run, or lose
visibility, before a silent failure disrupts the work downstream.

Production Python implementation · 52 automated tests · generalized from a live 8-workflow environment
· code: [`heartbeat/ops_heartbeat.py`](../heartbeat/ops_heartbeat.py) · tests: [`tests/test_ops_heartbeat.py`](../tests/test_ops_heartbeat.py)

## In 30 seconds
- **Problem:** each automation could report its own failures, but a job that never started produced no alert at all.
- **Solution:** an independent fleet watcher that reads the platform's own run history, grades every scheduled workflow, and escalates when it cannot see.
- **First live run:** found four previously unknown reliability problems across eight scheduled workflows, two of which had never worked (**measured**).
- **Key lesson:** a monitor needs independent validation of its own blind spots; the review gate and a deliberate break-it test caught exactly that before the tool was relied on.

## 1. Business problem
Eight scheduled jobs each reported on themselves when they ran. None could report when they *didn't*
run: a job that silently stopped firing looked exactly like a quiet day. On 2026-09-07 a nightly job
moved 144 CRM records and could tell no one, because the notification secret it referenced had never
been created. The people affected: me, and the two team members whose daily work depends on those jobs having run.

## 2. As-is → to-be
```
BEFORE                                   AFTER
Job A ──► self-alert                     Scheduled jobs
Job B ──► self-alert                          │
Job C ──► self-alert                          ▼
                                         Platform run history (read from outside the jobs)
Job never starts ──► no signal                │
                                              ▼
                                         Independent Ops Heartbeat, once a day
                                              │
                                              ▼
                                         GREEN / AMBER / RED verdict
                                              │
                                              ▼
                                         One operational channel ──► human investigation
                                         (a missing daily post is itself the signal)
```

## 3. What I built
One script, run daily by the platform's scheduler after the whole morning cluster of jobs. It lists
every workflow file in the repo, reads each one's run history and cron schedule, judges it, checks
that the notification secret each job references actually exists, and posts one message. If the
watcher itself cannot see (a credential or scope fault), it says so loudly instead of passing.

## 4. Measured results
- **measured** — First live run, 2026-09-09: `RED 3 of 8 scheduled jobs need attention`. Four pre-existing problems nobody knew about: one weekly job had **zero runs in its entire life**; one nightly job had **never once succeeded** (a spreadsheet API 503 with no retry); one job referenced a notification secret that did not exist; one job had no notification wired at all.
- **measured** — The 2026-09-07 silent move of 144 records is now a red line in the report instead of silence.
- **not claimed** — No hours-saved figure. Nobody was doing this check by hand; the failure mode was invisible.
- **later** — Once enough production history exists, the right ongoing metric is detection coverage: injected failure scenarios correctly detected, out of those injected.

Reconstructed first-run report, job names replaced (structure from `render()`, verdict and findings real):
```
Ops heartbeat — Wed 09 Sep  (owner/production-repo)

🔴 weekly-rollup — no runs ever
🔴 nightly-sync — 3 consecutive failures, never succeeded
🔴 nightly-sweep — notify secret NIGHTLY_SWEEP_SLACK_WEBHOOK_URL not found
✅ county-scrape — ok
✅ ...

watching 8 of 8 workflow files scanned.
3 need you. This came from the fleet watcher, not from the jobs themselves,
so it also fires when a job never runs at all.
```

## 5. Design decisions and tradeoffs
- **Reuse an existing channel or open a new one?** New. Every other channel answers "how did my job go"; this one answers "is anything broken anywhere," and a channel that posts daily makes a *missing* post visible.
- **Adopt a single-server heartbeat as-is?** No. The pattern I borrowed (verdict from the printed line, not the exit code; missing webhook = failure) came from an SSH-probe heartbeat for one long-running server. My fleet is scheduled jobs with no server to probe, so the principle transferred and the mechanism didn't.
- **One credential or two?** Two, with no fallback. The platform token can read run history but never secret names; a fine-grained token can read secret names but not run history. Falling back from one to the other silently blinded all eight jobs during testing.
- **Escalate on any blindness?** No. Escalating on one transient seam trains people to ignore the channel. Total blindness is always RED; a third of the fleet (minimum two) escalates; one unreadable job stays AMBER.

## 6. What went wrong
During rollout, two iterations reported GREEN while the watcher lacked visibility (**measured**; the run
is preserved as a regression case). Root cause: blindness was an optional input with a safe default, so
a caller that omitted it silently disabled the escalation. The review gate flagged the defect, mutation
testing exposed why the test suite had not, and the same-day fix derives blindness from the rows
themselves, with no parameter to forget, plus a test that fails if that rule is ever removed. No wrong
alert reached the team.

## 7. How I validated it
We deliberately removed the control that detects blindness. Every one of the 44 tests at the time still
passed (**measured**). That proved the suite was validating the rendered message, not the business rule.
The fix was a regression test built to fail the moment that control disappears
(`test_a_fleet_that_403s_on_every_job_exits_RED`), and the README's "Run it" section documents
deleting that control as the check to repeat after any change to the verdict logic.

## 8. Technical implementation
Standard library only. Reads `.github/workflows/*.yml` for schedules and referenced secrets, the
platform's Actions API for run history, and the repository secrets list for names (two tokens, no
fallback). Verdict per workflow; worst dimension wins; the verdict line is a regex contract the CI
workflow greps. Runs at 15:30 UTC, after the morning cluster (11:00–14:05 UTC), so a late-but-real
run is not reported as a miss.

## 9. Requirements and acceptance criteria
**Core**
- Zero historical runs is never "healthy."
- An isolated failure (AMBER) is distinguished from sustained failure (three in a row, RED).
- The monitor detects when it itself lacks visibility, and refuses to pass.
- A missing notification configuration is a failure, never silence.
- Expected timing variation does not raise false alerts.

<details><summary>Implementation-level rules</summary>

- The verdict line (`GREEN|AMBER|RED …`) is a contract the CI workflow greps; nothing else in the output may match it.
- A 404 is read as "not registered" only after registration is confirmed by a second call, because a private repo answers 404, not 403, to a token with no grant.
- Blindness escalation: total = RED always; otherwise at least a third of the fleet, minimum two.
- A job inside its grace window is "pending," counted as neither healthy nor a problem.
- The watcher's own workflow is scheduled after the whole morning cluster.
</details>

## 10. Demo
**Offline, no token:** `python -m heartbeat.demo` grades two synthetic fleets from [`heartbeat/fixtures/`](../heartbeat/fixtures/) and prints, for each, the verdict line and the digest the live tool would post, plus the exit code it would return. It runs the real `ops_heartbeat.main()`: the real workflow-file parser, cron maths, grading, blindness rule and renderer. Only the three network reads (run history, registered workflows, secret names) are replaced with fixture readers, and the clock is frozen so the output is the same every time.

- *Readable fleet* (eight scheduled jobs): one healthy, one that never ran, one failing three in a row, one with a single failure, one whose notification secret does not exist, one whose run history cannot be read, one still inside its grace window, and one with a cron form the checker does not support. Verdict: `RED 6 of 8 scheduled jobs need attention`.
- *Blind fleet*: the same eight jobs with a token that cannot read run history. Verdict: `RED 8 of 8 scheduled jobs need attention (+1 config warning)`, led by the BLIND warning, never a row of ambers that passes.

**Live:** point it at any public repo with scheduled workflows:
`python -m heartbeat.ops_heartbeat --repo <owner>/<name>` (needs `GITHUB_TOKEN`; add `--post` to send to a channel).

## 11. Built with
Designed, directed, tested and operated by me. Implemented with Claude Code.
