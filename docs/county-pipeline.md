# County records pipeline — replacing a manual data pull with a controlled automation

A scheduled job that watches a county's public-records portal for changed lien filings, reads each
document image, normalizes and de-duplicates the rows, checks them against a second source, and
writes clean rows to the sheet the team works from. At each point where it can be wrong, it is built
to stop and hand the row to a person instead of guessing.

Reference implementation of a system I run in production · 16 automated tests
· demo: `python -m county_pipeline.demo` · code: [`county_pipeline/`](../county_pipeline/) (synthetic data)
· tests: [`tests/test_county_pipeline.py`](../tests/test_county_pipeline.py)

> Production code is private. The incidents, numbers and decisions below are from the real system,
> with names, IDs and business specifics replaced ("Marlow County" is fictional; the watch-list here
> is "lien filings" only).

## In 30 seconds
- **Problem:** pulling county filings by hand was a 6 to 8 hour shift for two counties, with human errors: a misread document, the wrong parcel when two owners share a name, a skipped filing.
- **Solution:** a cloud job that fetches only what changed, reads documents with a vision model, de-duplicates, enriches, and routes anything unsettled to a review queue with a reason; a watchdog outside the job; a CRM bridge under a parallel-run cutover.
- **Strongest measured fact:** cloud build 0.1.12, 2026-04-16: **250 records in 55 seconds, zero failures** (**measured**); the review queue is now about 15 minutes a day (**recollection**).
- **Key lesson:** silence and a quiet day look identical from inside the job, so the watcher lives outside it; and the first root cause is a hypothesis to test against live data.

## 1. Business problem
Before this existed, I pulled the records myself: log in to each county's portal, page through the
day's filings, open each document, type the owner, address and date into a sheet, then look each one
up in the appraisal records to confirm the parcel. For two counties that was a **6 to 8 hour manual
shift, now about 15 minutes of checking the review queue**, by my own account of the work (no
timesheet exists). The directive I wrote for the build set a deliberately conservative planning
target, written before the build and lower than my recollection of the actual shift: **replace 2 to
4 hours of daily manual pulling with runs needing under 10 minutes of human oversight**. Speed is the
requirement: a filing found the morning it posts is actionable; the same filing found a week later
is stale.

The people affected: me, and the two team members who work those rows and who used to
wait on the sheet filling up by hand.

## 2. As-is → to-be
```
BEFORE                                     AFTER
Person logs in to portal                   Scheduled cloud job (hosted crawler)
  │ pages through the day's filings           │ fetches only what changed since last run
  │ opens each document, types rows           ▼
  │ cross-checks each parcel by hand       Vision reader ──► unreadable? fail loudly
  │ uploads rows to CRM by hand               │
  ▼                                           ▼
Typos, skipped documents;               Key + de-dupe ──► second-source enrichment
a slow day and a skipped day                  │              (ambiguous? blank + flag)
look the same                                 ▼
                                        Settled rows ──► sheet     Unsettled ──► review queue (+ reason)
                                              │                    fetched = dupes + written + queued
                                              ▼
                                        CRM bridge (one writer per tab, stack don't skip)
                                        parallel with the manual upload until 5 clean weekdays
                                              ▲
                                        Watchdog outside the job: ok / stale / unreachable
```

## 3. What I built
A legacy-portal automation with human takeover at every seam (the *automate* stage: redesign the work).
On top of the fetch, read, de-dupe, enrich and review-queue path above, a circuit breaker aborts a
batch that is mostly wrong, a quiet-streak counter separates a quiet county from a stale scraper, and
a watchdog outside the job checks that it reported in. A CRM bridge then writes the day's clean rows
under a one-writer-per-tab rule while the manual upload keeps running until five clean weekdays
prove it out.

