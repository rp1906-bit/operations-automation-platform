# QA Verifier — output verification with human-in-the-loop repair

A separate checker that grades every scheduled data pull by the rows it wrote, not the exit code it
returned, names the likely cause with a confidence level, and decides whether a pre-vetted repair is
eligible under defined safety gates; everything else it drafts for approval or escalates to a person. Since
2026-09-28 it grades every registered pipeline on the platform in one card a day.

Reference implementation of a system I run in production · 27 automated tests
· demo: `python -m qa_verifier.demo` · code: [`qa_verifier/`](../qa_verifier/) (synthetic data)
· tests: [`tests/test_qa_verifier.py`](../tests/test_qa_verifier.py)

> Production code is private. The incidents, numbers and decisions below are from the real system,
> with names, IDs and business specifics replaced ("Marlow County" is fictional).

## In 30 seconds
- **Problem:** a weekday county-records pull exited "succeeded" while writing the wrong parcel's data to 15 rows; the only signal was the exit code.
- **Solution:** an independent checker that grades every run's output against explicit thresholds, names the cause, and picks a lane (fix / draft / escalate) under hard circuit breakers, leaving one card and one ledger row per run.
- **Strongest measured fact:** 37 unattended production runs from 2026-06-04 to 2026-09-02, every one a ledger row; auto-fixes executed: zero, by design (**measured**). Extended platform-wide 2026-09-26: one card every weekday grading every registered pipeline (two county pulls, a weekly resale sweep, and the liveness of all nine scheduled jobs), test count 81 → 389, and its first live run exposed 64 rows a person had deleted from the sheet (**measured**).
- **Key lesson:** the checker's first job is to be trustworthy about its own failures, so a fault in its own environment is its own message class, never a "FAILED" about a healthy scrape. The platform-wide version makes that a fourth verdict, grey "UNVERIFIED": never a false RED, never a default.

## 1. Business problem
Every weekday a scraper pulls county filing records into a spreadsheet the team works from. On
2026-04-22 it exited with code 0, "succeeded", while writing the wrong parcel's data to **15 rows**:
document-image extraction had worked on only **2 of 19** documents, and with no address to match on,
the enrichment step picked an arbitrary parcel by owner name (from the incident note in the
production directive). The people affected: me, and the two team members who work those rows.

## 2. As-is → to-be
```
BEFORE                                      AFTER
Scrape ──► exit code 0 ──► "the data       Scrape ──► log + rows written
           is right"                             │
                                                 ▼   (about fifteen minutes later)
Wrong rows found by hand,                   Independent QA Verifier
if at all                                        │  grades 4 dimensions vs thresholds
                                                 │  names root cause + confidence
                                                 ▼
                                      GREEN / YELLOW / UNVERIFIED / RED verdict
                                                 │
                              ┌──────────────────┼──────────────────┐
                              ▼                  ▼                  ▼
                        Band 1: fix        Band 2: draft      Band 3: escalate
                        (pre-vetted,       (diff, cost,       (evidence only)
                        2 clean reviews)   rollback; waits)
                              └──────────────────┼──────────────────┘
                                                 ▼
                              one plain-English card + one append-only ledger row
```
## 3. What I built
A human-in-the-loop approval workflow wired into the team's chat channel, with checkpointed approval
gates. The checker grades every run on four dimensions, worst dimension wins, and attaches a named
cause with a confidence level. Band 1 is repair-eligible only when a pre-vetted fix exists for that
exact signature and every gate holds (none has executed in production yet); Band 2 drafts (diff, rationale, cost, rollback) and waits; Band 3
posts evidence only. Circuit breakers sit above all three. Every run, greens included, becomes a
ledger row on a branch the bot owns, landed on `main` weekly. The reference code here stops at the
lane decision on purpose: it returns AUTO_EXECUTE, DRAFT or ESCALATE and runs no repair, because the
repair scripts are production code and stay private.

