"""Roster parsing + atomic validation.

Every rejection here has to happen BEFORE anything spawns: a fleet that
half-launches leaves the operator reconstructing which peers exist from the
agent listing alone.
"""
import io
import json
import unittest
from pathlib import Path

from scripts import fleet
from tests.fleet.helpers import init_repo, sandbox, write_roster


class RosterParseTest(unittest.TestCase):
    def test_jsonl_with_blank_lines_and_comments(self):
        with sandbox() as td:
            p = td / "r.jsonl"
            p.write_text(
                "# the fixer\n"
                '{"slug": "fixer", "prompt": "fix it"}\n'
                "\n"
                "   # indented comment\n"
                '{"slug": "reviewer", "prompt": "review it"}\n',
                encoding="utf-8")
            entries = fleet.parse_roster(str(p))
            self.assertEqual([e["slug"] for e in entries], ["fixer", "reviewer"])
            self.assertEqual(entries[0]["prompt"], "fix it")

    def test_single_json_array(self):
        with sandbox() as td:
            p = td / "r.json"
            p.write_text(json.dumps(
                [{"slug": "a"}, {"slug": "b"}]), encoding="utf-8")
            self.assertEqual([e["slug"] for e in fleet.parse_roster(str(p))],
                             ["a", "b"])

    def test_dash_reads_stdin(self):
        with sandbox():
            buf = io.StringIO('{"slug": "from-stdin"}\n')
            entries = fleet.parse_roster("-", stdin=buf)
            self.assertEqual(entries[0]["slug"], "from-stdin")

    def test_empty_roster_is_an_error(self):
        with sandbox() as td:
            p = td / "r.jsonl"
            p.write_text("# only a comment\n\n", encoding="utf-8")
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.parse_roster(str(p))
            self.assertIn("empty", str(ctx.exception))

    def test_missing_file_is_an_error(self):
        with sandbox() as td:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.parse_roster(str(td / "nope.jsonl"))
            self.assertIn("roster not found", str(ctx.exception))

    def test_malformed_line_names_the_line_number(self):
        with sandbox() as td:
            p = td / "r.jsonl"
            p.write_text('{"slug": "a"}\nnot json\n', encoding="utf-8")
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.parse_roster(str(p))
            self.assertIn(":2:", str(ctx.exception))


class RosterValidateTest(unittest.TestCase):
    def test_missing_slug_is_an_error(self):
        with sandbox() as td:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster([{"prompt": "x"}], worktree_flag=str(td))
            self.assertIn("slug is required", str(ctx.exception))

    def test_unknown_key_is_named(self):
        with sandbox() as td:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster([{"slug": "a", "promt": "typo"}],
                                      worktree_flag=str(td))
            msg = str(ctx.exception)
            self.assertIn("promt", msg)          # the offender, by name
            self.assertIn("allowed:", msg)

    def test_owns_must_be_a_list(self):
        with sandbox() as td:
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster([{"slug": "a", "owns": "scripts/*"}],
                                      worktree_flag=str(td))
            self.assertIn("owns must be a list", str(ctx.exception))

    def test_duplicate_composed_names_rejected(self):
        """Two entries whose ticket+slug collapse to one address. The peers
        would be indistinguishable to every `SendMessage` in the fleet."""
        with sandbox() as td:
            wt = td / "shared-wt"
            wt.mkdir()
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster(
                    [{"slug": "fix", "ticket": "BRO-1"},
                     {"slug": "fix", "ticket": "BRO-1"}],
                    worktree_flag=str(wt))
            self.assertIn("duplicate peer name", str(ctx.exception))

    def test_distinct_slugs_in_one_worktree_are_fine(self):
        with sandbox() as td:
            wt = td / "shared-wt"
            wt.mkdir()
            out = fleet.validate_roster(
                [{"slug": "fix", "ticket": "BRO-1"},
                 {"slug": "review", "ticket": "BRO-1"}],
                worktree_flag=str(wt))
            self.assertEqual([e["name"] for e in out],
                             ["shared-wt-bro-1-fix", "shared-wt-bro-1-review"])

    def test_more_than_one_worktree_is_rejected(self):
        """A fleet is N peers in ONE worktree. Two distinct worktrees is the
        wave shape; the error names the offending entry and points at wave."""
        with sandbox() as td:
            a = td / "wt-a"; a.mkdir()
            b = td / "wt-b"; b.mkdir()
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster(
                    [{"slug": "fix", "ticket": "BRO-1", "worktree": str(a)},
                     {"slug": "rev", "ticket": "BRO-1", "worktree": str(b)}])
            msg = str(ctx.exception)
            self.assertIn("rev", msg)                # the offender, by name
            self.assertIn("share ONE worktree", msg)
            self.assertIn("wave dispatch", msg)

    def test_one_worktree_across_many_peers_is_fine(self):
        with sandbox() as td:
            wt = td / "shared"; wt.mkdir()
            out = fleet.validate_roster(
                [{"slug": "fix", "ticket": "BRO-1", "worktree": str(wt)},
                 {"slug": "rev", "ticket": "BRO-1", "worktree": str(wt)}])
            self.assertEqual(len(out), 2)

    def test_duplicate_owns_glob_across_peers_rejected(self):
        with sandbox() as td:
            wt = td / "shared"; wt.mkdir()
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.validate_roster(
                    [{"slug": "fix", "ticket": "BRO-1", "owns": ["scripts/*.py"]},
                     {"slug": "rev", "ticket": "BRO-1", "owns": ["scripts/*.py"]}],
                    worktree_flag=str(wt))
            msg = str(ctx.exception)
            self.assertIn("scripts/*.py", msg)
            self.assertIn("same lane", msg)

    def test_distinct_owns_globs_are_fine(self):
        with sandbox() as td:
            wt = td / "shared"; wt.mkdir()
            out = fleet.validate_roster(
                [{"slug": "fix", "ticket": "BRO-1", "owns": ["scripts/*.py"]},
                 {"slug": "rev", "ticket": "BRO-1", "owns": ["tests/**"]}],
                worktree_flag=str(wt))
            self.assertEqual([sorted(e["owns"]) for e in out],
                             [["scripts/*.py"], ["tests/**"]])

    def test_bad_mcp_mode_rejected_before_launch(self):
        with sandbox() as td:
            with self.assertRaises(Exception) as ctx:
                fleet.validate_roster([{"slug": "a", "mcp": "loose"}],
                                      worktree_flag=str(td))
            self.assertIn("mcp mode", str(ctx.exception))


