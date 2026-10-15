# Build vs. buy — evaluating an off-the-shelf platform against my own

**Pattern:** discovery-to-spec: a scoped technical decision with success metrics. **Code:** none in
this repo. This page is the decision record. The platform evaluated is an open-source operations
stack for the same industry, referred to here as "the candidate platform"; none of its code is in this repo.

## In 30 seconds
- **Problem:** an open-source platform appeared covering the same ground as mine, end to end; switching wholesale was tempting and undisciplined.
- **Solution:** score both codebases area by area from a code read, send only the genuine ties to a bake-off on a live deal, and let nothing of theirs touch production until it wins on evidence.
- **Strongest measured fact:** their comping engine returned zero comps on my market (no disclosed sale prices); fed prices, it landed about 4% from ours, so the gap was data, not math (**measured**).
- **Key lesson:** "theirs is newer" is not a reason; a cheap bake-off (about 30 API calls) ended a debate the code read had scored a tie.

## 1. The decision to make
By mid-2026 I had a working platform: county-record intake, enrichment, prioritization, valuation,
reporting, and CRM delivery. An open-source project appeared that covered the same
ground end to end, written by someone whose training I had taken. The tempting move was to switch
wholesale. The disciplined move was to answer, area by area: *keep ours, adopt theirs, or test both
on real numbers before deciding?* The rule going in: our platform stays the base; nothing of theirs
touches production until it wins on evidence, and no new paid vendor comes in through the back door.

## 2. The eight areas
Each area was scored from a code read of both systems (score shown as ours / theirs, out of the
criteria compared). Where the code read could not separate them, the decision went to a bake-off on a
real deal.

| Area | Theirs | Ours | Decision | Why | How decided |
|---|---|---|---|---|---|
| Record intake | Notice parser, OCR, a multi-type photo path | Daily multi-county scrape with vision reading and a second-source cross-check | Keep ours; borrow parser parts | Ours is live and verified; theirs has useful donor pieces | Code read (4 / 3) |
| Buyer matching | Basic buyer matching | Ranked buyer lists with a second track for small local buyers | Keep ours; build two features theirs only described on paper | Their two flourishes were ideas, not code | Code read (5 / 2) |
| Orchestration / notify | Test scaffolding, structured run logs | Scheduled-job fleet with Slack reporting | Keep ours; borrow their test scaffolding and log format | Their tests were better than ours; their runtime was not | Code read (4 / 3) |
| Enrichment (record matching) | Public-record lookup layer | County-assessor backbone | Keep our backbone; graft their lookup layer onto it | Tie on the read; the layers are complementary, not competing | Code read (4 / 4) |
| Comping / ARV | Deterministic adjustment arithmetic fed by a listings API | Triangulation built for a non-disclosure state, condition bucketing, outlier control | **Bake-off** | Tie on the read; only real numbers could separate them | Bake-off on a live deal |
| Third-party contact-data API (paid per record) | A per-record paid API engine | Existing vendor flow | Adopt theirs, **build-and-shelve**: off by default, no automation may call it | Better engine, but no auto-spend without a human | Code read (3 / 4) |
| Deal / rehab math | Rehab estimator + deal analyzer, tuned for another metro | Spreadsheet-era math | **Bake-off** after porting and re-tuning for my market | Theirs scored higher, but its cost tables were calibrated elsewhere | Code read (3 / 4), then bake-off |
| CRM upload | Bulk upload path (plus message templates, not adopted) | Manual daily upload by a team member | Adopt theirs, retargeted to my CRM account | Replaces a daily manual step outright | Code read (3 / 4) |

## 3. Success metrics used
- **Code read:** per-area criteria scored on both codebases, with the source cloned locally and a
  written reference document, not a README skim.
- **Bake-off (comping):** run both engines on the same live deal, fed by the same comparable
  sales, and score on (a) comps returned, (b) ARV produced, (c) distance between the two answers.
  Winner had to win on data, not on age.
- **Bake-off (deal/rehab):** port their engines into an isolated folder with zero network calls,
  ARV and rehab as inputs, then compare against closed deals with known actuals.
- **Cost gate:** no new paid vendor for any adopted piece; a third-party listings API was used on
  its free tier for the bake-off only and recorded as "evaluating," never a production dependency.

## 4. What the bake-off showed (comping)
- **measured** — Their engine could not run as written: its comps endpoint had been retired by the
  API vendor, and because my market is a non-disclosure state, the API returned no sold prices, so
  their `if not sold_price: continue` dropped all 41 comps. Zero comps, no ARV. That is the faithful
  output, not a bug in the port.
- **measured** — Fed the only available price signal (last list price) as a labeled proxy, their
  math produced a value at low confidence with a 125% spread, and pulled a home two
  and a half times the subject's size as a comp; no condition bucketing, no size-outlier control.
- **measured** — Ours produced a tight range (about ±6%) from five condition-matched, triangulated
  comps within 0.86 miles and 90 days, with the active-listing ceiling tested.
- **measured** — With price supplied, their answer landed about 4% from ours. So their math is not
  wrong, and there was no accuracy gain to chase.

**Decision (2026-06-04): ours wins; the candidate's comping shelved. Do not build the production adapter.**
The data wall is structural (their engine needs disclosed sale prices; my market has none), and
ours already contains every adjustment theirs has plus the pieces theirs lacks.

## 5. Adopted, shelved, and what changed after
- **Adopted and shipped:** the CRM bulk-upload path, retargeted to my account, replacing a daily
  manual upload. Its first live day (2026-09-09) failed on every one of 17 creates with HTTP 403;
  I root-caused it to the vendor closing a legacy route (401-vs-403 and GET-vs-POST comparisons ruled
  out our own regression), presented the one-line fix, and held it for a human decision while the
  manual upload continued as the safety net (**measured**). Cutover is still in progress.
- **Adopted as a pattern, not code:** their heartbeat idea (verdict from the printed line, missing
  webhook is a failure) became [Ops Heartbeat](ops-heartbeat.md), rebuilt for a fleet of scheduled jobs.
- **Ported, hardened, awaiting bake-off:** deal/rehab math. Review of the port found one critical
  defect before any test ran: an ARV of zero or below produced a confident GO on phantom rent. Fixed
  with hard failures on non-positive ARV, rehab, or purchase, and an "uncalibrated numbers" caveat
  on every output (**measured**). Its rehab totals read high against my market until calibrated.
- **Shelved:** their comping engine and the third-party contact-data engine. The enrichment graft and
  the message templates remain on the backlog.

## 6. Lessons learned
- The bake-off was cheap (about 30 API calls) and it ended a debate that a code read alone had
  scored a tie.
- Their engines lived in a separate folder, never imported by production, so the evaluation could
  not break the live system.
- The critical deal-math defect was in the original, and the faithful port carried it across.

## 7. Built with
Designed, directed, tested and operated by me. Implemented with Claude Code. The candidate platform
was evaluated from a local clone; nothing from it is reproduced here.