## 4. Measured results
- **measured** — 2026-04-22: exit code 0, 15 wrong rows, extraction succeeded on 2 of 19 documents. The founding incident; every threshold in the verdict model traces to it.
- **measured** — 37 runs from 2026-06-04 to 2026-09-02: **33 RED, 1 YELLOW, 2 RED→GREEN recoveries, 1 RED→draft.**
- **measured** — About 33 of those 37 REDs were the checker's own environment (a missing dependency in its cloud runtime surfacing as "metric source missing"), not the scrape. This is why "checker fault" is its own message class.
- **measured** — A separate bug: those 37 runs' notes were each committed to a throwaway branch nobody merged, so run history was invisible and a stale handoff called the agent dormant when it was not. Fixed 2026-09-03 with a durable ledger branch plus a weekly PR to `main`.
- **measured** — 2026-09-03: adding a second county exposed a silently disabled detection stratum. The second county's ambiguous-match log line and audit note were worded differently, the matcher recognised neither, and one metric was structurally zero for that county. Found by reading the matcher, not by an alert; fixed the same day and pinned by a test.
- **measured** — Auto-fixes executed in production: **zero** (verified 2026-09-21 against the ledger: about 19 escalations, 3 drafts, 0 auto-executes). One Band 1 path has been open since 2026-05-13 for a fault that has not recurred. The signature that does recur went to draft because its pre-vetted repair script did not exist; that script was merged 2026-09-21 and is armed. Until it fires, the claim is exactly: *repairs the safest class of problem itself once a pre-vetted fix exists for it; everything else it diagnoses, drafts and hands to a human.* Not "self-healing".
- **measured** — Platform-wide since 2026-09-26 (first daily card 2026-09-28). One card per weekday grades every registered pipeline: two county pulls, the weekly resale sweep, and whether each of the nine scheduled jobs actually ran. A fourth verdict, grey UNVERIFIED, means the checker itself could not check; it is never scored as a failure and never defaults to green. Grading is code; the AI runs only the pre-vetted fix path. Tests went from 81 to 389. Two $0 dry runs against the live services wrote nothing before the switch.
- **measured** — First live proof, 2026-09-26: the new grader raised two false REDs. Fixing them uncovered a real event. 64 rows from the two prior days (60 in one county, 4 in the other) had been removed from the sheet by a human account overnight; the old checker had been reading the blank rows at their old positions as results. Rows are now located by pull date and county, a vanished block is a RED that says so, and blank rows are never scored. Whether the deletion was intended went to a person to decide.
- **measured** — Eleven weekday cards from 2026-09-28 to 2026-10-12, every one on the ledger. Two days were all green. The most common RED is the scheduled-jobs line, which is the fleet watchdog ([Ops Heartbeat](ops-heartbeat.md)) reporting from inside the same card.
- **not claimed** — No hours-saved figure exists for this system. Nobody was checking every run's rows by hand before it; the honest claim is that a green-with-bad-data run is now a red card the same morning.

### 22 April incident timeline (**measured** where dated; reconstructed from the directive's incident note)
1. Weekday scheduled scrape fires; the run finishes with `status=SUCCEEDED`, `exit_code=0`.
2. Document-image extraction had worked on 2 of 19 documents; 15 of the rows written had no usable property address.
3. With no address, enrichment searched by owner name alone and, where the name matched several parcels, took one.
4. Those 15 rows land on the sheet looking like any other row. No alert: the only signal was the exit code.
5. How and when the wrong rows were noticed is not in the logs I have; the directive records the incident and the rule it produced.
6. Outcome: a run-level health check with explicit thresholds on document-read rate, empty-address rate and ambiguous-owner count. Re-graded under those rules the run is RED (`tests/test_qa_verifier.py::test_exit_code_zero_with_wrong_output_grades_red`).

The 22 April shape, as the reference code renders it (**finding**, synthetic data):
```
🔴 Marlow County lien scraper — 2026-04-22 — FAILED
Rows came out with no property address, so owner lookups ran on the name alone and many came back
uncertain. Those rows may point at the wrong parcel.
Why: The document images are not yielding a readable property address.
Numbers: county document images we could read = 0.105; rows with no property address = 0.882;
rows flagged as an uncertain owner match = 15
Next: DRAFT — awaiting approval. A proposed fix (diff, cost, rollback) is attached; nothing ships
until a human says so. (no pre-vetted fix registered for ambiguous_empty_address)
```
Exit code 0, verdict RED, lane: draft. In production this is the signature that recurs.

## 5. Design decisions and tradeoffs
- **Trust the signature's own cost estimate?** No. The orchestrator recomputes it from primitive inputs (unit count × unit price) and a stale or wrong self-report over 20% off drops the fix to a draft.
- **One reviewer or two?** Two, independent, and "unable to review" counts as dissent.
- **Key the repeat-signature breaker on (pipeline, county, signature) or on signature alone?** Signature alone, across all pipelines. Two scrapes on the same infrastructure ten minutes apart hitting the same cause is the systemic case; narrower keying would let the same bug fire twice without a human ever looking.
- **Append the run notes to the shared working log?** No: a daily bot append to a file humans edit in parallel collides with every session. That collision is why the first 37 runs' notes never merged (§6). The ledger is its own file on its own branch, landed on `main` by a weekly PR.
- **Log GREEN runs?** Yes. A history without the greens cannot show a recovery.
- **One "FAILED" message for every RED?** No. When the checker's own environment breaks, the card says "CHECK INCOMPLETE — the scrape may be fine." A bare FAILED there teaches the team to distrust a healthy scrape.
- **Auto-fix granted per system or earned per signature?** Per signature, with the breakers above all of it.

