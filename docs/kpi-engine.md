# KPI engine — measurement that separates "no data" from "bad result"

Nightly activity pulls with proof of delivery, and a weekly stoplight report that colors each metric
against agreed bands, walks a fixed diagnostic order before it names a person, and ships as a PDF
without anyone converting it by hand.

Reference implementation of a system I run in production · 20 automated tests, one per rule below
· demo: `python -m kpi_engine.demo` · code: [`kpi_engine/`](../kpi_engine/) (synthetic data)
· tests: [`tests/test_kpi_engine.py`](../tests/test_kpi_engine.py)

> Production code is private. The incidents, numbers and decisions below are from the real system,
> with names, IDs and business specifics replaced. Callers are "Caller A/B", campaigns "Campaign
> A/B/C", and every stoplight band in this repo is illustrative: the real thresholds are the
> business's operating targets.

## In 30 seconds
- **Problem:** managers wanted one question answered every Monday, *are we producing leads, and if not, whose problem is it?*, from numbers compiled by hand out of two systems, in a report with no diagnostic order.
- **Solution:** a nightly pull that writes its artifact before deciding its own exit code and leaves a receipt of what the sheet actually returned; a weekly report that colors every metric, walks Data → Phone Health → Systems → Performance before anyone is coached, and arrives as a PDF.
- **Strongest measured fact:** splitting an over-cap day into two unioned queries recovered **+7,712** records on 2026-08-29 and **+1,099** on 2026-08-31 that a single narrower query silently dropped (**measured**).
- **Key lesson:** "no data" and "bad result" are different facts and need different colors; a zero would be believed and acted on.

## 1. Business problem
Callers dial from several campaigns all day (in this reference version, Caller A/B and Campaign
A/B/C). Their managers wanted one question answered every Monday: *are we producing leads, and if not, whose problem is it: the list, the phones, one
person's setup, or the caller?* Before this system, the activity numbers were compiled by hand
from the phone system and the lead-tracking software, and the weekly report was converted to PDF
by hand because that is the format the managers read. The people affected: me, the managers who read
the report, and the callers whose week it describes. The risk with a hand-compiled
report is not just the time. It is that a bad list or a bad phone week makes a good caller look
bad, and a report with no diagnostic order invites the wrong conversation.

## 2. As-is → to-be
```
BEFORE                                   AFTER
Phone system ─┐                          Activity API (10,000-result cap)
              ├─► hand-compiled sheet        │  per day; split + union when over cap
Lead tracker ─┘        │                     ▼
                       ▼                 aggregate → daily.json  (written FIRST)
              ~2 hr weekly review            │
                       │                     ▼
                       ▼                 fail-closed rules ──► RED: digest names first failing step
              hand-converted PDF             │ GREEN
                                             ▼
A quiet night and a broken               sheet write ──► receipt: counts the API RETURNED
night look the same                          │
                                             ▼
                                         weekly: metrics + bands, diagnostic order, coaching gate
                                             │
                                             ▼
                                         self-contained HTML ──► post-upload byte check (>5% off = STOP)
                                             │
                                             ▼
                                         PDF conversion in the destination account
```
**As-is:** activity compiled by hand from two systems into a spreadsheet; a weekly review of
about two hours; the report hand-converted to PDF; a quiet night and a broken
night indistinguishable.

## 3. What I built
Two scheduled jobs and a delivery path. The nightly job pulls the previous day's activity,
aggregates it per caller, writes the artifact, decides its own exit code from explicit fail-closed
rules, writes to the shared sheet and leaves a receipt of what the sheet API returned. The weekly
job colors every metric against bands, walks Data → Phone Health → Systems → Performance and stops
at the first category that worsened, writes one plain-language "why it moved" line, renders
self-contained HTML and verifies the bytes that arrived. A scheduled script in the destination
account converts it to PDF.

## 4. Measured results
- **measured** — The activity API refuses any query over 10,000 results. Bulk automation tripped
  it on 2026-08-29 (**51,077** candidates) and 2026-08-31 (**9,671**). Splitting the day into two
  unioned queries recovered **+7,712** records on 08-29 and **+1,099** on 08-31 that a single
  narrower query silently dropped.
- **measured** — The nightly job had **never once succeeded** until 2026-09-09. Root cause: a
  spreadsheet API 503 with no retry. It was found by the fleet watchdog
  ([Ops Heartbeat](ops-heartbeat.md)), not by the pipeline itself. A same-day redo of 2026-09-08
  by hand dispatch verified live.
- **measured** — The hand PDF-conversion step is gone. The managers get a PDF beside every report.
- **recollection** — The weekly review that took about two hours now takes minutes.
- **not claimed** — No broader hours-saved figure exists for the daily pull. Nobody logged the
  time the hand compilation took before it, so I do not estimate it.
- **open item at the time** — A monthly rollup tab had its own tripwire, and the tripwire did not
  fire while its condition held (the tab summed to zero for months the daily log had data for).
  Found; listed as open, not fixed, in the handoff that recorded it.
