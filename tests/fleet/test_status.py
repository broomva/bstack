"""`fleet status` — liveness read once, and never reported clean when unread.

The fixture here is not invented: it is CLONED from the one committed real
`claude agents --json --all` capture (`tests/wave/fixtures/claude-agents-2.1.258.json`),
with only the `id`/`name` remapped onto the fleet's peers. An earlier draft used
a made-up schema (`needs`, `state: running`) and passed against nothing the real
`scripts/peer.py` classifier ever sees. The classes asserted below are the
`peer.*` constants, so the test tracks the real classifier rather than a string.

A dead session keeps listing as `blocked`; only a `pid` proves a live process.
The case that matters most is the one where the instrument itself fails: an
unreadable listing must render `unknown` and say so, never render a fleet as
healthy because nothing contradicted it.
"""
import contextlib
import copy
import io
import json
import re
import unittest
from pathlib import Path

from scripts import fleet, peer
from tests.fleet.helpers import (only_fleet_dir, plain_worktree, sandbox,
                                 write_roster, write_stub)

PEERS = ["alpha", "bravo", "charlie", "delta", "echo"]

# The one committed real capture, reached relative to THIS file — it lives at
# ../wave/fixtures/ from tests/fleet/.
_REAL_AGENTS = (Path(__file__).resolve().parent.parent / "wave" / "fixtures"
                / "claude-agents-2.1.258.json")


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


def _real_of(classification: str) -> dict:
    """The first real fixture entry `peer.classify` puts in `classification`.

    This is what pins the test to the real schema: if a future client build
    changes the fields, the class this returns changes with it, not a literal.
    """
    for entry in json.loads(_REAL_AGENTS.read_text(encoding="utf-8")):
        if peer.classify(entry) == classification:
            return entry
    raise AssertionError(f"no real fixture entry classifies as {classification}")


def _as(classification: str, *, name: str, sid: str, pid: int | None = None) -> dict:
    """Clone a real entry of the wanted class and remap it onto a fleet peer."""
    entry = copy.deepcopy(_real_of(classification))
    entry["id"] = sid                 # fleet joins by the short id it recorded
    entry["name"] = name
    if pid is not None and "pid" in entry:
        entry["pid"] = pid
    return entry


def _agents_fixture(td):
    """alpha live · bravo waiting (a real `waitingFor`) · charlie done · delta
    listed but gone (a real failed/stopped shape, no pid) · echo not listed.

    Every entry is a clone of a real capture, so `peer.classify` sees exactly
    the fields the client emits."""
    (td / "agents.json").write_text(json.dumps([
        _as(peer.LIVE, name="wt-bro-1-alpha", sid="abc120", pid=4242),
        _as(peer.WAITING, name="wt-bro-1-bravo", sid="abc121"),
        _as(peer.DONE, name="wt-bro-1-charlie", sid="abc122"),
        _as(peer.GONE, name="wt-bro-1-delta", sid="abc123"),
        # echo (abc124) is deliberately absent → GONE by omission.
    ]), encoding="utf-8")


class ClassifyShapeTest(unittest.TestCase):
    """The shapes the real classifier reaches, straight from the fixture — the
    guardrail the invented schema tripped over."""

    def test_background_blocked_with_a_pid_is_waiting_not_live(self):
        # A background `--bg` peer surfaces "needs the operator" as
        # `state: blocked` (never `status: waiting`, which is the interactive
        # dialog layer) — with a live `pid` and even `status: idle`. That is
        # WAITING, not LIVE: the peer is stalled on a login / usage / permission
        # prompt and cannot take an inbound message. Only a `blocked` WITHOUT a
        # pid is the dead-session shape and classifies GONE (see `delta`).
        blocked = next(e for e in json.loads(_REAL_AGENTS.read_text())
                       if e.get("kind") == "background"
                       and e.get("state") == "blocked" and e.get("pid"))
        self.assertEqual(peer.classify(blocked), peer.WAITING)

    def test_interactive_waiting_entry_carries_a_reason(self):
        # An interactive `status: waiting` entry is the only shape that carries
        # a `waitingFor` reason; a background WAITING (state:blocked) does not,
        # and its suggestion degrades to "input".
        waiting = next(e for e in json.loads(_REAL_AGENTS.read_text())
                       if e.get("status") == "waiting")
        self.assertEqual(peer.classify(waiting), peer.WAITING)
        self.assertTrue(peer.waiting_for(waiting))          # a real `waitingFor`

    def test_background_waiting_reason_degrades_to_empty(self):
        blocked = next(e for e in json.loads(_REAL_AGENTS.read_text())
                       if e.get("kind") == "background"
                       and e.get("state") == "blocked" and e.get("pid"))
        self.assertEqual(peer.waiting_for(blocked), "")


