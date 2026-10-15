"""One named test per decision-logic line in the publication spec (county pipeline + CRM delivery stage).

Bias: a row the pipeline is not sure about goes to a human. It never guesses and never drops."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from county_pipeline import breaker, quiet
from county_pipeline.dedupe import Manifest, dedupe
from county_pipeline.delivery import (FakeCrm, ParallelRun, TabRegistry, WriterConflict, accept, deliver,
                                      diagnose_denial)
from county_pipeline.enrich import ParcelTable, enrich
from county_pipeline.normalize import Row
from county_pipeline.pipeline import run_day
from county_pipeline.reader import read_document
from county_pipeline.source import FIXTURES, WATCH_LIST, MarlowPortal

DAY1, DAY2, DAY3 = "2026-06-01T23:59", "2026-06-02T23:59", "2026-06-03T23:59"
PACKAGE = Path(__file__).parent.parent / "county_pipeline"


@pytest.fixture
def parcels():
    return ParcelTable(FIXTURES / "parcels.json")


@pytest.fixture
def crm():
    return FakeCrm(json.loads((FIXTURES / "crm_seed.json").read_text()))


def row(owner="HARRIET VANCE", address="14 Larkspur Row", filed="2026-06-01", flags=()):
    return Row(doc_id="x", filed=filed, owner=owner, address=address, flags=list(flags))


# ---------- county pipeline ----------

def test_fetch_only_what_changed():
    portal = MarlowPortal()
    first = portal.fetch_changed("", DAY1)
    again = portal.fetch_changed(DAY1, DAY2)                 # nothing changed on day two
    day3 = portal.fetch_changed(DAY2, DAY3)
    assert len(first) == 21 and again == []
    assert all(r["modified"] > DAY2 for r in day3)          # only the re-stamped and new records
    assert portal.fetches == len(first) + len(day3) < 2 * len(first)


def test_dedupe_on_address_date_owner_composite_key():
    a = row()
    same = row(owner="harriet  vance", address="14 LARKSPUR ROW ")     # spacing and case differ only
    other_date = row(filed="2026-06-02")
    new, dups = dedupe([a, same, other_date], Manifest())
    assert new == [a, other_date] and dups == [same]
    manifest = Manifest(); manifest.commit(new)
    assert dedupe([row()], manifest) == ([], [row()])                 # seen in an earlier run


def test_ambiguous_second_source_match_blanks_and_flags_never_guesses(parcels):
    ambiguous = enrich(row(owner="MARGO DELACROIX", address=""), parcels)   # two parcels, no address
    assert ambiguous.parcel_id == "" and ambiguous.mailing == "" and ambiguous.value is None
    assert any(f.startswith("ambiguous: 2 parcels") for f in ambiguous.flags)
    single = enrich(row(owner="TOBIAS RENNICK", address=""), parcels)        # one parcel: unambiguous
    assert single.parcel_id == "P-2001" and not single.flags


def test_unreadable_document_fails_extraction_instead_of_inventing_an_address():
    result = read_document({"readable": False, "text": "<<scan illegible>>"})
    assert not result.ok and result.address == ""


def test_incomplete_records_route_to_review_queue_nothing_dropped(parcels):
    report = run_day(MarlowPortal(), "", DAY1, Manifest(), parcels)
    assert report.fetched == report.duplicates + len(report.written) + len(report.queued)
    assert len(report.queued) == 2 and all(r.flags for r in report.queued)
    assert all(r.complete for r in report.written)


def test_circuit_breaker_aborts_at_nine_in_ten():
    rows = [row(flags=["wrong"])] * 9 + [row()]
    assert breaker.should_abort(rows) == (True, 0.9)


def test_circuit_breaker_continues_below_nine_in_ten():
    rows = [row(flags=["wrong"])] * 8 + [row()] * 2
    assert breaker.should_abort(rows) == (False, 0.8)


def test_aborted_batch_writes_nothing_and_leaves_manifest_uncommitted(parcels):
    portal, manifest = MarlowPortal(), Manifest()
    keys_after_day1 = set(run_day(portal, "", DAY1, manifest, parcels) and manifest.keys)
    report = run_day(portal, DAY2, DAY3, manifest, parcels)          # 1 re-stamped duplicate + 10 new, 9 wrong
    assert report.aborted and report.new_rows == 10 and report.wrong_ratio == 0.9
    assert report.written == [] and report.queued == [] and manifest.keys == keys_after_day1


def test_quiet_streak_separates_quiet_county_from_stale_scraper():
    assert quiet.classify_run(0, probe_proved_health=False) == quiet.SUSPICIOUS   # portal not proven up
    assert quiet.classify_run(0, probe_proved_health=True) == quiet.QUIET         # a genuinely quiet day
    streak = 0
    for day in range(1, quiet.QUIET_STREAK_THRESHOLD + 1):
        streak, status = quiet.update_streak(quiet.QUIET, streak)
        assert status == (quiet.QUIET_STALE if day >= quiet.QUIET_STREAK_THRESHOLD else quiet.QUIET)
    assert quiet.update_streak(quiet.OK, streak) == (0, quiet.OK)                # real rows reset it


def test_generic_watch_list_only():
    assert WATCH_LIST == {"lien"}
    assert all(r["filing_type"] == "lien" for r in MarlowPortal().fetch_changed("", DAY3))


# ---------- CRM delivery stage ----------

def test_one_writer_per_tab(crm):
    registry = TabRegistry()
    deliver([row()], crm, "Marlow Lien", "bridge", registry)
    with pytest.raises(WriterConflict):
        deliver([row(address="5 Sable Hollow")], crm, "Marlow Lien", "team-member-upload", registry)
    deliver([row(address="5 Sable Hollow")], crm, "Marlow Lien", "bridge", registry)   # same writer is fine


def test_stack_dont_skip_for_records_already_present(crm):
    before = copy.deepcopy(crm.find("9 Heron Terrace"))
    summary = deliver([row(address="9 Heron Terrace")], crm, "Marlow Lien", "bridge", TabRegistry())
    after = crm.find("9 Heron Terrace")
    assert summary.stacked == [("9 Heron Terrace", "crm-0001")] and summary.created == []
    assert after["uuid"] == before["uuid"] and after["lists"] == ["Code Notice", "Marlow Lien"]
    assert after["notes"] == before["notes"] + ["Marlow Lien filed 2026-06-01"]
    assert len(crm.records) == 3                                                    # no duplicate record


def test_acceptance_criteria_checkable_from_the_summary_alone(crm):
    summary = deliver([row(), row(address="9 Heron Terrace"), row(address="7 Fennel Walk")],
                      crm, "Marlow Lien", "bridge", TabRegistry())
    ok, problems = accept(summary)                       # no CRM handle passed in
    assert ok and problems == [] and len(summary.unconfirmed) == 1
    summary.stacked.append(("14 LARKSPUR ROW", ""))      # a stacked row that names no existing record
    assert accept(summary) == (False, ["created and stacked the same address: ['14 LARKSPUR ROW']",
                                       "a stacked row names no existing record"])


def test_five_weekday_parallel_run_before_cutover():
    run = ParallelRun()
    for _ in range(4):
        run.record(accepted=True)
    run.record(accepted=True, weekday=False)             # a weekend does not count
    assert not run.cutover_ready
    run.record(accepted=False)                           # one failed day restarts the clock
    assert run.passed == 0
    for _ in range(5):
        run.record(accepted=True)
    assert run.cutover_ready


def test_403_diagnosis_reads_401_vs_403_and_get_vs_post_as_evidence():
    verdict = diagnose_denial(401, 403, 403, code_changed_since_last_success=False)
    assert verdict.startswith("vendor-side route closure")
    assert diagnose_denial(401, 200, 403, code_changed_since_last_success=False).startswith("method-level")
    assert diagnose_denial(403, 403, 403, code_changed_since_last_success=False).startswith("inconclusive")
