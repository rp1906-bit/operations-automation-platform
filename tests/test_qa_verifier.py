"""One named test per decision-logic line in the publication spec, plus the coverage gate.

Bias: every uncertain path lands on draft or escalate. Auto-execute is the narrow exception."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qa_verifier import bands, breakers, plain_english, signatures
from qa_verifier.ledger import Ledger, LedgerRewriteError
from qa_verifier.signatures import HIGH, LOW, MEDIUM, Match, Signature
from qa_verifier.verdict import GREEN, RED, UNVERIFIED, YELLOW, grade
from qa_verifier.verify import verify_run

FIX = Path(__file__).parent.parent / "qa_verifier" / "fixtures"
NOW = datetime(2026, 6, 10, 12, 47, tzinfo=timezone.utc)
CLEAN = {"reviewer_a": "clean", "reviewer_b": "clean"}


def fixture(name):
    return json.loads((FIX / name).read_text())


def timeout_match():
    return signatures.diagnose(grade(fixture("05-red-timeout-band1.json")))


# ---------- verdict ----------

def test_exit_code_zero_with_wrong_output_grades_red():
    run = fixture("00-red-false-green-0422.json")          # exit 0, 2 of 19 documents read, 15 wrong rows
    v = grade(run)
    assert run["exit_code"] == 0 and v.dimensions["run_health"] == GREEN
    assert v.overall == RED and v.dimensions["extraction"] == RED


def test_verdict_is_worst_dimension_wins():
    v = grade(fixture("07-red-delivery-draft.json"))
    assert [d for d in v.dimensions.values() if d == RED] == [RED]   # exactly one dimension RED
    assert v.overall == RED


def test_one_red_and_one_yellow_dimension_grades_red():
    run = dict(fixture("01-green-clean.json"), warnings=1, delivered=False)   # run_health YELLOW, delivery RED
    v = grade(run)
    assert v.dimensions["run_health"] == YELLOW and v.dimensions["delivery"] == RED
    assert v.overall == RED


def test_worst_dimension_wins_regardless_of_dimension_order():
    run = dict(fixture("01-green-clean.json"), exit_code=1, docs_read=14)     # run_health RED, extraction YELLOW
    v = grade(run)
    assert v.dimensions["run_health"] == RED and v.dimensions["extraction"] == YELLOW
    assert v.overall == RED


def test_yellow_posts_and_watches_with_no_fix():
    v, _, decision, card = verify_run(fixture("02-yellow-warning.json"), Ledger(), breakers.RunLock(), NOW)
    assert v.overall == YELLOW and decision.band == "none" and "watch" in decision.reason
    assert "No fix" in card


# ---------- bands ----------

def test_band1_auto_executes_only_when_all_conditions_hold():
    run = fixture("05-red-timeout-band1.json")
    d = bands.triage(timeout_match(), run, CLEAN, None)
    assert d.band == bands.AUTO_EXECUTE and d.cost_usd < bands.COST_CAP_USD


@pytest.mark.parametrize("reviews", [
    {"reviewer_a": "clean", "reviewer_b": "dissent"},
    {"reviewer_a": "clean", "reviewer_b": "timeout"},
    {"reviewer_a": "clean", "reviewer_b": "unable to review"},
    {"reviewer_a": "clean"},          # one reviewer missing
    None,
])
def test_band1_any_review_dissent_timeout_or_unable_escalates(reviews):
    d = bands.triage(timeout_match(), fixture("05-red-timeout-band1.json"), reviews, None)
    assert d.band == bands.ESCALATE


def test_band1_needs_pre_vetted_fix_for_exact_signature():
    run = fixture("00-red-false-green-0422.json")       # HIGH confidence, fix module never vetted
    d = bands.triage(signatures.diagnose(grade(run)), run, CLEAN, None)
    assert d.band == bands.DRAFT and "no pre-vetted fix" in d.reason


def test_band1_needs_idempotent_fix_and_high_confidence():
    run = fixture("05-red-timeout-band1.json")
    sig = timeout_match().signature
    medium = Match(Signature(sig.name, MEDIUM, sig.matches, sig.fix_module, sig.declared_cost, sig.fix_units), MEDIUM)
    assert bands.triage(medium, run, CLEAN, None).band == bands.DRAFT
    non_idem = Match(Signature(sig.name, HIGH, sig.matches, "expand_target_grid", lambda r: 0.5, lambda r: 1), HIGH)
    assert bands.triage(non_idem, run, CLEAN, None).band == bands.DRAFT


def test_band1_needs_cost_under_cap(monkeypatch):
    run = dict(fixture("05-red-timeout-band1.json"), docs_attempted=1000, docs_read=0)   # 1000 re-reads
    sig = timeout_match().signature
    m = Match(Signature(sig.name, HIGH, sig.matches, sig.fix_module, lambda r: 1000 * 0.02, sig.fix_units), HIGH)
    d = bands.triage(m, run, CLEAN, None)
    assert d.band == bands.DRAFT and "cap" in d.reason


def test_cost_recomputed_independently_and_divergence_downgrades(monkeypatch):
    monkeypatch.setitem(bands.UNIT_PRICES_USD, "document_reread", 0.03)   # price moved; signature still declares 0.02/unit
    d = bands.triage(timeout_match(), fixture("05-red-timeout-band1.json"), CLEAN, None)
    assert d.band == bands.DRAFT and "diverges" in d.reason


def _hundred_rereads():
    return dict(fixture("05-red-timeout-band1.json"), docs_attempted=100, docs_read=0)   # declared $2.00


def test_cost_divergence_just_under_twenty_percent_stays_band1(monkeypatch):
    monkeypatch.setitem(bands.UNIT_PRICES_USD, "document_reread", 0.0239)   # recomputed $2.39, 19.5% over
    d = bands.triage(timeout_match(), _hundred_rereads(), CLEAN, None)
    assert d.band == bands.AUTO_EXECUTE


def test_cost_divergence_just_over_twenty_percent_downgrades(monkeypatch):
    monkeypatch.setitem(bands.UNIT_PRICES_USD, "document_reread", 0.0241)   # recomputed $2.41, 20.5% over
    d = bands.triage(timeout_match(), _hundred_rereads(), CLEAN, None)
    assert d.band == bands.DRAFT and "diverges" in d.reason


def test_band2_writes_draft_with_diff_rationale_cost_rollback_and_waits():
    run = fixture("07-red-delivery-draft.json")
    _, _, d, card = verify_run(run, Ledger(), breakers.RunLock(), NOW)
    assert d.band == bands.DRAFT and set(d.draft) == {"diff", "rationale", "cost_usd", "rollback"}
    assert "awaiting approval" in card


def test_band3_posts_evidence_only():
    m = Match(None, LOW)
    d = bands.triage(m, {}, CLEAN, None)
    assert d.band == bands.ESCALATE and d.draft == {}


# ---------- breakers ----------

def test_same_signature_twice_in_24h_forces_human_review_across_pipelines():
    ledger, lock = Ledger(), breakers.RunLock()
    _, _, first, _ = verify_run(fixture("05-red-timeout-band1.json"), ledger, lock, NOW, CLEAN)
    assert first.band == bands.AUTO_EXECUTE
    other = fixture("06-red-timeout-repeat-other-pipeline.json")     # different pipeline, same signature
    _, _, second, _ = verify_run(other, ledger, lock, NOW + timedelta(hours=6), CLEAN)
    assert second.band == bands.ESCALATE and "repeat signature" in second.reason
    _, _, later, _ = verify_run(other, ledger, lock, NOW + timedelta(hours=40), CLEAN)
    assert later.band == bands.AUTO_EXECUTE                          # 24h window has expired


def test_cost_cap_is_a_hard_limit():
    assert bands.COST_CAP_USD == 5.00
    assert bands.recompute_cost(bands.FIX_REGISTRY["reread_documents"], 300) >= bands.COST_CAP_USD


def test_one_run_per_pipeline_lock_expires():
    lock = breakers.RunLock()
    assert lock.acquire("lien_scraper", NOW)
    assert not lock.acquire("lien_scraper", NOW + timedelta(minutes=5))
    assert lock.acquire("notice_scraper", NOW + timedelta(minutes=5))       # other pipeline unaffected
    assert lock.acquire("lien_scraper", NOW + timedelta(minutes=31))        # stale lock expired
    _, _, d, card = verify_run(fixture("01-green-clean.json"), Ledger(), lock, NOW + timedelta(minutes=32))
    assert d.band == "busy" and card == "BUSY"


def test_protected_target_is_never_touched():
    run = dict(fixture("05-red-timeout-band1.json"), fix_targets=["records", "crm_upload"])
    _, _, d, _ = verify_run(run, Ledger(), breakers.RunLock(), NOW, CLEAN)
    assert d.band == bands.ESCALATE and "protected" in d.reason


# ---------- message classes ----------

def test_checker_environment_fault_is_distinct_from_scrape_failure():
    _, m_fault, _, card_fault = verify_run(fixture("04-red-checker-fault.json"), Ledger(), breakers.RunLock(), NOW)
    _, m_scrape, _, card_scrape = verify_run(fixture("05-red-timeout-band1.json"), Ledger(), breakers.RunLock(), NOW, CLEAN)
    assert m_fault.name == "checker_environment_fault" and m_scrape.name == "document_read_timeout"
    assert "CHECK INCOMPLETE" in card_fault and "scrape may be fine" in card_fault
    assert "CHECK INCOMPLETE" not in card_scrape


def test_checker_fault_grades_unverified_never_red_never_green():
    run = fixture("04-red-checker-fault.json")             # healthy run; the checker's own import failed
    v = grade(run)
    assert v.overall == UNVERIFIED and v.dimensions["extraction"] == UNVERIFIED
    assert RED not in v.dimensions.values()                # never a false RED about a healthy scrape
    assert v.overall != GREEN                              # never a default pass
    ledger = Ledger()
    _, _, decision, card = verify_run(run, ledger, breakers.RunLock(), NOW)
    assert decision.band == "escalate" and ledger.rows[-1]["verdict"] == UNVERIFIED
    assert card.startswith("⚪") and "UNVERIFIED" in card.splitlines()[0]


# ---------- ledger ----------

def test_ledger_is_append_only_and_records_green_runs():
    ledger, lock = Ledger(), breakers.RunLock()
    verify_run(fixture("04-red-checker-fault.json"), ledger, lock, NOW)
    verify_run(fixture("08-green-recovery.json"), ledger, lock, NOW + timedelta(days=1))
    assert [r["verdict"] for r in ledger.rows] == [UNVERIFIED, GREEN]
    with pytest.raises(LedgerRewriteError):
        ledger[0] = {}
    with pytest.raises(LedgerRewriteError):
        del ledger[0]
    assert isinstance(ledger.rows, tuple)                            # no in-place edits via the view


# ---------- coverage gate ----------

def test_glossary_coverage_gate_every_signature_has_plain_english():
    assert plain_english.coverage_gaps() == []
    for f in ("exit_status", "doc_read_rate", "empty_address_rate", "ambiguous_owner_count", "delivered", "metric_source_missing"):
        assert f in plain_english.GLOSSARY


def test_glossary_coverage_gate_fails_on_new_signature_without_wording(monkeypatch):
    new = Signature("brand_new_fault", LOW, lambda v: False)
    monkeypatch.setattr(plain_english, "SIGNATURES", signatures.SIGNATURES + (new,))
    assert plain_english.coverage_gaps() == ["brand_new_fault"]