## 6. What went wrong
Recovering the first 37 runs' notes on 2026-09-03 surfaced two problems, both fixed the same day.
The ledger rows showed that 33 of 37 REDs were the checker's own cloud runtime (a missing package),
each posted as "FAILED" about a scrape that was fine. Checker faults now have their own message
class, so absence of evidence stays RED but says *whose* evidence is missing. The notes had also been
landing on throwaway branches nobody merged, so they now go to a ledger file on a branch the bot
owns, landed on `main` by a weekly PR. In the same session, adding a second county, reading the
matcher against the new source's actual log wording showed one detection metric was structurally
zero for it. That was fixed the same day and pinned by a test.

Three ledger rows, as the reference code renders them (structure real, names synthetic):
```
## 2026-06-04 — lien_scraper — marlow — UNVERIFIED
- **Source run:** run-0604-a
- **Dimensions:** run_health=GREEN extraction=UNVERIFIED row_compliance=GREEN delivery=GREEN
- **RCA:** checker_environment_fault (LOW)
- **Band / action:** escalate — check incomplete — the checker could not read its evidence

## 2026-06-15 — lien_scraper — marlow — RED
- **Source run:** run-0615-a
- **Dimensions:** run_health=GREEN extraction=GREEN row_compliance=GREEN delivery=RED
- **RCA:** delivery_stalled (MEDIUM)
- **Band / action:** draft — MEDIUM confidence

## 2026-06-16 — lien_scraper — marlow — GREEN
- **Source run:** run-0616-a
- **Dimensions:** run_health=GREEN extraction=GREEN row_compliance=GREEN delivery=GREEN
- **RCA:** none
- **Band / action:** —
```
The first is the shape of most of the first 37 rows (checker fault, escalated; the original graded
these RED, the platform-wide version and this reference grade them UNVERIFIED). The second is a
draft awaiting approval. The second and third together are a RED→GREEN recovery, which is only
visible because GREEN rows are logged.

## 7. How I validated it
Every rule is a named test in `tests/test_qa_verifier.py`, including re-grading the 22 April run
to RED. The second-county detection gap is pinned by a test in the production repo; the reference
grades from structured counts rather than log wording, so that path is not reproduced here. The production claim was
verified against the ledger itself on 2026-09-21, not against the handoff describing it. The
platform-wide figures above were re-read from the daily ledger cards on 2026-10-12.

## 8. Technical implementation
Fires about fifteen minutes after each weekday scrape; reads the run's log and the rows it wrote;
grades four dimensions (run health, extraction, row compliance, delivery) against explicit
thresholds, worst wins. Signatures map to a plain-English glossary with a build-time coverage gate.
Lane selection recomputes cost from unit counts and prices, requires two independent reviewers for
Band 1, and sits under the breakers. The ledger is an append-only file on its own branch.

## 9. Requirements and acceptance criteria
**Core**
- A run that exits 0 with wrong output is graded RED; the verdict comes from the rows and the log, never the exit code.
- The checker repairs nothing on its own unless a pre-vetted fix exists for that exact cause and every safety gate holds; otherwise it drafts or escalates.
- A repeat of the same cause inside 24 hours always goes to a person.
- A fault in the checker's own environment is reported as "check incomplete," never as a scrape failure.
- Every run, including a healthy one, leaves a ledger row that nothing can rewrite.

<details><summary>Implementation-level rules</summary>

Each line is a named test in `tests/test_qa_verifier.py`.
- Exit code 0 with wrong output grades RED (the 22 April shape: extraction succeeded on a minority of documents).
- Verdict is worst-dimension-wins across GREEN / YELLOW / UNVERIFIED / RED. YELLOW posts and watches the next run; no fix.
- A fault inside the checker grades UNVERIFIED, never RED and never GREEN; it escalates to a person and is never a false failure about a healthy run.
- Band 1 (auto-execute) only when **all** hold: a pre-vetted fix is registered for the exact signature, it is idempotent, cost is under the cap, confidence is HIGH, and two independent reviewers both return "clean". Any dissent, timeout or "unable to review" escalates.
- The cost estimate is recomputed from unit counts and unit prices, never trusted from the signature; over 20% divergence downgrades the lane.
- Band 2 writes a draft (diff, rationale, cost, rollback) and waits. Band 3 posts evidence only.
- Breakers: the same signature twice in 24h forces human review, keyed on the signature alone across every pipeline; a cost cap; one run per pipeline with an expiring lock; a protected tab is never touched.
- A fault in the checker's own environment is a separate message class from a scrape failure.
- The ledger is append-only and records GREEN runs too.
- Glossary-coverage gate: a new signature without plain-English wording fails the build.
</details>

## 10. Demo
`python -m qa_verifier.demo` runs eight synthetic runs through the whole path and prints the cards
and the ledger, including the 22 April shape shown in §4 (exit code 0, verdict RED, lane: draft).

## 11. Built with
Designed, directed, tested and operated by me. Implemented with Claude Code. Cost, cap and unit
prices in the reference code are illustrative; the real figures are private.