## 4. Measured results
- **recollection** — Two counties by hand: a 6 to 8 hour shift, now about 15 minutes. **goal** — the directive's stated target: 2 to 4 hours daily down to under 10 minutes of oversight. No measured hours-saved figure exists beyond these two.
- **measured** — Cloud build 0.1.12, 2026-04-16: **250 records in 55 seconds, zero failures**, the run that validated the hosted-crawler deployment.
- **measured** — 2026-04-22: extraction succeeded on 2 of 19 documents; with no address to compare, enrichment chose a parcel by owner name and wrote the wrong parcel's data onto a row. This is the incident behind the blank-and-flag invariant (and behind the separate QA Verifier, `docs/qa-verifier.md`).
- **measured** — 2026-04-28: the portal's own index lagged four days behind the calendar, so a date window anchored on "today" scraped an empty range and reported a failure. That produced the probe: fetch the portal's latest recorded date first, then search from there.
- **measured** — 2026-06-21: the probe was proving the portal healthy while zero-row searches still paged the team red on three quiet filing days in a row. A naive "probe horizon vs last written" detector was proposed and rejected after live data showed it would false-fire (the probe counts documents the search correctly filters out). What shipped: a benign quiet-day status, plus the per-source quiet-streak counter that escalates at a threshold (10 in production and in the reference code) so a slow-onset block cannot hide behind quiet days forever.
- **measured** — From 2026-08-20 the scheduled run for one county **timed out on seven consecutive weekdays and nothing said a word**: the host killed the container, so the job's own catch-all notifier never ran and no summary was written. A person noticed a week later and started hand-typing rows. That is what the watchdog exists for.
- **measured** — The outage's fix was wrong the first time. The first theory was an append race between two writers, with an atomic append as the fix. Before it shipped, every logged write was reconciled row-for-row against the live tab: none lost, none duplicated, and the cited duplicate rows were from a different county and from days the job wrote nothing. The append fix was itself shown to be dangerous (the tab had thousands of blank rows above the region the sheet is read from, so a naive append would land far from where anyone looks). The real cause, found by measurement, was a silent write clobber; the fix withholds the affected rows' dedupe keys and downgrades the run to `partial` with named fields the watchdog renders. Merged in a PR with **5,942 tests passing**. Review then caught the fix disarming itself: a dropped length guard meant a single ragged row raised an error the block's own `except` swallowed, so a destroyed row produced a green card. Two live reproductions confirmed it before the corrected fix shipped.
- **measured** — CRM cutover, day one (2026-09-09): **all 17 creates returned HTTP 403; 25 stacks landed** and were verified by read-back. Root cause: the vendor closed the legacy top-level route the bridge was calling. The evidence: 403 on GET *and* POST, on both credentials; unauthenticated returned 401 (so we were authenticated, then denied); the 403 carried an `Allow` header naming the methods (the view exists, its permission class refuses); 5 of 5 reproducible; and the create call was byte-for-byte unchanged since it had provably worked on 8/28 and 8/29. An independent refuter could not break the diagnosis. The fix is a one-line route change to the vendor's preferred path. It was **not coded that day**: the ruling was findings first, code after approval. The manual upload continued as the safety net, and day one does not count toward the five-day clock.
- **status** — The CRM cutover is in progress.

## 5. Design decisions and tradeoffs
- **Cloud actor or local cron?** Cloud. A scheduled job on my machine stops when the machine sleeps, and the portal only served the job reliably once it ran on a hosted crawler. The cost: the host kills a run at its timeout with no error, no summary and no message, which is exactly what happened in August (§6). The answer was not to come home to cron but to add a watchdog that runs somewhere the job isn't and asks whether a run summary exists, not whether the run "succeeded".
- **Vision model or template OCR?** Vision. County documents are scanned, skewed and inconsistent, and a template breaks on the next clerk. The vision reader lands most documents; the ones it cannot read must fail loudly, because the founding incident (a run that read 2 of 19 documents and still wrote rows) came from a quiet failure here. The reference reader is deterministic and offline; the real one is a paid API behind a per-run cost cap.
- **Blank-and-flag or best-guess?** Blank and flag. With no document address, a search by owner name that returns several parcels has nothing to score against; picking the "best" one is how the wrong parcel's mailing address, square footage and value were written onto a real row on 2026-04-22. The rule is an invariant: several hits and no address means no pick, a blank enrichment, an audit note, a red row, and exclusion from the CRM upload until a person clears it. A blank cell with a red row costs a person thirty seconds; a confidently wrong parcel costs a phone call to the wrong owner and trust in every other row.
- **Abort or continue?** Abort at about nine in ten wrong. A batch that bad is a broken source (the second source down, the reader failing), not nine bad filings, and writing it would poison the sheet and commit the dedupe keys so the batch could never be retried. Below the threshold, wrong rows go to the queue and the good rows ship.
- **CRM cutover: parallel run or big-bang?** Parallel. A team member kept doing the daily upload while the bridge ran the same tab in commit mode, and the tab only cuts over after five weekdays whose card shows *created* only on addresses the CRM did not have and *stacked* on every address it did. Big-bang would have meant that on 2026-09-09, when every create failed (§6), the day's leads would simply not have reached the CRM.
- **Skip a record the CRM already has?** No: stack it. Two of the first three live rows the bridge met were already in the CRM from other lists, and skipping them lost the one thing a second signal on a known address is for (**measured**, first live sweep, 2026-08-28). Stacking adds the list and note onto the existing record and never overwrites a field, which also closes the last-write-wins race between two writers.