class NameCompositionTest(unittest.TestCase):
    def test_roster_ticket_is_used(self):
        with sandbox() as td:
            wt = td / "my-wt"
            wt.mkdir()
            out = fleet.validate_roster([{"slug": "triage", "ticket": "BRO-2454"}],
                                        worktree_flag=str(wt))
            self.assertEqual(out[0]["name"], "my-wt-bro-2454-triage")

    def test_ticket_pulled_from_the_branch_name(self):
        with sandbox() as td:
            repo = init_repo(td, name="wt", branch="feature/bro-2454-fleet")
            out = fleet.validate_roster([{"slug": "triage"}],
                                        worktree_flag=str(repo))
            self.assertEqual(out[0]["ticket"], "bro-2454")
            self.assertEqual(out[0]["name"], "wt-bro-2454-triage")

    def test_no_ticket_anywhere_yields_a_ticketless_name(self):
        with sandbox() as td:
            repo = init_repo(td, name="wt", branch="main")
            out = fleet.validate_roster([{"slug": "triage"}],
                                        worktree_flag=str(repo))
            self.assertIsNone(out[0]["ticket"])
            self.assertEqual(out[0]["name"], "wt-triage")

    def test_ticket_pattern_is_configurable(self):
        """A workspace whose tickets are `#1234` declares it once; the default
        `[A-Za-z]+-\\d+` would find nothing in that branch name."""
        with sandbox() as td:
            repo = init_repo(td, name="wt", branch="fix/1234-thing")
            out = fleet.validate_roster([{"slug": "triage"}],
                                        worktree_flag=str(repo),
                                        ticket_pattern=r"\d{4}")
            self.assertEqual(out[0]["ticket"], "1234")

    def test_default_worktree_is_the_current_one(self):
        with sandbox() as td:
            repo = init_repo(td, name="wt", branch="main")
            sub = repo / "deep" / "nested"
            sub.mkdir(parents=True)
            resolved = fleet._current_worktree(sub)
            self.assertEqual(Path(resolved).resolve(), repo.resolve())

    def test_per_entry_worktree_overrides_the_default(self):
        with sandbox() as td:
            other = td / "other-wt"
            other.mkdir()
            out = fleet.validate_roster(
                [{"slug": "a", "ticket": "BRO-1", "worktree": str(other)}],
                worktree_flag=str(td / "default-wt"))
            self.assertEqual(out[0]["worktree"], str(other))
            self.assertEqual(out[0]["name"], "other-wt-bro-1-a")


class RosterFileRoundTripTest(unittest.TestCase):
    def test_write_roster_helper_parses_back(self):
        with sandbox() as td:
            p = write_roster(td, [{"slug": "a"}, {"slug": "b"}])
            self.assertEqual(len(fleet.parse_roster(str(p))), 2)


if __name__ == "__main__":
    unittest.main()