- **measured** — A diagnosis, a change, and a metric that moved. In late August the weekly report
  showed leads at 2–3 a week against 19 in June, with dials per record up from 2.3 to 4.2 while
  records worked per day fell from 47 to 24. The first response, a primary-contact-first dialing rule issued
  2026-08-29, changed nothing: dials per prospect went 8.8 → 7.5 → 7.8 over the next two weeks. The
  diagnostic walk on 9/10 found why. Slow records carried several times the dials of fast ones, and records per
  day correlated −0.74 with dials per record: effort was pooling on records that were not going to
  convert. The rule was re-issued on 2026-09-14 with enforcement (a "primary numbers exhausted"
  status added to the tracker, so a record leaves the main campaign instead of absorbing more dials). In the like-for-like campaign, dials per prospect fell from 10.2 to 2.4 on first
  contact and from 6.2 to 3.1 on follow-up, and conversations per dial held (3.8% → 4.1%). Prospects
  worked went from 133 to 433 the following week. Caveat carried: a new call list started the same
  week, so the account-wide number mixes two changes; the campaign-level figures do not.
- **finding** (synthetic) — In the bundled fixture, the over-cap day is 2026-09-01 and the union
  recovers every text-born lead a dialed-only query would drop; the test asserts it, the number
  itself is fixture-dependent and not a production claim.

## 5. Design decisions and tradeoffs
- **Direct PDF, or HTML then PDF?** HTML, converted afterward. The managed file connector
  corrupted the original PDFs in transit (they were about 160 KB); a self-contained HTML report of
  about 30 KB rides it reliably, and a scheduled script in the destination account does the
  HTML→document→PDF step. The managers still get a PDF; nobody makes it by hand.
- **Cell fills or foreground glyphs for the stoplight?** Glyphs. The conversion on the delivery
  path strips background colors and keeps foreground text color, so a colored ● survives into the
  PDF and a filled cell does not. This was found by looking at the delivered file, not by reading
  documentation.
- **One narrower query or two unioned queries for an over-cap day?** Two. The obvious narrower
  query ("records the dialer touched") drops exactly the outcomes the report exists to count: a
  lead born from a text reply or an inbound call flips status with zero dials. The union is still
  not a complete read (a record whose only activity was an inbound touch with no status change is
  outside both halves), so the day carries a flag.
- **Trust the write call or demand a receipt?** Receipt. The receipt holds what the API answered,
  and it is deleted at the start of every run because the run folder is keyed on the date and a
  same-day redo lands in the same folder; an inherited receipt would report a landed day for an
  attempt that never reached the write.
- **Zero or blank for a metric nobody could measure?** Blank with a flag. A zero is a number and
  gets colored red; a blank says "not measured," which is a different fact and leads to a
  different action (fix the store, not the caller).
- **Diagnose the person or the funnel first?** The funnel. The order Data → Phone Health → Systems
  → Performance is a management rule encoded as a gate, and it stops a bad list week from becoming
  a coaching conversation. The one carve-out, the metric that is input-clean by construction,
  exists so the gate is not a permanent excuse either.

## 6. What went wrong
During rollout, the fleet watchdog ([Ops Heartbeat](ops-heartbeat.md)) reported that the nightly
job had never once succeeded; the job's own signal, a spreadsheet API 503 with no retry, had looked
like a quiet night from the inside. The same-day response was a hand-dispatched redo of 2026-09-08,
verified live, and the retry ladder that already existed in sibling jobs. Two rules came out of it.
Never re-run a scheduled KPI run: it recomputes its target day from today's clock, so a "Re-run" of
last night's job measures the wrong day and overwrites the right one; a redo is a hand dispatch with
the day named explicitly. And never diagnose from absence at slot time: the 2026-09-08 night fired
about five hours late, measured the correct day anyway, and then died on the 503. The outage was
the missing retry.

The same watchdog is what surfaced the monthly rollup tab's tripwire, which had not fired while its
condition held (**measured**, listed as open in the handoff that recorded it). This pipeline's
health is now judged from outside it as well as from within.

## 7. How I validated it
Every rule is a named test, including the three that fail the run on purpose and the one that
proves a stale receipt cannot survive into a new run. The glyph-versus-fill decision was validated
by opening the delivered file. The 2026-09-08 redo was verified live against the sheet. The demo
runs eight synthetic weeks, including one over-cap day and one stale-store day, and prints three
different answers to "whose problem is it."

## 8. Technical implementation
Stdlib only in the reference, no network. The nightly runner pulls per day against an activity API
with a 10,000-result cap, splitting an over-cap day into two unioned queries (records dialed ∪
records whose status changed) and flagging it `narrowed`. `daily.json` is written before the exit
code is decided. The delivery receipt is deleted at the very start of every run and written
atomically after the sheet write succeeds, holding the counts the API returned. The weekly renderer
colors metrics with foreground glyphs, walks the diagnostic order, and compares the remote byte
count to the local one after upload.