## 6. What went wrong
Three episodes, each caught by a control and fixed the same cycle.

**August timeout.** From 2026-08-20 the host killed one county's run at its timeout on seven
weekdays running, before the job's own notifier could fire. A team member surfaced it and covered
by hand. The fix is a watchdog outside the job that checks for a fresh run summary, with "cannot
tell" as its own state. It now reports every morning.

**The data bug behind it.** The first theory, an append race, did not survive row-for-row
reconciliation against the live tab. The real cause was a silent write clobber. Code review caught
the first fix disarming itself, and the corrected fix shipped after two live reproductions.

**CRM cutover, day one.** All 17 creates returned 403 while 25 stacks landed. Because the cutover
was a parallel run, the manual upload was still going and nobody lost a lead. The 401-vs-403 and
GET-vs-POST comparisons, an unchanged create call, and an independent refuter established a
vendor-side route closure before any code changed. The one-line fix was presented for approval.

## 7. How I validated it
Every rule is a named test, and the demo runs the whole path on synthetic data in one command.
The quiet-day detector was checked against live data before it shipped, which rejected the naive
version. The August fix was validated by row-for-row reconciliation against the live sheet and two
live reproductions of the guard defect. The day-one CRM diagnosis was handed to an independent
refuter, who could not break it. Every daily card reconciles: fetched = duplicates + written + queued.

## 8. Technical implementation
A scheduled cloud job on a hosted crawler. It probes the portal's latest
recorded date first, then fetches only records changed since the last run, keyed by a normalized
composite key. A vision reader extracts each document (reference: deterministic and offline; real:
a paid API under a per-run cost cap). The dedupe manifest is committed only when a batch writes. A
watchdog scheduled elsewhere reports ok / stale / unreachable. The CRM bridge enforces one writer
per tab, stacks on existing records, and computes acceptance from the run summary alone. Thresholds
in the reference code (nine in ten, a quiet streak of ten, five weekdays) match the production
directive.

## 9. Requirements and acceptance criteria
**Core**
- Fetch only what changed; nothing is re-pulled, and every fetched record is accounted for as a duplicate, a written row, or a queued row.
- The pipeline never guesses: an unreadable document takes an address only from a single unambiguous second-source match, with a note; an ambiguous match produces a flagged, blank row for a person, not an invented value.
- A batch that is mostly wrong is aborted and stays retry-eligible rather than written.
- A quiet county is distinguished from a stale scraper, and the watchdog reports from outside the job.
- CRM cutover is earned: one writer per tab, existing records stacked never overwritten, and five clean weekdays in a row before the manual upload stops.

<details><summary>Implementation-level rules</summary>

Each line is a named test in `tests/test_county_pipeline.py`.

