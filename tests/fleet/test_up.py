"""`fleet up` — the spawn path, the briefs, and the state-before-spawn rule.

The stub records whether a `fleet.json` already existed at the moment it was
invoked. That is the assertion a "write the state after spawning" mutation
cannot survive: a crash between two launches must leave a file that names every
peer the operator now has to find.
"""
import contextlib
import io
import unittest

from scripts import fleet
from tests.fleet.helpers import (argv_logs, only_fleet_dir, plain_worktree,
                                 read_state_json, sandbox, write_roster,
                                 write_stub)


def _run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fleet.main(argv)
    return rc, buf.getvalue()


ROSTER = [
    {"slug": "fixer", "ticket": "BRO-2454", "prompt": "Fix the flaky test.",
     "owns": ["scripts/*.py"]},
    {"slug": "reviewer", "ticket": "BRO-2454", "prompt": "Review the fix.",
     "owns": ["tests/**"]},
]


class DryRunTest(unittest.TestCase):
    def test_dry_run_writes_nothing_and_spawns_nothing(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            rc, out = _run(["up", str(roster), "--worktree", str(wt), "--dry-run"])
            self.assertEqual(rc, 0)
            # The state root itself must not appear: a dry run that mints a
            # directory has already changed the machine.
            self.assertFalse(fleet.state_root().exists(), out)
            self.assertEqual(argv_logs(td), [])

    def test_dry_run_prints_the_full_argv_and_brief_paths(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            _, out = _run(["up", str(roster), "--worktree", str(wt), "--dry-run"])
            self.assertIn("--name wt-bro-2454-fixer", out)
            self.assertIn("--name wt-bro-2454-reviewer", out)
            self.assertIn("--strict-mcp-config", out)
            self.assertIn("crossSessionInbound", out)
            self.assertIn("briefs/wt-bro-2454-fixer.md", out)
            self.assertIn("would be written", out)


class UpTest(unittest.TestCase):
    def test_state_ids_briefs_and_prompt(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            rc, out = _run(["up", str(roster), "--worktree", str(wt)])
            self.assertEqual(rc, 0, out)

            fd = only_fleet_dir(td)
            state = read_state_json(fd)
            self.assertEqual(state["schema_version"], 1)
            by_name = {p["name"]: p for p in state["peers"]}
            self.assertEqual(sorted(by_name),
                             ["wt-bro-2454-fixer", "wt-bro-2454-reviewer"])
            self.assertEqual(by_name["wt-bro-2454-fixer"]["session_id"], "abc120")
            self.assertEqual(by_name["wt-bro-2454-reviewer"]["session_id"], "abc121")
            self.assertTrue(by_name["wt-bro-2454-fixer"]["spawned"])
            self.assertEqual(by_name["wt-bro-2454-fixer"]["owns"], ["scripts/*.py"])
            self.assertTrue(by_name["wt-bro-2454-fixer"]["launched_at"])

            # One line per peer, `name → session_id`.
            self.assertIn("wt-bro-2454-fixer → abc120", out)
            self.assertIn("ListAgents", out)

    def test_up_trailer_speaks_the_real_liveness_vocabulary(self):
        """The trailer once told the operator to look for `needs=<…>`, a field
        that does not exist. It must name the real liveness vocabulary."""
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            rc, out = _run(["up", str(roster), "--worktree", str(wt)])
            self.assertEqual(rc, 0, out)
            self.assertNotIn("needs", out)
            self.assertIn("status=waiting", out)
            self.assertIn("state=blocked", out)

    def test_state_file_exists_before_the_first_spawn(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            _run(["up", str(roster), "--worktree", str(wt)])
            first = td / "state-at-spawn-0"
            self.assertTrue(first.exists(), "stub never ran")
            self.assertEqual(first.read_text().strip(), "yes",
                             "fleet.json did not exist when the first peer "
                             "spawned — a crash mid-up would orphan the fleet")

    def test_brief_carries_the_name_the_lane_and_the_task(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            _run(["up", str(roster), "--worktree", str(wt)])
            fd = only_fleet_dir(td)
            brief = (fd / "briefs" / "wt-bro-2454-fixer.md").read_text()
            self.assertIn("wt-bro-2454-fixer", brief)
            self.assertIn("you cannot rename yourself", brief)
            self.assertIn("/rename wt-bro-2454-fixer", brief)
            self.assertIn("/autonomous", brief)             # the peer contract
            self.assertIn("scripts/*.py", brief)            # the lane
            self.assertIn("READ-ONLY", brief)
            self.assertIn("## Task", brief)
            self.assertIn("Fix the flaky test.", brief)     # the prompt, verbatim
            # Durability rules, each one a measured disturbance.
            self.assertIn("Login expired", brief)
            self.assertIn("orchestrator owns the wait", brief)
            self.assertIn("claim to verify", brief)

    def test_positional_prompt_is_last_and_points_at_the_brief(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, ROSTER)
            _run(["up", str(roster), "--worktree", str(wt)])
            fd = only_fleet_dir(td)
            logs = argv_logs(td)
            self.assertEqual(len(logs), 2)
            for argv, slug in zip(logs, ("fixer", "reviewer")):
                name = f"wt-bro-2454-{slug}"
                self.assertEqual(argv[0], "--bg")
                self.assertEqual(argv[1], "--name")
                self.assertEqual(argv[2], name)
                self.assertIn("--strict-mcp-config", argv)
                self.assertIn('{"crossSessionInbound":"accept"}', argv)
                prompt = argv[-1]                          # LAST, per the contract
                self.assertTrue(prompt.startswith(f"You are {name} (fleet fleet_"),
                                prompt)
                self.assertIn(str(fd / "briefs" / f"{name}.md"), prompt)
                self.assertIn("/autonomous", prompt)

    def test_mcp_inherit_drops_strict_for_that_peer_only(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [
                {"slug": "fixer", "ticket": "BRO-1", "mcp": "inherit"},
                {"slug": "reviewer", "ticket": "BRO-1"},
            ])
            _run(["up", str(roster), "--worktree", str(wt)])
            logs = argv_logs(td)
            self.assertNotIn("--strict-mcp-config", logs[0])
            self.assertIn("--strict-mcp-config", logs[1])

    def test_role_model_and_allowed_tools_reach_the_argv(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [
                {"slug": "reviewer", "ticket": "BRO-1", "role": "devils-advocate",
                 "model": "opus", "allowed_tools": "Read,Grep"},
            ])
            _run(["up", str(roster), "--worktree", str(wt)])
            argv = argv_logs(td)[0]
            self.assertIn("--agent", argv)
            self.assertEqual(argv[argv.index("--agent") + 1], "devils-advocate")
            self.assertEqual(argv[argv.index("--model") + 1], "opus")
            self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Grep")

    def test_failed_spawn_exits_1_and_keeps_a_truthful_state_file(self):
        with sandbox() as td:
            write_stub(td)
            (td / "spawn.fail").write_text("1")
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "fixer", "ticket": "BRO-1"}])
            rc, out = _run(["up", str(roster), "--worktree", str(wt)])
            self.assertEqual(rc, 1)
            self.assertIn("ERROR", out)
            state = read_state_json(only_fleet_dir(td))
            self.assertIsNone(state["peers"][0]["session_id"])   # never invented
            self.assertFalse(state["peers"][0]["spawned"])

    def test_validation_failure_creates_no_state(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"},
                                       {"slug": "a", "ticket": "BRO-1"}])
            rc, _ = _run(["up", str(roster), "--worktree", str(wt)])
            self.assertEqual(rc, 1)
            self.assertFalse(fleet.state_root().exists())
            self.assertEqual(argv_logs(td), [])

    def test_missing_binary_is_refused_before_any_state_is_written(self):
        with sandbox() as td:
            import os
            os.environ["BSTACK_FLEET_CLAUDE_BIN"] = str(td / "no-such-binary")
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            rc, _ = _run(["up", str(roster), "--worktree", str(wt)])
            self.assertEqual(rc, 1)
            self.assertFalse(fleet.state_root().exists())

    def test_json_output_carries_the_ids(self):
        import json
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            rc, out = _run(["up", str(roster), "--worktree", str(wt), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["peers"][0]["session_id"], "abc120")
            self.assertTrue(payload["fleet_id"].startswith("fleet_"))

    def test_explicit_fleet_id_is_honoured(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1"}])
            _run(["up", str(roster), "--worktree", str(wt),
                  "--fleet", "fleet_123_abcd"])
            self.assertTrue(
                (fleet.state_root() / "fleet_123_abcd" / "fleet.json").exists())

    def test_per_entry_worktree_is_the_spawn_cwd(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            other = plain_worktree(td, name="other")
            roster = write_roster(td, [
                {"slug": "a", "ticket": "BRO-1", "worktree": str(other)}])
            _run(["up", str(roster), "--worktree", str(wt)])
            state = read_state_json(only_fleet_dir(td))
            self.assertEqual(state["peers"][0]["worktree"], str(other))
            self.assertEqual(state["peers"][0]["name"], "other-bro-1-a")


class BriefWithoutLaneTest(unittest.TestCase):
    def test_no_owns_says_so_rather_than_leaving_a_blank(self):
        with sandbox() as td:
            write_stub(td)
            wt = plain_worktree(td, name="wt")
            roster = write_roster(td, [{"slug": "a", "ticket": "BRO-1",
                                        "prompt": "do the thing"}])
            _run(["up", str(roster), "--worktree", str(wt)])
            fd = only_fleet_dir(td)
            brief = (fd / "briefs" / "wt-bro-1-a.md").read_text()
            self.assertIn("no lane declared", brief)
            self.assertIn("do the thing", brief)


if __name__ == "__main__":
    unittest.main()
