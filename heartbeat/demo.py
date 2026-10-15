"""Grade two synthetic fleets offline: `python -m heartbeat.demo`.

This drives the REAL `ops_heartbeat.main()` end to end: the real workflow-file
parser, cron maths, grading, blindness rule and digest renderer. Only the three
network seams (`fetch_runs`, `fetch_registered`, `fetch_secret_names`) are
swapped for fixture readers, the clock is frozen at the fixture's `now`, and
`urlopen` is replaced with a tripwire so nothing can reach the network. No
GITHUB_TOKEN is needed; a placeholder satisfies main()'s precondition and is
never sent anywhere.

Prints, per scenario, exactly what the live tool prints (verdict line, then the
Slack digest) and the exit code the live tool would have returned. The demo
itself always exits 0.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

from . import ops_heartbeat as hb

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixtures() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]


def _workflow_yaml(w: dict) -> str:
    """A minimal scheduled workflow file, so the real parser reads the schedule."""
    return (f"name: {w['name']}\n"
            f"on:\n  schedule:\n    - cron: \"{w['cron']}\"\n"
            f"jobs:\n  run:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: ./run.sh\n"
            f"        env:\n"
            f"          SLACK_WEBHOOK_URL: ${{{{ secrets.{w['notify_secret']} }}}}\n")


def _frozen_clock(iso: str) -> type[datetime]:
    frozen = datetime.fromisoformat(iso.replace("Z", "+00:00"))

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 - matches datetime.now
            return frozen if tz else frozen.replace(tzinfo=None)

    return _Clock


def _no_network(*_a, **_k):
    raise AssertionError("heartbeat.demo attempted a network call; it must run offline")


def run_scenario(fx: dict) -> tuple[str, int]:
    """Run ops_heartbeat.main() against one fixture. Returns (stdout, exit_code)."""
    by_file = {w["file"]: w for w in fx["workflows"]}

    def fetch_runs(repo, wf_file, token, limit=hb.RUN_PAGE):
        w = by_file[wf_file]
        if "runs_error" in w:
            e = w["runs_error"]
            raise urllib.error.HTTPError(f"fixture:{wf_file}", e["status"], e["reason"], {}, None)
        runs = w["runs"][:limit]
        return runs, w.get("total_count", len(w["runs"]))

    def fetch_registered(repo, token):
        return set(by_file)

    def fetch_secret_names(repo, token):
        return set(fx["secret_names"]), ""

    with tempfile.TemporaryDirectory() as wf_dir:
        for w in fx["workflows"]:
            Path(wf_dir, w["file"]).write_text(_workflow_yaml(w), encoding="utf-8")
        argv = ["ops_heartbeat", "--repo", fx["repo"], "--workflows-dir", wf_dir]
        env = {"GITHUB_TOKEN": "offline-demo-placeholder"}
        out = io.StringIO()
        with mock.patch.object(hb, "fetch_runs", fetch_runs), \
                mock.patch.object(hb, "fetch_registered", fetch_registered), \
                mock.patch.object(hb, "fetch_secret_names", fetch_secret_names), \
                mock.patch.object(hb, "datetime", _frozen_clock(fx["now"])), \
                mock.patch("urllib.request.urlopen", _no_network), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out):
            os.environ.pop("HEARTBEAT_SECRETS_TOKEN", None)
            os.environ.pop("GH_TOKEN", None)
            code = hb.main()
    return out.getvalue(), code


def main() -> int:
    for fx in load_fixtures():
        text, code = run_scenario(fx)
        print("=" * 72)
        print(f"SCENARIO: {fx['scenario']}  (clock frozen at {fx['now']})")
        print("=" * 72)
        print(text.rstrip())
        print(f"\n-> the live tool would exit {code} "
              f"({'RED: the scheduled check fails loudly' if code else 'not RED'})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
