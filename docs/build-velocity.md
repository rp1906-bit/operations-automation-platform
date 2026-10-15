# Build velocity — why builds took two to three weeks

**Pattern:** root-cause investigation where the data refuted the theory. **Code:** none in this
repo; the measurements were ad-hoc queries against the production repo's pull-request and commit
history and the session transcripts. This page is the investigation record.

## In 30 seconds
- **Problem:** work a small team "puts out in a day or two" was taking my team, working with AI tools, two to three calendar weeks, and my theory was that we reviewed too much.
- **Solution:** three independent agents measured the same question from PR history, commit logs, workstream calendars and session transcripts, and had to converge before anything counted as a finding.
- **Strongest measured fact:** review was **6% of calendar days**; the real problem was **34 open PRs, median age 36 days**, and **55%** of workstream-days idle (**measured**).
- **Key lesson:** I was optimizing the wrong thing; nothing was changed until the findings were written up as a diagnosis.

## 1. The question
Work that a small team "puts out in a day or two" was taking my team, working with AI tools, two to three
calendar weeks. Why?

## 2. The theory going in
Mine: **we review too much.** Every code change went through review passes, and I remembered one
change taking five of them. Cut the review and the calendar would shrink.

## 3. Data gathered
Three independent agents measured the same question from different angles on 2026-09-09 and
converged. Sources, all internal metadata, no customer data:
- **Open pull requests:** the platform's own PR listing (`gh pr list --state open`), for count,
  age, and whether each carried code.
- **Merged commits:** the last 60 days of the main branch log, classified by whether the commit
  touched code or only documentation.
- **Workstream calendars:** open and close dates for closed workstreams, and agent working hours
  per workstream from the session records.
- **Session records:** for review-round counts per change.
- **One natural experiment:** a session that shipped six commits with the review gate accidentally
  suppressed, and the session that followed it.

## 4. What the data showed
- **measured** — Review was **6% of calendar days** and **11–16% of agent working hours**. The
  median code PR got **one** review round; **9%** went to four or more. The "five passes" change I
  remembered took **40 minutes** end to end. Cutting review entirely would recover about **3.5 hours**
  on a three-week build.
- **measured** — Building was not slow either: **6–37 agent-hours** per build; the median closed
  workstream (**n=46**) closed in **3 calendar days**. One workstream took 12.5 agent-hours across
  7 calendar days, so working time was 7% of elapsed time.
- **measured** — The real problem was work sitting idle. **34 open PRs, median age 36 days, oldest
  93 days**, and only **15** of them carried code. **55%** of workstream-days had nothing happening
  (not blocked; idle). Working-to-waiting ratio **33:67**.
- **measured** — **62–65% of merged PRs contained no code**: the same decision restated across a
  handoff, a working log, and an instructions file. In the 60-day commit log, **398 of 804** commits
  were documentation-only.
- **measured** — The natural experiment: the unreviewed session's six commits were followed by a
  session that found three real defects in them. Skipping review moved the work later; it did not
  save it.

## 5. The refuted theory, stated plainly
We did not review too much. Review was a small, fixed cost that also caught real defects. The
calendar was long because work waited.

## 6. What was changed
The investigation was framed as a diagnosis, not a change; nothing was implemented without sign-off.
Recommendations recorded for decision: triage the 34 open PRs; risk-tier review (heavy path only
for money, CRM and deploy changes, light path for docs); cap work in progress at 3 active
workstreams instead of 14; collapse the per-session documentation ritual.

Two fixes did ship directly from what the measurement surfaced, because it exposed monitoring gaps
while counting: [Ops Heartbeat](ops-heartbeat.md) (one weekly job had zero runs in its life; one
nightly job had never succeeded) and a ledger fix for the recently-sold sweep (**measured**).
Four smaller monitoring defects surfaced the same way were verified, written up and parked for decision.

## 7. Method notes
- **Review share** was computed two ways, as a share of calendar days and as a share of agent
  working hours, then cross-checked against the distribution of review rounds per PR and one
  worked example. Time-share, round-count, and the example had to agree before "review is the
  problem" was rejected.
- **PR age** came from the open-PR listing: count, median days since opened, oldest.
- **Documentation-only merges** came from classifying the last 60 days of commits on the main
  branch by prefix and by whether they touched code.
- **Idle time:** the sources record the result (55% of workstream-days idle; working:waiting 33:67)
  and that it was derived from workstream open/close dates against agent working hours, but do
  not preserve the exact day-classification rule.
- **Convergence** was the check on all of it: three agents measured independently and their
  numbers agreed before anything was written down as a finding.
- **Not claimed:** any hours-saved or calendar-days-saved figure from the recommendations. None
  had been executed as of the diagnosis.

## 8. Built with
Designed, directed and measured by me. Measurement runs were carried out by Claude Code agents,
with independent agents assigned to refute each finding before it was accepted.