County pipeline
- Fetch only what changed: unchanged records are never re-fetched (`test_fetch_only_what_changed`).
- Dedupe on the composite key address + filing date + owner, normalized for case and spacing, across runs and within a batch (`test_dedupe_on_address_date_owner_composite_key`).
- An ambiguous second-source match (several parcels for the owner, no document address to compare) blanks the enrichment and flags the row; a single hit is unambiguous and fills (`test_ambiguous_second_source_match_blanks_and_flags_never_guesses`).
- An unreadable document fails extraction rather than inventing an address (`test_unreadable_document_fails_extraction_instead_of_inventing_an_address`).
- Incomplete rows route to the review queue; fetched = duplicates + written + queued, always (`test_incomplete_records_route_to_review_queue_nothing_dropped`).
- The circuit breaker aborts the batch at nine in ten rows wrong and continues at eight in ten (`test_circuit_breaker_aborts_at_nine_in_ten`, `test_circuit_breaker_continues_below_nine_in_ten`); an aborted batch writes nothing and leaves the dedupe manifest uncommitted so it is retry-eligible (`test_aborted_batch_writes_nothing_and_leaves_manifest_uncommitted`).
- Zero rows is benign only when a probe proved the portal reachable; a quiet streak escalates at the threshold and any real row resets it (`test_quiet_streak_separates_quiet_county_from_stale_scraper`).
- The watch-list is generic: lien filings only (`test_generic_watch_list_only`).

CRM delivery stage
- One writer per tab; a second writer to the same tab is refused (`test_one_writer_per_tab`).
- A record already in the CRM is stacked (new list and note added onto the existing record), never skipped, never duplicated, never overwritten (`test_stack_dont_skip_for_records_already_present`).
- Acceptance is computed from the run summary alone, so the Slack card is enough to check a day (`test_acceptance_criteria_checkable_from_the_summary_alone`).
- Five accepted weekdays in a row before cutover; a weekend does not count and a failed day restarts the clock (`test_five_weekday_parallel_run_before_cutover`).
- A denial is read as evidence: 401 without credentials plus 403 with them, on GET and POST alike, with our code unchanged, is a vendor-side route closure (`test_403_diagnosis_reads_401_vs_403_and_get_vs_post_as_evidence`).

Note: the reference dedupes on address + filing date + owner; production keys on the county's own document number plus grantee plus county. The reference key is the generic form of the same rule.
</details>

## 10. Demo
`python -m county_pipeline.demo` runs three synthetic days, a quiet streak, and the delivery stage (output abridged).
Everything below is a **finding** on synthetic data.
```
2026-06-01  ok: fetched 21, duplicates 2, written 17, review queue 2
    review  MC-00418 MARGO DELACROIX    (no address)   ambiguous: 2 parcels for MARGO DELACROIX, no document address to disambiguate
    review  MC-00419 EPHRAIM NOBODY     (no address)   no document address and owner not found in second source
    written MC-00417 TOBIAS RENNICK     61 IRONWOOD REACH extraction failed: document unreadable; address taken from second source
2026-06-02  quiet_day: fetched 0, duplicates 0, written 0, review queue 0, quiet streak 1
2026-06-03  ABORTED — 90% of 10 new rows look wrong; nothing written, manifest not committed, batch stays retry-eligible

portal served 32 records across the three fetches (unchanged records were not re-fetched)

QUIET STREAK — eleven quiet days in a row
  day  1: streak  1 -> quiet_day
  day 10: streak 10 -> quiet_streak_stale

CRM DELIVERY — day-one rows, one writer per tab, stack don't skip
Marlow Lien — 14 created, 2 stacked, 1 unconfirmed (writer: bridge)
  stacked   9 HERON TERRACE  on crm-0001
  unconfirmed 7 FENNEL WALK  (near-miss spelling of '7 Fennell Walk', left to a human)
acceptance from the card alone: PASS
second writer refused: tab 'Marlow Lien' is written by 'bridge'; 'team-member-upload' refused
DENIAL DIAGNOSIS — vendor-side route closure: authenticated caller denied on GET and POST; our code unchanged
```
Day one shows the three ways a document can be unreadable: one owner resolves to a single parcel
and is written with a note; one owner has two parcels and is blanked and flagged; one owner is not
in the second source at all. Day three is the breaker: nine of ten new rows wrong, nothing written.

## 11. Built with
Designed, directed, tested and operated by me. Implemented with Claude Code. The CRM bridge, the
one-writer-per-tab law and the parallel-run cutover design are mine; the CRM's API is the vendor's.
Thresholds in the reference code match the production directive; everything else here is synthetic.
