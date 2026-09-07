"""`fleet down` — reclaim, and the one invariant that outranks the others.

Teardown must never orphan a fleet by deleting the only record of it. The state
directory goes only when EVERY peer was removed or was already gone; a peer
whose id was never captured is exactly the case where the operator still needs
the file.
"""
import contextlib
import io
import json
import unittest

from scripts import fleet
from tests.fleet.helpers import (only_fleet_dir, plain_worktree,
                                 read_state_json, sandbox, write_roster,
                                 write_stub)


def _run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fleet.main(argv)
    return rc, buf.getvalue()


def _run_err(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fleet.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _launch(td, slugs=("alpha", "bravo")):
    write_stub(td)
    wt = plain_worktree(td, name="wt")
    roster = write_roster(td, [{"slug": s, "ticket": "BRO-1"} for s in slugs])
    _run(["up", str(roster), "--worktree", str(wt)])
    return only_fleet_dir(td)


def _calls(td) -> list[str]:
    """Every `stop`/`rm` the stub was asked for, in order."""
    log = td / "calls.log"
    if not log.exists():
        return []
    return [line.strip() for line in log.read_text().splitlines() if line.strip()]


def _both_live(td):
    """Both peers listed WITH a pid.

    Without this the listing is empty, every peer classifies as `gone`, and a
    failing `rm` is legitimately tolerated — which would make the
    unreclaimed-peer assertions below pass for the wrong reason.
    """
    (td / "agents.json").write_text(json.dumps([
        {"id": "abc120", "name": "wt-bro-1-alpha", "pid": 11, "state": "running"},
        {"id": "abc121", "name": "wt-bro-1-bravo", "pid": 12, "state": "running"},
    ]), encoding="utf-8")


class DownTest(unittest.TestCase):
    def test_stops_then_removes_exactly_this_fleets_ids(self):
        with sandbox() as td:
            fd = _launch(td)
            rc, out = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 0, out)
            self.assertEqual(_calls(td),
                             ["stop abc120", "rm abc120",
                              "stop abc121", "rm abc121"])

    def test_clean_teardown_deletes_the_state_directory(self):
        with sandbox() as td:
            fd = _launch(td)
            rc, out = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            self.assertFalse(fd.exists())
            self.assertIn("state deleted", out)

    def test_a_failed_rm_keeps_the_state_and_exits_1(self):
        with sandbox() as td:
            fd = _launch(td)
            _both_live(td)
            (td / "fail-rm-abc121").write_text("1")
            rc, out = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 1)
            self.assertTrue(fd.exists(), "state deleted despite an unreclaimed peer")
            self.assertIn("state KEPT", out)
            self.assertIn("wt-bro-1-bravo", out)
            state = read_state_json(fd)
            by_name = {p["name"]: p for p in state["peers"]}
            self.assertTrue(by_name["wt-bro-1-alpha"]["removed"])
            self.assertFalse(by_name["wt-bro-1-bravo"]["removed"])

    def test_a_peer_with_no_session_id_is_not_removed(self):
        """The crash-mid-`up` shape: the record exists, the id never landed.
        Deleting the directory here would erase the operator's only handle."""
        with sandbox() as td:
            fd = _launch(td)
            sf = fd / "fleet.json"
            data = json.loads(sf.read_text())
            data["peers"][1]["session_id"] = None
            sf.write_text(json.dumps(data))
            rc, out = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 1)
            self.assertTrue(fd.exists())
            self.assertIn("unknown id — remove by name from claude agents", out)
            # The peer with no id is never handed to `stop`/`rm`.
            self.assertEqual(_calls(td), ["stop abc120", "rm abc120"])
            self.assertFalse(read_state_json(fd)["peers"][1]["removed"])

    def test_rm_saying_not_found_counts_as_removed(self):
        """A session the client already reaped is reclaimed, not a failure."""
        with sandbox() as td:
            fd = _launch(td)
            (td / "gone-rm-abc121").write_text("1")
            rc, _ = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            self.assertFalse(fd.exists())

    def test_a_peer_already_classified_gone_tolerates_a_failing_rm(self):
        with sandbox() as td:
            fd = _launch(td)
            # bravo is listed without a pid → GONE; its rm fails transiently.
            (td / "agents.json").write_text(json.dumps([
                {"id": "abc120", "name": "wt-bro-1-alpha", "pid": 1, "state": "running"},
                {"id": "abc121", "name": "wt-bro-1-bravo", "state": "blocked"},
            ]), encoding="utf-8")
            (td / "fail-rm-abc121").write_text("1")
            rc, _ = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 0)
            self.assertFalse(fd.exists())

    def test_down_all_walks_every_fleet(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_1_aaaa"])
            _run(["up", str(roster), "--worktree", str(wt), "--fleet", "fleet_2_bbbb"])
            rc, out = _run(["down", "--all"])
            self.assertEqual(rc, 0)
            self.assertIn("fleet_1_aaaa", out)
            self.assertIn("fleet_2_bbbb", out)
            self.assertEqual(fleet.list_fleet_dirs(), [])

    def test_down_needs_a_target(self):
        with sandbox() as td:
            _launch(td)
            rc, _ = _run(["down"])
            self.assertEqual(rc, 1)

    def test_down_all_with_no_fleets_is_not_an_error(self):
        with sandbox():
            rc, out = _run(["down", "--all"])
            self.assertEqual(rc, 0)
            self.assertIn("(no fleets)", out)

    def test_missing_binary_keeps_state_and_removes_nothing(self):
        """The BLOCKER: a `claude` that cannot run must NOT delete a live
        fleet. `_ensure_claude_on_path` refuses before teardown; the message
        says so, and no `stop`/`rm` is attempted."""
        import os
        with sandbox() as td:
            fd = _launch(td)
            os.environ["BSTACK_FLEET_CLAUDE_BIN"] = "definitely-not-claude-xyz"
            rc, _out, err = _run_err(["down", "--fleet", fd.name])
            self.assertEqual(rc, 1)
            self.assertTrue(fd.exists(), "state deleted when the binary is missing")
            self.assertIn("not found on PATH", err)
            self.assertEqual(_calls(td), [])            # nothing was reclaimed

    def test_a_binary_that_cannot_launch_never_counts_a_peer_removed(self):
        """Isolate the launched-guard from `_ensure_claude_on_path`: even past
        that check, a `stop`/`rm` that OSErrors returns a 'no such' errno string
        — without the `launched` guard that reads as 'already gone' and the live
        fleet's only record is deleted."""
        import os
        with sandbox() as td:
            fd = _launch(td)
            orig = fleet._ensure_claude_on_path
            fleet._ensure_claude_on_path = lambda binary: None
            os.environ["BSTACK_FLEET_CLAUDE_BIN"] = "definitely-not-claude-xyz"
            try:
                rc, out = _run(["down", "--fleet", fd.name])
            finally:
                fleet._ensure_claude_on_path = orig
            self.assertEqual(rc, 1)
            self.assertTrue(fd.exists(), "a command that never ran removed nothing")
            self.assertIn("state KEPT", out)
            state = read_state_json(fd)
            self.assertTrue(all(p["removed"] is False for p in state["peers"]))

    def test_stop_refuses_but_rm_reaps_it_counts_removed(self):
        """A `stop` that exits non-zero does not block removal — the removal is
        `rm`, and here `rm` finds the session already gone."""
        with sandbox() as td:
            fd = _launch(td, slugs=("solo",))
            (td / "fail-stop-abc120").write_text("1")   # stop refuses (launched)
            (td / "gone-rm-abc120").write_text("1")      # rm: not found
            rc, out = _run(["down", "--fleet", fd.name])
            self.assertEqual(rc, 0, out)
            self.assertFalse(fd.exists())

    def test_json_report_names_what_remains(self):
        with sandbox() as td:
            fd = _launch(td)
            _both_live(td)
            (td / "fail-rm-abc121").write_text("1")
            rc, out = _run(["down", "--fleet", fd.name, "--json"])
            self.assertEqual(rc, 1)
            payload = json.loads(out)
            report = payload["fleets"][0]
            self.assertEqual(report["removed"], ["wt-bro-1-alpha"])
            self.assertEqual(report["remaining"][0]["name"], "wt-bro-1-bravo")
            self.assertFalse(report["state_deleted"])


if __name__ == "__main__":
    unittest.main()