## 9. Requirements and acceptance criteria
**Core**
- A metric nobody could measure renders blank with a flag, never as a red zero.
- The nightly run fails closed: zero dials on a measured weekday, a failed read, or an almost entirely empty scan is RED, and the failure digest names the first thing that broke.
- A day is "in the sheet" only when a receipt holds what the sheet API actually returned.
- Nobody is coached until the list, the phones and the setup have been ruled out.
- A truncated report cannot reach the managers: the delivered bytes are checked against the local ones.

<details><summary>Implementation-level rules</summary>

Each line is a named test in [`tests/test_kpi_engine.py`](../tests/test_kpi_engine.py).
- An unmeasured metric renders blank with a flag, never a red zero. A store that is merely stale
  is tagged `store_stale`, not failed: nobody measured those days, but nothing broke.
- The full data artifact is written **before** the exit code is decided, so a RED run still leaves
  something to look at.
- Fail-closed: every measured weekday at zero dials is RED (far more often an expired token or a
  silently ignored filter than a week off); an activity-log read that fails after one retry is
  RED; more than 90% empty of at least 50 scanned logs is RED (most records genuinely have no
  activity, so empties alone are not an alarm, but an almost entirely empty scan is an outage).
- The delivery receipt holds the counts the sheet write **returned**, not the rows attempted; it is
  written atomically after the write succeeds and deleted at the very start of every run, before
  anything that can raise.
- An over-cap day splits into two unioned queries (records dialed ∪ records whose status changed)
  and is flagged `narrowed`, so a partial read is never mistaken for a complete one.
- The failure digest names the **first** failing step, never a downstream "skipped".
- Diagnosis walks Data, Phone Health, Systems, Performance and stops at the first category that
  worsened. Nobody is coached until the first three are clear, with one carve-out: the metric that
  is input-clean by construction (correct-party conversations per lead) is judged regardless,
  because wrong numbers, dead numbers and no-answers all happen before a correct conversation
  exists and cannot move it.
- Stoplight color is a foreground-colored glyph, never a background fill.
- After upload, the remote byte count is compared to the local one; more than 5% apart is a hard
  stop, not a warning.
- A redo of a missed night is a hand dispatch with the day named explicitly, never a "Re-run" of the scheduled job.
</details>

## 10. Demo
`python -m kpi_engine.demo` runs eight weeks of synthetic weekdays through the nightly runner
(one over-cap day, one stale-store day), then renders the last week's stoplight and the three most
recent diagnostic walks, and writes the HTML report to `.tmp/kpi_report.html`. What it printed:
```
KPI weekly stoplight, week of 2026-09-14  (synthetic data, illustrative bands)
nightly runs: exit 0, receipt {'updated': 0, 'appended': 40}
flags: narrowed=2026-09-01, store_stale=2026-09-10
metric                            team          Caller A          Caller B
Dials                             4130              2066              2064
Correct # / Dials               G 0.08            G 0.08            G 0.08
Wrong # / Connects              G 0.18            G 0.18            G 0.18
Connects / Dials                G 0.13            G 0.13            G 0.13
No Answer / Dials               Y 0.87            Y 0.87            Y 0.87
Leads                               44                28                16
Correct # / Leads               G 7.52            G 6.07           Y 10.06
Connects / Leads               G 12.27            G 9.82           G 16.56
Text replies                        15                 8                 7
week of 2026-08-31: first worsened = Data          coaching gate CLOSED  (Correct # / Dials 0.08 -> 0.03 (team))
week of 2026-09-07: first worsened = Phone Health  coaching gate CLOSED  (Connects / Dials 0.12 -> 0.07 (team))
week of 2026-09-14: first worsened = Performance   coaching gate open  (Correct # / Leads 3.95 -> 6.07 (Caller A))
this week's per-caller verdicts: {'Caller A': {'opp_to_lead': 'GREEN', 'connects_to_lead': 'GREEN'}, 'Caller B': {'opp_to_lead': 'YELLOW', 'connects_to_lead': 'GREEN'}}
why it moved: Leads up 19% vs last week (37 -> 44). First thing that worsened: Performance (Correct # / Leads 3.95 -> 6.07 (Caller A)), so look at closing first.
digest example: KPI run FAILED at fetch. No numbers tonight.
html report: .tmp/kpi_report.html (2833 bytes, post-upload check passed)
```
Three weeks, three different answers to "whose problem is it": the list (gate closed), the phones
(gate closed), then a clean funnel where a per-person read is fair (**finding**, synthetic data).

## 11. Built with
Designed, directed, tested and operated by me. Implemented with Claude Code. The underlying
activity pull against the dialer's API is a third-party script, not reproduced here; the spreadsheet
layer, retry ladder, stoplight diagnosis, delivery receipt, cap-split union and the HTML-to-PDF
delivery pipeline are mine. Bands, volumes, callers and campaigns in this repo are synthetic and
illustrative; the real figures are private.
