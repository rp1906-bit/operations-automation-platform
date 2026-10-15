"""The offline heartbeat demo: runs the real grading path, touches no network."""
from __future__ import annotations

import re
import socket
from pathlib import Path

from heartbeat import demo

FIX = Path(demo.__file__).parent / "fixtures"


def _by_name(fragment):
    return next(fx for fx in demo.load_fixtures() if fragment in fx["scenario"].lower())


def test_demo_runs_offline_without_a_token_and_exits_0(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def no_sockets(*_a, **_k):
        raise AssertionError("demo opened a socket")

    monkeypatch.setattr(socket, "create_connection", no_sockets)
    monkeypatch.setattr(socket.socket, "connect", no_sockets)
    assert demo.main() == 0
    out = capsys.readouterr().out
    assert out.count("SCENARIO:") == 2
    assert "would exit" in out


def test_each_scenario_prints_its_expected_verdict_line():
    readable = _by_name("readable")
    text, code = demo.run_scenario(readable)
    verdict = text.splitlines()[0]
    assert verdict.startswith(readable["expect"]["level"]), verdict
    assert code == readable["expect"]["exit_code"]
    # each fixture job lands where it was designed to, via the real grader
    assert "*daily-export* — failing 3 runs in a row" in text
    assert "*weekly-rollup* — no scheduled run has EVER happened" in text
    assert "MORNING_DIGEST_SLACK_WEBHOOK_URL which does not exist" in text
    assert "*inventory-refresh* — last run failed (1 since a success)" in text
    assert "*record-cleanup* — could not assess (HTTP 403 Forbidden)" in text
    assert "still within the grace window" in text
    assert "its timing is NOT being watched" in text
    assert ":white_check_mark: *nightly-sync* — last ok" in text
    assert "BLIND" not in text, "one unreadable job must not page the fleet"

    text, code = demo.run_scenario(_by_name("blind"))
    assert text.splitlines()[0].startswith("RED"), text.splitlines()[0]
    assert "BLIND — 8 of 8" in text
    assert code == 1


def test_fixtures_hold_no_urls_or_token_like_strings():
    token_like = re.compile(
        r"gh[pousr]_[A-Za-z0-9]{10,}|github_pat_|xox[abpors]-|sk-[A-Za-z0-9]{10,}"
        r"|Bearer\s|[A-Za-z0-9+/]{32,}")
    files = sorted(FIX.glob("*.json"))
    assert files
    for p in files:
        body = p.read_text()
        assert "http" not in body.lower(), p.name
        assert not token_like.search(body), (p.name, token_like.search(body))