class StatusTest(unittest.TestCase):
    def test_every_classification_is_rendered(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            rc, out = _run(["status", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            rows = _rows(out)
            self.assertIn(peer.LIVE, rows["wt-bro-1-alpha"])
            self.assertIn("4242", rows["wt-bro-1-alpha"])
            self.assertIn(peer.WAITING, rows["wt-bro-1-bravo"])
            self.assertIn(peer.DONE, rows["wt-bro-1-charlie"])
            self.assertIn(peer.GONE, rows["wt-bro-1-delta"])   # listed, no pid
            self.assertIn(peer.GONE, rows["wt-bro-1-echo"])    # not listed at all
            self.assertIn("abc120", rows["wt-bro-1-alpha"])
            # The invented schema's vocabulary must never appear.
            self.assertNotIn("idle-start", out)
            self.assertNotIn("needs", out)

    def test_suggestions_name_the_action_per_class(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            _, out = _run(["status", "--fleet", fd.name])
            # A waiting peer can only be answered or restarted.
            self.assertIn("wt-bro-1-bravo", out)
            self.assertIn("claude attach abc121", out)
            self.assertIn("wt-bro-1-delta is gone (no pid): claude logs abc123", out)
            self.assertIn("re-dispatch with `fleet up`", out)
            # A live peer needs nothing said about it.
            self.assertNotIn("wt-bro-1-alpha is gone", out)

    def test_waiting_suggestion_carries_the_reason_and_the_restart_action(self):
        with sandbox() as td:
            fd = _launch(td)
            (td / "agents.json").write_text(json.dumps([
                {"id": "abc120", "name": "wt-bro-1-alpha", "pid": 71,
                 "status": "waiting", "waitingFor": "dialog open"},
                {"id": "abc121", "name": "wt-bro-1-bravo", "pid": 72,
                 "kind": "background", "state": "working", "status": "waiting"},
            ]), encoding="utf-8")
            _, out = _run(["status", "--fleet", fd.name])
            self.assertIn("wt-bro-1-alpha is waiting (dialog open)", out)
            self.assertIn("claude attach abc120 to answer it, or stop+respawn", out)
            # A background peer carries no waitingFor → the reason is `input`.
            self.assertIn("wt-bro-1-bravo is waiting (input)", out)
            # The retired wording (a waiting peer cannot take a SendMessage).
            self.assertNotIn("its brief pointer", out)

    def test_unreadable_listing_renders_unknown_never_clean(self):
        with sandbox() as td:
            fd = _launch(td)
            (td / "agents.fail").write_text("1")
            rc, out = _run(["status", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            self.assertIn("liveness unavailable", out)
            self.assertIn("claude agents --json --all could not be read", out)
            self.assertEqual(out.count(peer.UNKNOWN), len(PEERS))
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

    def test_all_skips_an_unreadable_fleet_dir(self):
        """One corrupt fleet.json must not blind the whole sweep."""
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_ok_0001"])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_bad_0002"])
            (fleet.state_root() / "fleet_bad_0002" / "fleet.json").write_text(
                "{ not json", encoding="utf-8")
            rc, out = _run(["status", "--all"])
            self.assertEqual(rc, 0)
            self.assertIn("fleet_ok_0001", out)      # healthy fleet still shown

    def test_json_status_reports_whether_liveness_was_available(self):
        with sandbox() as td:
            fd = _launch(td)
            _agents_fixture(td)
            _, out = _run(["status", "--fleet", fd.name, "--json"])
            payload = json.loads(out)
            self.assertTrue(payload["liveness_available"])
            by_name = {p["name"]: p for p in payload["fleets"][0]["peers"]}
            self.assertEqual(by_name["wt-bro-1-alpha"]["live"], peer.LIVE)
            self.assertEqual(by_name["wt-bro-1-alpha"]["pid"], 4242)
            self.assertEqual(by_name["wt-bro-1-bravo"]["live"], peer.WAITING)
            self.assertEqual(by_name["wt-bro-1-echo"]["live"], peer.GONE)

            (td / "agents.fail").write_text("1")
            _, out2 = _run(["status", "--fleet", fd.name, "--json"])
            payload2 = json.loads(out2)
            self.assertFalse(payload2["liveness_available"])
            self.assertTrue(all(p["live"] == peer.UNKNOWN
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
            self.assertIn(f"1 {peer.LIVE}", out)
            self.assertIn(f"1 {peer.WAITING}", out)
            self.assertIn(f"1 {peer.DONE}", out)
            self.assertIn(f"2 {peer.GONE}", out)

    def test_no_fleets_says_so(self):
        with sandbox():
            rc, out = _run(["list"])
            self.assertEqual(rc, 0)
            self.assertIn("(no fleets)", out)


if __name__ == "__main__":
    unittest.main()
