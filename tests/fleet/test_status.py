"""`fleet status` — liveness read once, and never reported clean when unread.

A dead session keeps listing as `blocked`; only a `pid` proves a live process.
The case that matters most is the one where the instrument itself fails: an
unreadable `claude agents --json --all` must render `unknown` and say so, never
render a fleet as healthy because nothing contradicted it.
"""
import contextlib
import io
import json
import re
import unittest

from scripts import fleet
from tests.fleet.helpers import (only_fleet_dir, plain_worktree, sandbox,
                                 write_roster, write_stub)

PEERS = ["alpha", "bravo", "charlie", "delta", "echo"]


def _run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fleet.main(argv)
    return rc, buf.getvalue()


def _launch(td):
    """Five peers → session ids abc120..abc124, in roster order."""
    write_stub(td)
    wt = plain_worktree(td, name="wt")
    roster = write_roster(td, [{"slug": s, "ticket": "BRO-1"} for s in PEERS])
    _run(["up", str(roster), "--worktree", str(wt)])
    return only_fleet_dir(td)


def _rows(out: str) -> dict:
    """The table body, keyed by peer name (suggestion lines start with `•`)."""
    rows = {}
    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("wt-bro-1-"):
            rows[line.split()[0]] = line
    return rows


def _agents_fixture(td):
    """alpha live · bravo idle-start · charlie done · delta listed without a
    pid · echo not listed at all."""
    (td / "agents.json").write_text(json.dumps([
        {"id": "abc120", "name": "wt-bro-1-alpha", "pid": 4242, "state": "running"},
        {"id": "abc121", "name": "wt-bro-1-bravo", "needs": "prompt", "state": "idle"},
        {"id": "abc122", "name": "wt-bro-1-charlie", "state": "done"},
        {"id": "abc123", "name": "wt-bro-1-delta", "state": "blocked"},
    ]), encoding="utf-8")


class StatusTest(unittest.TestCase):
    def test_every_classification_is_rendered(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            rc, out = _run(["status", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            rows = _rows(out)
            self.assertIn("live", rows["wt-bro-1-alpha"])
            self.assertIn("4242", rows["wt-bro-1-alpha"])
            self.assertIn("idle-start", rows["wt-bro-1-bravo"])
            self.assertIn("done", rows["wt-bro-1-charlie"])
            self.assertIn("gone", rows["wt-bro-1-delta"])     # listed, no pid
            self.assertIn("gone", rows["wt-bro-1-echo"])      # not listed at all
            self.assertIn("abc120", rows["wt-bro-1-alpha"])

    def test_suggestions_name_the_action_per_class(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            _, out = _run(["status", "--fleet", fd.name])
            self.assertIn("SendMessage wt-bro-1-bravo its brief pointer", out)
            self.assertIn("claude attach abc121", out)
            self.assertIn("wt-bro-1-delta is gone (no pid): claude logs abc123", out)
            self.assertIn("re-dispatch with `fleet up`", out)
            # A live peer needs nothing said about it.
            self.assertNotIn("wt-bro-1-alpha is gone", out)

    def test_unreadable_listing_renders_unknown_never_clean(self):
        with sandbox() as td:
            fd = _launch(td)
            (td / "agents.fail").write_text("1")
            rc, out = _run(["status", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            self.assertIn("liveness unavailable", out)
            self.assertIn("claude agents --json --all could not be read", out)
            self.assertEqual(out.count("unknown"), len(PEERS))
            # Not one row may claim a live peer. `liveness` in the unavailable
            # line does not match \blive\b; a rendered class would.
            self.assertIsNone(re.search(r"\blive\b", out), out)
            self.assertIsNone(re.search(r"\bgone\b", out), out)

    def test_defaults_to_the_most_recent_fleet(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            _, out = _run(["status"])
            self.assertIn(fd.name, out)

    def test_all_renders_every_fleet(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_1_aaaa"])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_2_bbbb"])
            rc, out = _run(["status", "--all"])
            self.assertEqual(rc, 0)
            self.assertIn("fleet_1_aaaa", out)
            self.assertIn("fleet_2_bbbb", out)

    def test_json_status_reports_whether_liveness_was_available(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            _, out = _run(["status", "--fleet", fd.name, "--json"])
            payload = json.loads(out)
            self.assertTrue(payload["liveness_available"])
            by_name = {p["name"]: p for p in payload["fleets"][0]["peers"]}
            self.assertEqual(by_name["wt-bro-1-alpha"]["live"], "live")
            self.assertEqual(by_name["wt-bro-1-alpha"]["pid"], 4242)
            self.assertEqual(by_name["wt-bro-1-echo"]["live"], "gone")

            (td / "agents.fail").write_text("1")
            _, out2 = _run(["status", "--fleet", fd.name, "--json"])
            payload2 = json.loads(out2)
            self.assertFalse(payload2["liveness_available"])
            self.assertTrue(all(p["live"] == "unknown"
                                for p in payload2["fleets"][0]["peers"]))

    def test_unknown_fleet_id_is_an_error(self):
        with sandbox() as td:
            _launch(td)
            rc, _ = _run(["status", "--fleet", "fleet_nope_0000"])
            self.assertEqual(rc, 1)


class ListTest(unittest.TestCase):
    def test_list_counts_by_liveness_class(self):
        with sandbox() as td:
            _launch(td)
            _agents_fixture(td)
            rc, out = _run(["list"])
            self.assertEqual(rc, 0)
            self.assertIn("5 peer(s)", out)
            self.assertIn("1 live", out)
            self.assertIn("1 idle-start", out)
            self.assertIn("1 done", out)
            self.assertIn("2 gone", out)

    def test_no_fleets_says_so(self):
        with sandbox():
            rc, out = _run(["list"])
            self.assertEqual(rc, 0)
            self.assertIn("(no fleets)", out)


if __name__ == "__main__":
    unittest.main()
